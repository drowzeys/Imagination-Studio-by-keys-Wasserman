"""Character Swap (app/swap.py, spark/spark_swap.py, spark/swap_plan.py, app/static/swap.js): the settings a swap takes and
refuses, the windows a part is made in, the graph each window sends to ComfyUI (models, LoRAs, settings, the mask and
the history), the stages the job card shows, the MCP tool and the Library panel. Nothing is rendered: ComfyUI and the
studio around the action are stood in for.
Run: python3 -m pytest tests/test_swap.py."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import swap  # noqa: E402
import swap_plan as plan  # noqa: E402

spec = importlib.util.spec_from_file_location("spark_swap", ROOT / "spark/spark_swap.py")
spark_swap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark_swap)


# ---------------------------------------------------------------- the windows

def test_the_model_grid_and_its_latent_frames():
    assert [plan.grid_up(n) for n in (1, 5, 6, 21, 22, 23, 117, 120, 124, 125)] == [5, 5, 22, 22, 22, 39, 124, 124, 124, 141]
    assert plan.latent_frames(124) == 37 and plan.latent_frames(22) == 7 and plan.latent_frames(5) == 2
    spans = plan.latent_spans(22)
    assert spans[:7] == [(0, 0), (1, 4), (5, 8), (9, 12), (13, 16), (17, 17), (18, 21)]
    assert plan.held_latents(18) == 6 and plan.held_latents(0) == 0  # 17 frames before the join and the join: 6 whole latents
    assert sum(b - a + 1 for a, b in plan.latent_spans(141)) == 141
    with pytest.raises(ValueError):
        plan.latent_frames(120)


@pytest.mark.parametrize("seconds,expected", [
    (5, [(0, 124, 0, 4, 0, 119)]),
    (9, [(0, 124, 0, 0, 0, 123), (106, 124, 18, 14, 124, 215)]),
    (10, [(0, 124, 0, 0, 0, 123), (106, 124, 18, 0, 124, 229), (212, 39, 18, 11, 230, 239)]),
    (15, [(0, 124, 0, 0, 0, 123), (106, 124, 18, 0, 124, 229), (212, 124, 18, 0, 230, 335), (318, 56, 18, 14, 336, 359)]),
])
def test_windows_of_124_frames_with_18_of_history_for_5_10_and_15_seconds(seconds, expected):
    frames = plan.part_frames(seconds)
    made = plan.windows(frames, 124, 18)
    assert [(w["start"], w["length"], w["held"], w["pad"], w["first"], w["last"]) for w in made] == expected
    assert all(plan.on_grid(w["length"]) and w["of"] == len(made) for w in made)
    assert all(w["length"] <= 124 for w in made)  # the history counted: no window is longer than the 124 measured
    covered = [f for w in made for f in range(w["first"], w["last"] + 1)]
    assert covered == list(range(frames))  # every frame of the part made once, in order
    for before, after in zip(made, made[1:]):  # each later window starts 17 frames before the last one delivered
        assert after["start"] == before["last"] - 17 and after["skip"] == 18
        assert after["start"] + after["skip"] == after["first"]


def test_the_example_of_a_six_second_part():
    # 144 frames: the second window is 22 frames drawn after 17 carried, 39 in all, with one frame past the end
    second = plan.windows(144)[1]
    assert (second["start"], second["length"], second["pad"], second["first"], second["last"]) == (106, 39, 1, 124, 143)


def test_every_part_from_5_to_15_seconds_is_made_in_windows_of_at_most_124_frames():
    for frames in range(120, 361):
        made = plan.windows(frames)
        assert all(plan.on_grid(w["length"]) and w["length"] <= plan.WINDOW for w in made), frames
        assert [f for w in made for f in range(w["first"], w["last"] + 1)] == list(range(frames)), frames
        assert all(w["length"] == plan.WINDOW for w in made[:-1]), frames  # only the last is shorter
    assert len(plan.windows(230)) == 2 and len(plan.windows(231)) == 3  # 124, then 106 new frames a window


def test_the_canvas_keeps_the_clip_shape_on_32():
    assert plan.canvas(1376, 768, 768) == (1344, 768)
    assert plan.canvas(1920, 1080, 768) == (1344, 768)
    assert plan.canvas(1376, 768, 480) == (864, 480)
    assert plan.canvas(1080, 1920, 480) == (480, 864)
    w, h = plan.canvas(1000, 1000, 768)
    assert w * h <= plan.MAX_PIXELS and w % 32 == 0 and h % 32 == 0
    with pytest.raises(ValueError):
        plan.canvas(1376, 768, 600)


# ---------------------------------------------------------------- the action's settings

def stand_in(tmp_path, spark=True, length=8.0, installed=True):
    home = tmp_path / "home"
    for name in ("renders", "uploads", "scouts", "masks"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / "uploads/clip.mp4").write_bytes(b"clip")
    (home / "uploads/hero.png").write_bytes(b"png")
    models, prefix = tmp_path / "models", tmp_path / "prefix"
    if installed:
        for one in (swap.SAM, swap.REF2VA[0], swap.TURBO, swap.SWAP_LORA):
            (models / one).parent.mkdir(parents=True, exist_ok=True)
            (models / one).write_bytes(b"x")
        (prefix / "venv/bin").mkdir(parents=True)
        (prefix / "venv/bin/python").write_text("")
        (prefix / "ComfyUI").mkdir(parents=True)
        (prefix / "ComfyUI/main.py").write_text("")
    os.environ["SPARK_MODELS"], os.environ["PREFIX"] = str(models), str(prefix)

    def inside(path):
        found = (home / str(path)).resolve()
        if not str(found).startswith(str(home.resolve())):
            raise HTTPException(400, "outside the studio folder")
        return found

    router, build, title, source, guess = swap.attach(home, ROOT, spark, inside, lambda p: str(Path(p).relative_to(home.resolve())),
                                                      lambda path: (length, True))
    return SimpleNamespace(home=home, router=router, build=build, title=title, source=source, guess=guess, models=models, prefix=prefix)


IDLE_SPARK = {"MemTotal": 124_500, "MemAvailable": 118_000, "MemFree": 118_000}  # MB: an idle DGX Spark


@pytest.fixture(autouse=True)
def keep_environment(monkeypatch):
    """The environment put back after each test, and the memory guard seeing an idle DGX Spark, whatever the machine
    the tests run on has (a CI runner has 16 GB)."""

    import memory_guard

    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: dict(IDLE_SPARK))
    monkeypatch.setattr(memory_guard, "device", lambda fresh=False: None)
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    monkeypatch.delenv("STUDIO_ROOM_MB", raising=False)
    saved = {k: os.environ.get(k) for k in ("SPARK_MODELS", "PREFIX")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


GOOD = {"video": "uploads/clip.mp4", "image": "uploads/hero.png", "start": 1, "seconds": 5, "quality": 768}


def test_a_swap_is_built_with_the_parts_settings_and_its_outputs(tmp_path):
    s = stand_in(tmp_path)
    command, env, outputs = s.build("swap", {**GOOD, "direction": "  she keeps   her hands in her pockets ", "seed": 7}, "j1")
    joined = " ".join(command)
    assert command[0] == str(s.prefix / "venv/bin/python") and command[1].endswith("spark/spark_swap.py")
    for words in ("--start 1", "--seconds 5", "--quality 768", "--seed 7", "--direction=she keeps her hands in her pockets"):
        assert words in joined
    assert command[command.index("--swap-lora") + 1].endswith("h3_character_swap_v1.5.safetensors")
    assert command[command.index("--turbo-lora") + 1].endswith("minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
    assert "--no-anchor" not in command and "--anchor-out" in command
    assert outputs == [str(s.home / "renders/j1.mp4"), str(s.home / "masks/j1/person.mp4"), str(s.home / "scouts/j1/anchor.png")]
    assert env["SPARK_MODELS"] == str(s.models)
    command, _, outputs = s.build("swap", {**GOOD, "anchor": False, "quality": 480}, "j2")
    assert "--no-anchor" in command and "--anchor-out" not in command and len(outputs) == 2
    assert command[command.index("--seed") + 1] == str(swap.SEED)  # Akatz Labs' own seed when none is given
    assert s.title({"video": "uploads/clip.mp4"}) == "Character Swap: clip.mp4" and s.source({"video": "uploads/clip.mp4"}) == "uploads/clip.mp4"
    # nothing is made when the job is built (a full queue may still refuse it): the job makes its folders when it runs
    assert not (s.home / "masks/j1").exists() and not (s.home / "scouts/j1").exists()


def test_a_direction_that_starts_with_a_dash_reaches_the_script_as_the_direction(tmp_path, monkeypatch):
    import argparse
    import signal

    s = stand_in(tmp_path)
    command, _, _ = s.build("swap", {**GOOD, "direction": "-- no hat, -x"}, "j3")
    read, real = {}, argparse.ArgumentParser.parse_args

    class Read(Exception):
        pass

    def parse(self, args=None, namespace=None):
        read["args"] = real(self, args, namespace)
        raise Read

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse)
    monkeypatch.setattr(sys, "argv", command[1:])
    before = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(Read):
            spark_swap.main()  # stopped once spark_swap.py's own parser has read the command
    finally:
        signal.signal(signal.SIGTERM, before)
    assert read["args"].direction == "-- no hat, -x" and read["args"].seed == 904239


@pytest.mark.parametrize("change,said", [
    ({"video": None}, "choose a clip"),
    ({"video": "uploads/hero.png"}, "is not a clip"),
    ({"image": None}, "choose the character's picture"),
    ({"image": "uploads/clip.mp4"}, "is not the character's picture"),
    ({"seconds": 4}, "5 to 15 seconds"),
    ({"seconds": 16}, "5 to 15 seconds"),
    ({"seconds": "long"}, "seconds is a number"),
    ({"start": -1}, "0 s or later"),
    ({"start": 4}, "runs past its end"),
    ({"quality": 600}, "480 or 768"),
    ({"anchor": "yes"}, "true or false"),
    ({"direction": "x" * 501}, "at most 500"),
    ({"seed": -2}, "whole number"),
    ({"seed": True}, "seed is a number"),
    ({"start": "nan"}, "start is a number"),
    ({"start": float("inf")}, "start is a number"),
    ({"seconds": "nan"}, "seconds is a number"),
    ({"seconds": "-inf"}, "seconds is a number"),
    ({"seed": float("inf")}, "seed is a number"),
    ({"quality": "nan"}, "quality is a number"),
])
def test_what_a_swap_refuses_before_it_is_queued(tmp_path, change, said):
    s = stand_in(tmp_path)
    with pytest.raises(HTTPException) as refused:
        s.build("swap", {**GOOD, **change}, "j1")
    assert refused.value.status_code == 400 and said in refused.value.detail


def test_where_it_cannot_run_it_says_what_to_install(tmp_path):
    mac = stand_in(tmp_path / "mac", spark=False)
    with pytest.raises(HTTPException) as refused:
        mac.build("swap", GOOD, "j1")
    assert "not on a Mac" in refused.value.detail
    short = stand_in(tmp_path / "mac-short", spark=False, length=3.0)  # the Mac's reason first, not the clip's length
    with pytest.raises(HTTPException) as refused:
        short.build("swap", GOOD, "j1")
    assert "not on a Mac" in refused.value.detail and "long" not in refused.value.detail
    bare = stand_in(tmp_path / "bare", installed=False)
    with pytest.raises(HTTPException) as refused:
        bare.build("swap", GOOD, "j1")
    assert "oneshot-setup-spark.sh --swap" in refused.value.detail
    import sparks
    had = sparks.card_face
    try:  # on a workstation's card the hint names that machine's installer
        sparks.card_face = lambda: True
        with pytest.raises(HTTPException) as refused:
            bare.build("swap", GOOD, "j1")
        assert "oneshot-setup-rtx-6000.sh --swap" in refused.value.detail
    finally:
        sparks.card_face = had
    s = stand_in(tmp_path / "some")
    (s.models / swap.SWAP_LORA).unlink()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(s.router)
    state = TestClient(app).get("/api/swap").json()
    assert state["ready"] is False and "Character Swap LoRA (version 1.5)" in state["why"]
    assert state["seconds"] == [5.0, 15.0] and state["qualities"] == [480, 768] and state["anchor"] is True and state["quality"] == 480
    assert state["estimates"]["480"]["5"] == swap.estimate(5, 480) and set(state["estimates"]["768"]) == {str(n) for n in range(5, 16)}
    assert state["window"] == 124 and state["history"] == 18 and "not licensed for use in the EU, the UK, South Korea or the US" in state["licence"]
    assert state["anchor_model"] == "qwen"  # FLUX3-K not installed here: the anchor falls back to Qwen-Image-2.1


def test_the_time_a_swap_takes_follows_the_measured_stages(tmp_path):
    # measured on a DGX Spark: a 5 s part at 480 with the anchor, 341 s; an 8 s part at 768 in two windows, 1220 s
    assert 300 <= swap.estimate(5, 480) <= 360 and 1100 <= swap.estimate(8, 768) <= 1300
    assert swap.estimate(5, 480, anchor=False) == swap.estimate(5, 480) - round(swap.ANCHOR[480])
    assert swap.estimate(5, 480) < swap.estimate(10, 480) < swap.estimate(15, 480) and swap.estimate(5, 768) > 2 * swap.estimate(5, 480)
    s = stand_in(tmp_path)
    assert s.guess({"seconds": 5}) == swap.estimate(5, 480) and s.guess({"seconds": 5, "quality": 768, "anchor": False}) == swap.estimate(5, 768, False)
    assert s.guess({"quality": 600}) is None and s.guess({"seconds": "x"}) is None


# ---------------------------------------------------------------- the graph a window sends

class FakeComfy:
    REF2VA = {"bf16": "minimax_h3_ref2va_pruned_bf16.safetensors", "int8": "minimax_h3_ref2va_pruned_int8_convrot.safetensors"}
    H3_TEXT, H3_VAE, H3_AUDIO_VAE = "text.safetensors", "video_vae.safetensors", "audio_vae.safetensors"

    def __init__(self):
        self.UPLOADED = []

    def upload(self, path):
        return "up_" + Path(path).name


def graph(monkeypatch, index=1, anchor=True, sound=True, direction=""):
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap, "put", lambda path: "in_" + Path(path).name)
    one = plan.windows(240)[index - 1]
    files = {"pixels": Path("w/pixels.mp4"), "ref": Path("w/pixels.mp4" if index == 1 else "w/ref.mp4"), "mask": Path("w/mask.mp4"),
             "sound": Path("w/sound.wav") if sound else None, "anchor": Path("w/anchor.png") if anchor and index == 1 else None}
    part = {"width": 1344, "height": 768}
    args = SimpleNamespace(seed=904239, direction=direction)
    loras = [("hub_turbo.safetensors", 1.0), ("hub_swap.safetensors", 1.0)]
    return spark_swap.window_graph(one, files, part, args, FakeComfy.REF2VA["int8"], loras, "up_hero.png"), one


def test_a_window_runs_ref2va_int8_with_both_loras_and_akatz_labs_settings(monkeypatch):
    w, one = graph(monkeypatch)
    kinds = {node["class_type"] for node in w.values()}
    assert w["1"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    assert w["2"]["class_type"] == "MiniMaxH3SigmaShift" and (w["2"]["inputs"]["shift_video"], w["2"]["inputs"]["shift_audio"]) == (12.0, 3.0)
    assert [w[n]["inputs"]["lora_name"] for n in ("3", "4")] == ["hub_turbo.safetensors", "hub_swap.safetensors"]
    assert w["3"]["inputs"]["model"] == ["2", 0] and w["4"]["inputs"]["model"] == ["3", 0] and w["3"]["inputs"]["strength_model"] == 1.0
    assert w["31"]["inputs"]["sampler_name"] == "er_sde"
    assert w["32"]["inputs"] == {"model": ["4", 0], "scheduler": "normal", "steps": 8, "denoise": 1.0}
    assert w["33"]["class_type"] == "BasicGuider" and w["33"]["inputs"]["model"] == ["4", 0]  # CFG 1: one forward a pass
    assert w["30"]["inputs"]["noise_seed"] == 904239
    ref = w["20"]["inputs"]
    assert ref["prompt"].startswith("Replace the selected person in <Video 1> with the character in <Picture 1>. Preserve source performance, scene and audio timing.")
    assert "Start from the first frame in <Picture 2>." in ref["prompt"]
    assert (ref["width"], ref["height"], ref["length"]) == (1344, 768, 124)
    assert ref["ref_images.ref_image_0"] == ["10", 0] and ref["ref_images.ref_image_1"] == ["19", 0] and ref["ref_videos.ref_video_0"] == ["12", 0]
    assert ref["ref_audios.ref_audio_0"] == ["13", 0]
    # the person's mask on the latent grid keeps the rest of each frame, joined to the reference's sound latent
    assert w["22"]["class_type"] == "SetLatentNoiseMask" and w["22"]["inputs"]["mask"] == ["18", 0]
    assert w["24"]["class_type"] == "LTXVConcatAVLatent" and w["34"]["inputs"]["latent_image"] == ["24", 0]
    # the sound guides the window from its first frame, the anchor is the first frame's guide
    assert w["25"]["inputs"]["audio"] == ["13", 0] and w["25"]["inputs"]["frame_idx"] == 0
    assert w["26"]["inputs"]["image"] == ["19", 0] and w["26"]["inputs"]["frame_idx"] == 0 and w["33"]["inputs"]["conditioning"] == ["26", 0]
    assert w["15"]["inputs"]["video"] == ["11", 0]  # window 1 starts from the part itself: one file for both
    assert "SAM3_Detect" not in kinds and not any(k.startswith(("H3Relay", "H3CharacterSwap")) for k in kinds)  # ComfyUI's own nodes only


def test_a_later_window_carries_the_history_and_not_the_anchor(monkeypatch):
    w, one = graph(monkeypatch, index=2, direction="the cape moves in the wind")
    assert one["held"] == 18 and one["length"] == 124
    assert "<Picture 2>" not in w["20"]["inputs"]["prompt"] and w["20"]["inputs"]["prompt"].endswith("the cape moves in the wind")
    assert "ref_images.ref_image_1" not in w["20"]["inputs"] and "26" not in w
    assert w["14"]["inputs"]["file"] == "in_pixels.mp4" and w["15"]["inputs"]["video"] == ["14", 0] and w["11"]["inputs"]["file"] == "in_ref.mp4"
    assert w["30"]["inputs"]["noise_seed"] == 904240  # the master seed, plus one a window
    silent, _ = graph(monkeypatch, sound=False, anchor=False)
    assert "13" not in silent and "25" not in silent and silent["33"]["inputs"]["conditioning"] == ["20", 0]


def test_on_tensorfolds_engine_the_blocks_run_int8_dense_with_the_loras_merged(monkeypatch):
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap, "put", lambda path: "in_" + Path(path).name)
    one = plan.windows(120)[0]
    files = {"pixels": Path("w/pixels.mp4"), "ref": Path("w/ref.mp4"), "mask": Path("w/mask.mp4"), "sound": None, "anchor": None}
    w = spark_swap.window_graph(one, files, {"width": 864, "height": 480}, SimpleNamespace(seed=1, direction=""),
                                FakeComfy.REF2VA["int8"], [("t.safetensors", 1.0), ("s.safetensors", 1.0)], "c.png",
                                blocks=FakeComfy.REF2VA["bf16"])
    assert w["8"] == {"class_type": "TensorFoldH3Blocks", "inputs": {"model": ["4", 0], "unet_name": "minimax_h3_ref2va_pruned_bf16.safetensors", "sparsity": 0.0}}
    assert w["32"]["inputs"]["model"] == ["8", 0] and w["33"]["inputs"]["model"] == ["8", 0]  # the same passes, sampler and schedule
    assert w["32"]["inputs"]["steps"] == 8 and w["31"]["inputs"]["sampler_name"] == "er_sde"
    assert w["15"]["inputs"]["video"] == ["14", 0]  # <Video 1> is the smaller copy: the frames start from the part's own


def test_the_stages_are_said_for_the_job_card(capsys):
    stages = spark_swap.Stages(["the part", "the person", "window 1 of 2"])
    stages.next("cutting the part out")
    stages.next("finding the person")
    stages.done()
    said = capsys.readouterr().out.splitlines()
    assert said == ["[tensorfold] stage 1/3 cutting the part out", "[tensorfold] stage 2/3 finding the person"]
    assert [name for name, _ in stages.took] == ["the part", "the person"]


def test_a_first_frame_with_nobody_in_it_is_refused(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    graphs = []
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap, "run_graph", lambda w, sampler, named=None: graphs.append(sorted(w)) or "p1")
    frame = tmp_path / "f.png"
    Image.new("RGB", (64, 32)).save(frame)
    part = {"frames": [frame], "width": 64, "height": 32}
    # SAM 3.1 found nothing (a flat colour): refused in words, and the nodes that cannot take an empty mask never run
    monkeypatch.setattr(spark_swap, "said_text", lambda prompt, node: "[\n    []\n]")
    with pytest.raises(SystemExit) as refused:
        spark_swap.find_person(part, tmp_path)
    assert "no person was found in the part's first frame" in str(refused.value) and "out of bounds" not in str(refused.value)
    assert graphs == [["1", "2", "3", "4", "7"]]
    # something it is not sure is a person (a gradient, 48%): refused, with how sure it was
    monkeypatch.setattr(spark_swap, "said_text", lambda prompt, node: json.dumps([[{"x": 0, "y": 0, "width": 64, "height": 32, "score": 0.484}]]))
    with pytest.raises(SystemExit) as refused:
        spark_swap.find_person(part, tmp_path)
    assert "no person was found" in str(refused.value) and "48% sure" in str(refused.value) and len(graphs) == 2
    # a person: the masks are made, and the largest person's is the one chosen
    masks = []
    for n, (left, right) in enumerate(((0, 8), (20, 50))):
        mask = Image.new("L", (64, 32))
        mask.paste(255, (left, 2, right, 30))
        masks.append(tmp_path / f"m{n}.png")
        mask.save(masks[-1])
    boxes = [{"x": 0, "y": 2, "width": 8, "height": 28, "score": 0.97}, {"x": 20, "y": 2, "width": 30, "height": 28, "score": 0.95}]
    monkeypatch.setattr(spark_swap, "said_text", lambda prompt, node: json.dumps([boxes]))
    monkeypatch.setattr(spark_swap, "collect", lambda prompt, node: [{}, {}])
    monkeypatch.setattr(spark_swap, "fetch_all", lambda items, folder: masks)
    chosen = spark_swap.find_person(part, tmp_path)
    assert graphs[-1] == ["1", "2", "3", "4", "5", "6", "7"]
    assert (__import__("numpy").asarray(Image.open(chosen)) > 0).sum() == 30 * 28


@pytest.mark.parametrize("name,box,area,person", [
    # SAM 3.1 on synthetic 864x480 frames (2026-10-27): what it found, how sure, its box and its mask
    ("an empty kitchen", (95, 18), 1161, False), ("a coat on a rack", (221, 467), 54205, False),
    ("an ffmpeg gradient", (856, 476), 413900, False), ("a Mandelbrot", (396, 358), 50512, False),
    ("one flat colour", (857, 477), 409600, False), ("an empty street", (8, 17), 124, False),
    ("a close-up", (850, 478), 247900, True), ("from behind", (370, 449), 108700, True),
    ("sitting", (275, 404), 35700, True), ("a waiter at the back of a cafe", (62, 170), 5970, True),
    ("a hiker far away", (20, 38), 332, True),
])
def test_what_counts_as_a_person_in_the_first_frame(name, box, area, person):
    scores = {"an empty kitchen": 0.644, "a coat on a rack": 0.667, "an ffmpeg gradient": 0.484, "a Mandelbrot": 0.404,
              "one flat colour": 0.312, "an empty street": 0.507, "a close-up": 0.981, "from behind": 0.991, "sitting": 0.983,
              "a waiter at the back of a cafe": 0.956, "a hiker far away": 0.931}
    found = [{"x": 0, "y": 0, "width": box[0], "height": box[1], "score": scores[name]}]
    assert bool(spark_swap.people(found, [area], 864, 480)) is person, name
    if name in ("an ffmpeg gradient", "one flat colour"):  # however sure, a rectangle over most of the frame is not a person
        assert not spark_swap.people([{**found[0], "score": 0.99}], [area], 864, 480)


def test_the_largest_person_comes_first():
    found = [{"width": 50, "height": 100, "score": 0.99}, {"width": 100, "height": 200, "score": 0.9}, {"width": 400, "height": 400, "score": 0.5}]
    assert [i for i, _, _ in spark_swap.people(found, [3000, 12000, 90000], 864, 480)] == [1, 0]


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")


@needs_ffmpeg
def test_a_part_past_the_clips_end_or_out_of_range_is_refused(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=6", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    for start, seconds, said in ((2, 5, "runs past its end"), (0, 4, "5 to 15 seconds"), (-1, 5, "0 s or later")):
        with pytest.raises(SystemExit) as refused:
            spark_swap.prepare(SimpleNamespace(video=str(clip), start=start, seconds=seconds, quality=480), tmp_path / "w")
        assert said in str(refused.value)


CANCELLED = r"""
import sys, time, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import spark_swap
spark_swap.sg = types.SimpleNamespace(UPLOADED=[], PREFIX=Path("/nowhere"))

def prepare(args, work):  # the part cut out, a picture handed to ComfyUI, then a long wait
    (work / "part").mkdir()
    (work / "part" / "00001.png").write_bytes(b"x" * 1000)
    spark_swap.put(Path(args.image))
    print("READY", flush=True)
    time.sleep(60)

spark_swap.prepare = prepare
sys.argv = ["spark_swap.py", *sys.argv[2:]]
spark_swap.main()
"""


def test_a_cancelled_swap_leaves_no_frames_and_no_pictures_in_comfyui(tmp_path):
    import signal

    for name in ("clip.mp4", "hero.png", "swap.safetensors", "turbo.safetensors"):
        (tmp_path / name).write_bytes(b"x")
    work, given = tmp_path / "renders" / ".swap-j1", tmp_path / "input"
    script = tmp_path / "cancelled.py"
    script.write_text(CANCELLED)
    process = subprocess.Popen([sys.executable, str(script), str(ROOT / "spark"), "--video", str(tmp_path / "clip.mp4"),
                                "--image", str(tmp_path / "hero.png"), "--swap-lora", str(tmp_path / "swap.safetensors"),
                                "--turbo-lora", str(tmp_path / "turbo.safetensors"), "--work", str(work), "-o", str(tmp_path / "renders/j1.mp4")],
                               stdout=subprocess.PIPE, text=True, env={**os.environ, "SPARK_COMFY_INPUT": str(given)})
    try:
        while "READY" not in process.stdout.readline():
            assert process.poll() is None, "the stand-in ended before it was ready"
        assert work.is_dir() and [f.name for f in given.iterdir()][0].startswith("studio_")
        process.send_signal(signal.SIGTERM)  # what server.cancel sends
        assert process.wait(20) == 143
    finally:
        if process.poll() is None:
            process.kill()
    assert not work.exists() and list(given.iterdir()) == []


def test_a_swap_s_work_folder_is_removed_however_the_job_ended(tmp_path):
    os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
    import server

    left = swap.work_folder(server.HOME, "0101-000000-tidy01")
    (left / "window1").mkdir(parents=True)
    (left / "window1" / "00001.png").write_bytes(b"x")
    job = {"id": "0101-000000-tidy01", "kind": "swap", "status": "running", "params": {"image": "uploads/x.png"}, "log": "",
           "outputs": [], "progress": None, "error": None}
    server.perform(job)  # refused when its turn comes (no clip): the job fails, and its folder goes with it
    assert job["status"] == "failed" and not left.exists()


def test_a_swap_that_made_no_clip_leaves_no_mask_preview_and_no_anchor(tmp_path):
    for job_id in ("0101-000000-gone01", "0101-000000-kept01"):
        (tmp_path / "masks" / job_id).mkdir(parents=True)
        (tmp_path / "masks" / job_id / "person.mp4").write_bytes(b"x")
        (tmp_path / "scouts" / job_id).mkdir(parents=True)
        (tmp_path / "scouts" / job_id / "anchor.png").write_bytes(b"x")
        swap.work_folder(tmp_path, job_id).mkdir(parents=True)
    (tmp_path / "renders" / "0101-000000-kept01.mp4").write_bytes(b"x")  # this one finished
    (tmp_path / "scouts" / "0101-000000-other1").mkdir()  # another job's pictures are not touched
    swap.tidy(tmp_path, "0101-000000-gone01")  # cancelled while it ran: the mask and the anchor were made already
    swap.tidy(tmp_path, "0101-000000-kept01")
    assert not (tmp_path / "masks" / "0101-000000-gone01").exists() and not (tmp_path / "scouts" / "0101-000000-gone01").exists()
    assert (tmp_path / "masks" / "0101-000000-kept01" / "person.mp4").is_file()
    assert (tmp_path / "scouts" / "0101-000000-kept01" / "anchor.png").is_file()
    assert (tmp_path / "scouts" / "0101-000000-other1").is_dir()
    assert not swap.work_folder(tmp_path, "0101-000000-gone01").exists() and not swap.work_folder(tmp_path, "0101-000000-kept01").exists()


# ---------------------------------------------------------------- the studio around it

def test_the_studio_takes_a_swap_job_and_shows_its_stages(tmp_path):
    os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
    import server
    from fastapi.testclient import TestClient

    assert "swap" in server.swap.KINDS
    said = TestClient(server.app).post("/api/jobs", json={"kind": "swap", "params": {"image": "uploads/x.png"}})
    # no "a prompt is needed": the swap has none; a Mac is told it has no Character Swap before anything about the clip
    assert said.status_code == 400 and ("not on a Mac" if sys.platform == "darwin" else "choose a clip") in said.json()["error"]
    def progress(*lines):
        job = {"id": "t1", "log": "", "status": "running", "progress": None}
        code = "; ".join(f"print({line!r}, flush=True)" for line in lines)
        assert server.run(job, [sys.executable, "-c", code], {})
        return job["progress"]

    assert progress("[tensorfold] stage 5/7 window 2 of 3") == {"stage": "window 2 of 3", "at": 5, "of": 7}
    assert progress("[tensorfold] stage 5/7 window 2 of 3", "[tensorfold] step 3/8") == {"stage": "window 2 of 3 · picture", "at": 3, "of": 8}
    assert progress("[tensorfold] stage 2/7 following the person", "[tensorfold] frame 40/192") == {"stage": "following the person · frame", "at": 40, "of": 192}

    # a refusal reads as the script wrote it; the exit status only where the script said nothing
    def failure(code):
        job = {"id": "t2", "log": "", "status": "running", "progress": None}
        try:
            server.run(job, [sys.executable, "-c", code], {})
        except RuntimeError as error:
            return str(error)
    assert failure("print('no one stands in this part of the clip'); raise SystemExit(1)") == "no one stands in this part of the clip"
    assert failure("raise SystemExit(4)") == "the script stopped (status 4)"


def test_the_agents_swap_a_character_through_the_studio_tools(monkeypatch):
    spec = importlib.util.spec_from_file_location("studio_mcp_swap", ROOT / "mcp/studio-mcp.py")
    mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp)
    sent = []
    monkeypatch.setattr(mcp, "call", lambda method, path, body=None, **_: sent.append((method, path, body)) or
                        {"id": "j1", "kind": "swap", "status": "queued", "outputs": []})
    listed = {t["name"]: t for t in mcp.answer({"method": "tools/list"})["tools"]}
    assert listed["swap_character"]["inputSchema"]["required"] == ["clip"]  # a job that numbers the people, or `swaps`, has no one picture
    mcp.answer({"method": "tools/call", "params": {"name": "swap_character", "arguments": {"clip": "renders/a.mp4", "swaps": [
        {"picture": "scouts/b/hero.png", "which": 2}, {"picture": "scouts/b/villain.png", "person": "man in a blue jacket"}]}}})
    assert sent[-1][2] == {"kind": "swap", "params": {"video": "renders/a.mp4", "swaps": [
        {"image": "scouts/b/hero.png", "which": 2}, {"image": "scouts/b/villain.png", "person": "man in a blue jacket"}]}}
    mcp.answer({"method": "tools/call", "params": {"name": "swap_character", "arguments": {"clip": "renders/a.mp4", "who": True, "quality": 480}}})
    assert sent[-1][2] == {"kind": "swap", "params": {"video": "renders/a.mp4", "quality": 480, "who": True}}
    assert set(listed["swap_character"]["inputSchema"]["properties"]) >= {"clip", "picture", "start", "seconds", "quality", "direction"}
    reply = mcp.answer({"method": "tools/call", "params": {"name": "swap_character", "arguments": {
        "clip": "renders/a.mp4", "picture": "scouts/b/hero.png", "start": 2, "seconds": 10, "quality": 480, "direction": ""}}})
    assert not reply.get("isError"), reply
    assert sent[-1] == ("POST", "/api/jobs", {"kind": "swap", "params": {"video": "renders/a.mp4", "image": "scouts/b/hero.png",
                                                                          "start": 2, "seconds": 10, "quality": 480}})
    quality = listed["swap_character"]["inputSchema"]["properties"]["quality"]["description"]
    assert quality.startswith(f"the short side, {swap.QUALITY} (default") and "title" in listed["swap_character"]["inputSchema"]["properties"]
    mcp.answer({"method": "tools/call", "params": {"name": "swap_character", "arguments": {"clip": "renders/a.mp4", "picture": "scouts/b/hero.png",
                                                                                        "title": "Hero swap"}}})
    assert sent[-1][2]["title"] == "Hero swap"
    import agents
    assert "swap_character" in agents.MAKERS and "swap_character" in agents.STUDIO_TOOLS


FAKE_GIT = """#!/bin/bash
# the node packs already at their pinned commits: `git -C <dir> rev-parse HEAD` answers the commit setup-workflows.sh wants
case "$(basename "$2")" in
  ComfyUI-H3-ContactSheet) echo 3c8c8c66e69362e5abf9cf4c4e693baddab90ca3;;
  ComfyUI-H3-Video-Upsampler) echo 36cb612ec3df30094eeb4fec66f1528925ebba73;;
  h3-relay) echo cf489b7f2472fdf33866fae36de49aea03ef464f;;
esac
"""
# a download stood in for: curl writes the file asked for, and its checksum is taken as right
FAKE_CURL = """#!/bin/bash
while [ $# -gt 0 ]; do [ "$1" = -o ] && { echo weights > "$2"; shift; }; shift; done
"""


def setup_machine(tmp_path, comfy=True):
    """A Spark with ComfyUI and every model the two setups fetch already there; git and curl stood in for."""

    prefix, models, tools = tmp_path / "prefix", tmp_path / "models", tmp_path / "bin"
    tools.mkdir(parents=True)
    for name, text in (("git", FAKE_GIT), ("curl", FAKE_CURL), ("sha256sum", "#!/bin/bash\ncat >/dev/null\n")):
        (tools / name).write_text(text)
        (tools / name).chmod(0o755)
    if comfy:
        (prefix / "ComfyUI").mkdir(parents=True)
        (prefix / "ComfyUI/main.py").write_text("")
        (prefix / "ComfyUI/extra_model_paths.yaml").write_text("studio:\n  base_path: " + str(models) + "\n")
    for one in (swap.SAM, swap.REF2VA[0], "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors", swap.TURBO,
                swap.SWAP_LORA, "Workflows-Comfy/latent_upscale_models/minimax_h3_latent_upscaler_3d_bf16.safetensors"):
        (models / one).parent.mkdir(parents=True, exist_ok=True)
        (models / one).write_bytes(b"x")
    env = {**os.environ, "PREFIX": str(prefix), "SPARK_MODELS": str(models), "PATH": f"{tools}:{os.environ['PATH']}"}
    return prefix, models, env


@pytest.mark.parametrize("first,then", [("setup-swap.sh", "setup-workflows.sh"), ("setup-workflows.sh", "setup-swap.sh")])
def test_both_setups_leave_comfyui_both_model_folders_whichever_runs_first(tmp_path, first, then):
    prefix, _, env = setup_machine(tmp_path)
    for script in (first, then, first):
        done = subprocess.run(["bash", str(ROOT / "spark" / script)], capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stdout + done.stderr
    yaml = (prefix / "ComfyUI/extra_model_paths.yaml").read_text()
    assert yaml.count("Workflows-Comfy/checkpoints") == 1 and yaml.count("Workflows-Comfy/latent_upscale_models") == 1
    assert yaml.count("  checkpoints: |") == 1 and yaml.count("  latent_upscale_models: |") == 1
    verify = subprocess.run(["bash", str(ROOT / "spark/setup-workflows.sh"), "--verify"], capture_output=True, text=True, env=env)
    assert verify.returncode == 0 and "does not list" not in verify.stdout


def test_the_swap_setup_downloads_before_the_studio_is_installed(tmp_path):
    _, models, env = setup_machine(tmp_path, comfy=False)
    (models / swap.SWAP_LORA).unlink()
    (models / swap.TURBO).unlink()
    done = subprocess.run(["bash", str(ROOT / "spark/setup-swap.sh")], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stdout + done.stderr  # no $PREFIX yet: nothing to mark for a restart, and no stop
    assert done.stdout.count("(downloaded)") == 2 and (models / swap.SWAP_LORA).is_file() and (models / swap.TURBO).is_file()


def test_the_installer_fetches_the_swaps_files_and_the_checklist_lists_it():
    setup = (ROOT / "spark/setup-swap.sh").read_text()
    for needed in ("h3_character_swap_v1.5.safetensors", "8f2c3c3eba161df8d0c6099598b4730f641f5ce01e04cd16d2427cff8a35218f",
                   "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors", "08cfe946033af7d27719b964b6e0a0e50c32138daabbd6ce4137e23df6bf9980",
                   "sam3.1_multiplex_fp16.safetensors", "mv -n", "South Korea"):
        assert needed in setup
    cuda = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert "--swap) SWAP=1;;" in cuda and 'bash "$HERE/spark/setup-swap.sh"' in cuda and "--swap \"Swap the character\"" in cuda
    rows = subprocess.run(["bash", "-c", f'. "{ROOT}/scripts/install-checklist.sh"; checklist_rows_cuda aarch64'], capture_output=True, text=True).stdout
    row = next(r for r in rows.splitlines() if r.startswith("swap|")).split("|")
    assert row[5] == "--swap" and row[7] == "" and row[4] == "23100"  # the int8 Ref2VA with it, not --ref2va's bf16


def test_the_credits_name_everyone_the_swap_runs():
    credits, notices = (ROOT / "CREDITS.md").read_text(), (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for name in ("Openjutsu", "Akatz Labs", "LightX2V", "SAM 3.1", "MiniMax H3"):
        assert name in credits
    for words in ("comfy-openjutsu", "MIT", "h3_character_swap_v1.5", "Apache License 2.0", "SAM License",
                  "European Union, the United Kingdom, the Republic of Korea", "GNU General Public License v3.0"):
        assert words in notices


# ---------------------------------------------------------------- the Library panel

HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.tab = "Library"; globalThis.render = () => {}; globalThis.toasts = []; globalThis.toast = text => toasts.push(String(text));
globalThis.calls = []; globalThis.answers = {}; globalThis.history = { replaceState() {} }; globalThis.document = { querySelector: () => null };
globalThis.api = async (path, body) => { calls.push([path, body]); return answers[path] || {}; };
globalThis.jobs = []; globalThis.poll = async () => {}; globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
const text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : [node.attrs && node.attrs.placeholder, node.attrs && node.attrs.title, text(node.kids)].filter(Boolean).join(" ");
globalThis.text = text;
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat() });
globalThis.put = (node, ...kids) => node.kids.push(...kids); globalThis.field = (label, node) => el("div", {}, label, node);
globalThis.bind = (obj, key, node) => { node.attrs.value = obj[key] ?? ""; return node; };
globalThis.select = (obj, key, options) => el("select", {}, Object.entries(options).map(([v, t]) => el("option", { value: v }, t)));
globalThis.choice = (obj, key, options) => el("div", { class: "choice" }, Object.entries(options).map(([v, [t, s]]) => el("button", {}, t, s)));
globalThis.media = path => el("video", { src: "/files/" + path }); globalThis.file = path => "/files/" + path;
globalThis.jobCard = job => el("div", {}, "job " + job.id);
const find = (node, test) => !node || typeof node !== "object" ? null : Array.isArray(node) ? node.map(n => find(n, test)).find(Boolean) || null : test(node) ? node : find(node.kids, test);
globalThis.find = find;
for (const f of process.argv.slice(2, -1)) vm.runInThisContext(fs.readFileSync(f, "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[process.argv.length - 1], "utf8")}; console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });`);
"""


def page(code: str) -> dict:
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "harness.js").write_text(HARNESS)
        (Path(scratch) / "test.js").write_text(code)
        done = subprocess.run(["node", str(Path(scratch) / "harness.js"), str(ROOT / "app/static/actions.js"), str(ROOT / "app/static/revise.js"),
                               str(ROOT / "app/static/swap.js"), str(Path(scratch) / "test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_clip_gets_swap_the_character_and_its_panel_queues_one_job():
    got = page("""
      ACT.state = { upscale: { ready: true }, remove: { ready: true }, turnaround: { ready: true } };
      SWAP.state = { ready: true, seconds: [5, 15], qualities: [480, 768], quality: 480, anchor: true, anchor_model: "flux3k-fast", direction_most: 500,
                     estimates: { "480": { "5": 331, "10": 670 }, "768": { "5": 739, "10": 1572 } }, anchor_seconds: { "480": 41, "768": 54 },
                     licence: "MiniMax H3 and the Character Swap LoRA (Akatz Labs): MiniMax H3 Community License, not licensed for use in the EU, the UK, South Korea or the US." };
      out.video = actButtons({ kind: "video", path: "renders/a.mp4" }).map(b => text(b.kids));
      out.image = actButtons({ kind: "image", path: "uploads/a.png" }).map(b => text(b.kids));
      const hot = node => find(node, n => n.attrs && n.attrs.class === "hot");
      actOpen("swap", "renders/a.mp4"); let panel = actPanel(); out.panel = text(panel); out.id = panel.attrs.id;
      out.seg = find(panel, n => n.attrs && n.attrs.class === "seg-ctl").kids.map(b => [text(b.kids), b.attrs.class]);
      out.anchor = find(panel, n => n.attrs && n.attrs.class === "switch").kids[0].attrs.checked;
      out.range = (({ min, max, step }) => ({ min, max, step }))(find(panel, n => n.attrs && n.attrs.type === "range").attrs);
      hot(panel).attrs.onclick(); out.refused = toasts.slice(-1)[0];
      SWAP.f.swaps[0].image = "scouts/s1/hero.png"; SWAP.f.seconds = 10; SWAP.f.start = 2; SWAP.f.swaps[0].direction = "keep the scarf";
      out.windows = text(actPanel()).includes("3 windows"); out.ten = text(actPanel());
      out.counts = [5, 6, 9, 10, 14, 15].map(swapWindows);
      out.high = find(actPanel(), n => n.attrs && n.attrs.class === "seg-ctl").kids[1].attrs.title;
      answers["/api/jobs"] = { id: "j7", title: "Character Swap: a.mp4" };
      await hot(actPanel()).attrs.onclick(); out.sent = calls.slice(-1)[0];
      jobs = [{ id: "j7", status: "done", outputs: ["renders/j7.mp4", "masks/j7/person.mp4", "scouts/j7/anchor.png"] }];
      out.done = text(actPanel());
      SWAP.state = { ready: false, why: "Character Swap runs on a DGX Spark or another CUDA machine with MiniMax H3 Ref2VA, not on a Mac." };
      out.mac = text(actPanel());
    """)
    assert got["video"] == ["Upscale", "Remove a person", "Restyle", "Extend", "Fix", "Swap the character"] and got["image"] == ["Turnaround"]
    assert got["id"] == "actpanel" and "Character Swap" in got["panel"] and "Swap the character" in got["panel"]
    assert "largest person" in got["panel"] and "Put the character in the first frame first" in got["panel"] and "FLUX3-K Fast" in got["panel"]
    assert "not licensed for use in the EU, the UK, South Korea or the US" in got["panel"]
    assert got["seg"] == [["Fast · 480p", "on"], ["High · 768p", ""]] and got["anchor"] is True and got["range"] == {"min": 5, "max": 15, "step": 1}
    assert "about 6 min on a DGX Spark" in got["panel"] and "about 11 min on a DGX Spark" in got["ten"]
    assert got["refused"] == "Choose the character’s picture first"
    assert got["windows"] is True
    assert got["counts"] == [len(plan.windows(plan.part_frames(n))) for n in (5, 6, 9, 10, 14, 15)]  # as swap_plan.py makes them
    assert got["high"] == "768p, a little over twice as long"
    assert all(2 < swap.estimate(n, 768) / swap.estimate(n, 480) < 2.5 for n in range(5, 16))  # what that title says
    assert got["sent"] == ["/api/jobs", {"kind": "swap", "params": {"video": "renders/a.mp4", "image": "scouts/s1/hero.png", "start": 2, "seconds": 10,
                                                                   "quality": 480, "anchor": True, "direction": "keep the scarf", "seed": 904239}}]
    assert "job j7" in got["done"] and "Swapped" in got["done"] and "Who was swapped, in green" in got["done"] and "The first frame it started from" in got["done"]
    assert "not on a Mac" in got["mac"]


def test_a_swap_the_machine_has_no_room_for_is_refused_before_it_is_queued(tmp_path, monkeypatch):
    import memory_guard

    s = stand_in(tmp_path)
    assert s.build("swap", GOOD, "j1")  # an idle DGX Spark takes High (768p)
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 124_500, "MemAvailable": 80_000})
    with pytest.raises(HTTPException) as refused:
        s.build("swap", GOOD, "j1")  # 80 GB available: High is refused, and Fast is named as what fits
    said = refused.value.detail
    assert refused.value.status_code == 400 and "Character Swap at High (768p) needs about 62 GB" in said and "Fast (480p) fits" in said
    assert s.build("swap", {**GOOD, "quality": 480}, "j1")
    assert not (s.home / "masks" / "j1").exists()  # a refused swap made no folder


def test_a_window_loads_the_text_encoder_a_small_card_kept(monkeypatch):
    """An install that kept an NVFP4 text encoder has no int8 one: ComfyUI turned the graph away for the missing name."""

    kept = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"

    class Kept(FakeComfy):
        have = [kept]

        def call(self, path, data=None):
            return {"CLIPLoader": {"input": {"required": {"clip_name": [self.have]}}}}

        def default_text_encoder(self):
            return kept

    monkeypatch.setattr(spark_swap, "sg", Kept(), raising=False)
    assert spark_swap.text_encoder() == kept
    Kept.have = [FakeComfy.H3_TEXT, kept]
    assert spark_swap.text_encoder() == FakeComfy.H3_TEXT
    Kept.have = []
    assert spark_swap.text_encoder() == FakeComfy.H3_TEXT


def test_a_few_words_choose_who_is_swapped(tmp_path, monkeypatch):
    """Several people in the first frame: `person` goes to SAM 3.1 in place of "person", for finding and following, and
    the anchor's edit names them; without it everything is as before."""

    s = stand_in(tmp_path)
    command, _, _ = s.build("swap", {**GOOD, "person": "  man in a   blue jacket "}, "j1")
    assert "--person=man in a blue jacket" in command
    assert not any(one.startswith("--person") for one in s.build("swap", dict(GOOD), "j2")[0])
    for bad in (7, "x" * (swap.PERSON_MOST + 1)):
        with pytest.raises(HTTPException):
            s.build("swap", {**GOOD, "person": bad}, "j3")
    assert spark_swap.sam_nodes()["2"]["inputs"]["text"] == "person"
    assert spark_swap.sam_nodes("man in a blue jacket")["2"]["inputs"]["text"] == "man in a blue jacket"
    assert spark_swap.anchor_edit() == spark_swap.ANCHOR_EDIT
    assert spark_swap.anchor_edit("The man in a blue jacket").startswith("Image 1 with the man in a blue jacket replaced by the character of image 2")
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    graphs = []
    monkeypatch.setattr(spark_swap, "run_graph", lambda w, sampler, named=None: graphs.append(w["2"]["inputs"]["text"]) or "p1")
    monkeypatch.setattr(spark_swap, "said_text", lambda prompt, node: "[[]]")
    with pytest.raises(SystemExit) as refused:
        spark_swap.find_person({"frames": [tmp_path / "f.png"], "width": 64, "height": 32}, tmp_path, "man in a blue jacket")
    assert 'nobody matching "man in a blue jacket"' in str(refused.value) and graphs == ["man in a blue jacket"]


def test_a_swap_runs_on_another_build_of_ref2va_the_machine_offers(tmp_path, monkeypatch):
    """`transformer`: one of the files the server offers (a small card's 4-bit build) reaches the script as
    SPARK_TRANSFORMER, where it is the file the windows load; any other name is refused."""

    own = "minimax_h3_ref2va_pruned_w4a8_mixed.safetensors"
    s = stand_in(tmp_path)
    assert "SPARK_TRANSFORMER" not in s.build("swap", dict(GOOD), "j1")[1]
    with pytest.raises(HTTPException):
        s.build("swap", {**GOOD, "transformer": own}, "j2")  # nothing is offered here
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap.sg, "call", lambda path, data=None: {"UNETLoader": {"input": {"required": {"unet_name": [[own, FakeComfy.REF2VA["int8"]]]}}}}, raising=False)
    monkeypatch.delenv("SPARK_TRANSFORMER", raising=False)
    assert spark_swap.weights() == FakeComfy.REF2VA["int8"]
    monkeypatch.setenv("SPARK_TRANSFORMER", own)
    assert spark_swap.weights() == own
    monkeypatch.setenv("SPARK_TRANSFORMER", "missing.safetensors")
    with pytest.raises(SystemExit):
        spark_swap.weights()


def test_a_part_is_longer_only_where_the_guard_warns(tmp_path, monkeypatch):
    """15 s is the measured limit; a machine whose guard warns (a small card) tries up to plan.LONGEST_TRIED, and the
    script takes the longer part only when the app says so."""

    s = stand_in(tmp_path, length=30.0)
    with pytest.raises(HTTPException):
        s.build("swap", {**GOOD, "seconds": 20}, "j1")
    def inside(path):
        return (s.home / str(path)).resolve()

    spec = swap.attach(s.home, ROOT, True, inside, lambda p: str(Path(p).relative_to(s.home.resolve())),
                       lambda path: (30.0, True), None, None, lambda: True)
    command, env, _ = spec[1]("swap", {**GOOD, "seconds": 20}, "j2")
    assert "--seconds 20" in " ".join(command) and env["SWAP_LONGEST"] == f"{plan.LONGEST_TRIED:g}"
    assert "SWAP_LONGEST" not in spec[1]("swap", {**GOOD, "seconds": 15}, "j3")[1]
    with pytest.raises(HTTPException):
        spec[1]("swap", {**GOOD, "seconds": plan.LONGEST_TRIED + 1}, "j4")
    assert [w["length"] for w in plan.windows(plan.part_frames(plan.LONGEST_TRIED))][0] == plan.WINDOW


# ---------------------------------------------------------------- several people: numbers, the chain, the crop

@pytest.mark.parametrize("box,canvas", [
    ((300, 100, 420, 400), (864, 480)), ((10, 200, 60, 300), (864, 480)), ((0, 0, 700, 480), (864, 480)),
    ((100, 50, 700, 470), (1344, 768)), ((400, 0, 560, 480), (864, 480)), ((0, 300, 480, 420), (480, 864)),
])
def test_a_persons_crop_is_on_32_inside_the_frame_and_never_more_pixels_than_it(box, canvas):
    found = plan.crop(*box, *canvas)
    (x, y, w, h), (wide, tall) = found["box"], found["size"]
    assert wide % 32 == 0 and tall % 32 == 0 and wide * tall <= canvas[0] * canvas[1]  # no more rows than the measured frame
    assert 0 <= x and 0 <= y and x + w <= canvas[0] and y + h <= canvas[1]
    assert x <= box[0] and y <= box[1] and x + w >= min(box[2], canvas[0]) and y + h >= min(box[3], canvas[1])  # the person is in it
    assert 9 / 16 - 0.05 <= wide / tall <= 16 / 9 + 0.05
    assert abs(w / h - wide / tall) < 0.08  # drawn in the box's own shape


def test_a_person_who_fills_the_frame_is_drawn_on_the_frame():
    assert plan.crop(0, 0, 864, 480, 864, 480) == {"box": (0, 0, 864, 480), "size": (864, 480)}
    with pytest.raises(ValueError):
        plan.crop(10, 10, 10, 40, 864, 480)


TWO = [{"image": "uploads/hero.png", "which": 2, "direction": " keeps the  scarf "}, {"image": "uploads/hero.png", "person": "man in a blue jacket", "seed": 9}]


def test_several_people_are_one_job_with_a_swap_each(tmp_path):
    s = stand_in(tmp_path)
    command, _, outputs = s.build("swap", {"video": "uploads/clip.mp4", "start": 1, "seconds": 5, "quality": 480, "seed": 7, "swaps": TWO}, "j1")
    assert "--image" not in command and "--which" not in command
    sent = json.loads(next(c for c in command if c.startswith("--swaps="))[len("--swaps="):])
    hero = str((s.home / "uploads/hero.png").resolve())
    assert sent == [{"image": hero, "which": 2, "person": "", "direction": "keeps the scarf", "seed": 7},
                    {"image": hero, "which": 0, "person": "man in a blue jacket", "direction": "", "seed": 9}]
    assert outputs[0] == str(s.home / "renders/j1.mp4")
    # one person picked by number: the same swap, with their number
    command, _, _ = s.build("swap", {**GOOD, "which": 3}, "j2")
    assert command[command.index("--which") + 1] == "3" and "--image" in command
    # the time: each further person is a whole swap less the finding
    one, two = swap.estimate(5, 480), swap.estimate(5, 480, swaps=2)
    assert two == 2 * one - round(swap.FIND + swap.OTHER)
    assert s.guess({"seconds": 5, "swaps": TWO}) == two


@pytest.mark.parametrize("change,said", [
    ({"swaps": []}, "1 to 4 people"),
    ({"swaps": [{"image": "uploads/hero.png", "which": n} for n in range(1, 6)]}, "1 to 4 people"),
    ({"swaps": "two"}, "1 to 4 people"),
    ({"swaps": TWO, "image": "uploads/hero.png"}, "takes the place of image"),
    ({"swaps": [{"which": 1}]}, "choose the character's picture of swap 1"),
    ({"swaps": [{"image": "uploads/hero.png", "which": 2}, {"image": "uploads/hero.png", "which": 2}]}, "same person"),
    ({"swaps": [{"image": "uploads/hero.png"}, {"image": "uploads/hero.png"}]}, "say who each swap is for"),
    ({"swaps": [{"image": "uploads/hero.png", "which": 10}]}, "1 to 9"),
    ({"swaps": [{"image": "uploads/hero.png", "which": 1, "person": "the tall one"}]}, "not both"),
    ({"swaps": [{"image": "uploads/hero.png", "which": 1, "seed": -1}]}, "swap 1: the seed"),
    ({"swaps": [{"image": "uploads/hero.png", "which": 1, "direction": "x" * 501}]}, "at most 500"),
    ({"image": "uploads/hero.png", "which": 0.5}, "1 to 9"),
    ({"who": "yes"}, "who is true or false"),
])
def test_what_several_people_are_refused_for_before_the_job_is_queued(tmp_path, change, said):
    s = stand_in(tmp_path)
    given = {"video": "uploads/clip.mp4", "seconds": 5, **change}
    with pytest.raises(HTTPException) as refused:
        s.build("swap", given, "j1")
    assert refused.value.status_code == 400 and said in refused.value.detail


def test_numbering_the_people_is_a_job_that_makes_one_picture(tmp_path):
    s = stand_in(tmp_path)
    command, _, outputs = s.build("swap", {"video": "uploads/clip.mp4", "start": 1, "seconds": 5, "quality": 480, "who": True}, "j1")
    assert command[command.index("--who") + 1] == str(s.home / "scouts/j1/people.png") == outputs[0] and len(outputs) == 1
    assert "--image" not in command and "-o" not in command and "--swap-lora" not in command
    assert s.title({"video": "uploads/clip.mp4", "who": True}) == "Character Swap: the people in clip.mp4"
    assert s.guess({"who": True, "seconds": 10}) == round(swap.OTHER + swap.FOLLOW * 240)
    # its picture stays when the job is tidied (it made no clip); a failed one leaves nothing
    (s.home / "scouts/j1").mkdir()
    (s.home / "scouts/j1/people.png").write_bytes(b"png")
    (s.home / "scouts/j2").mkdir()
    swap.tidy(s.home, "j1")
    swap.tidy(s.home, "j2")
    assert (s.home / "scouts/j1/people.png").is_file() and not (s.home / "scouts/j2").exists()


def mask_file(path, size, box):
    from PIL import Image

    mask = Image.new("L", size)
    mask.paste(255, box)
    mask.save(path)
    return path


def test_everyone_is_followed_through_the_whole_part_from_the_word_person(monkeypatch, tmp_path):
    """The graph of --who and of a swap by number: text-prompted tracking with no first-frame mask, so a person who
    comes into view later is found, and one mask stream an object."""

    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    graphs = []
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap, "put", lambda path: "in_" + Path(path).name)
    monkeypatch.setattr(spark_swap, "forget_upload", lambda path: None)
    monkeypatch.setattr(spark_swap, "run_graph", lambda w, sampler, named=None: graphs.append(w) or "p1")
    monkeypatch.setattr(spark_swap, "collect", lambda prompt, node: [node])
    monkeypatch.setattr(spark_swap, "fetch_all", lambda items, folder: [mask_file(tmp_path / f"m{items[0]}.png", (64, 32), (2, 2, 30, 30))])
    (tmp_path / "source.mp4").write_bytes(b"clip")
    found = spark_swap.everyone({"count": 3}, tmp_path, [0, 2])
    w = graphs[0]
    track = w["7"]["inputs"]
    assert w["7"]["class_type"] == "SAM3_VideoTrack" and "initial_mask" not in track and track["max_objects"] == plan.MOST_PEOPLE
    assert w["2"]["inputs"]["text"] == "person" and track["conditioning"] == ["2", 0] and track["detection_threshold"] == spark_swap.TRACK
    picked = sorted(n["inputs"]["object_indices"] for n in w.values() if n["class_type"] == "SAM3_TrackToMask")
    assert picked == ["0", "2"] and not any(n["class_type"] == "ImageScaleBy" for n in w.values())
    assert sorted(found) == [0, 2] and len(found[0]) == 3 and found[0][2].sum() == 28 * 28  # a short stream holds its last frame
    spark_swap.everyone({"count": 3}, tmp_path, [0], 0.5)  # the who's-who picture reads them at half size
    assert [n["inputs"]["scale_by"] for n in graphs[1].values() if n["class_type"] == "ImageScaleBy"] == [0.5]
    assert np is not None


def test_a_person_is_left_alone_before_they_come_into_view_and_mended_while_they_are_in_it():
    np = pytest.importorskip("numpy")
    on, off = np.ones((16, 16), bool), np.zeros((16, 16), bool)
    speck = off.copy()
    speck[0, 0] = True  # a few pixels are not a person
    masks, filled = spark_swap.mend([off, speck, on, off, on, off])
    assert filled == 1 and [bool(m.any()) for m in masks] == [False, False, True, True, True, False]
    masks, filled = spark_swap.mend([off, off])
    assert filled == 0 and not any(m.any() for m in masks)


def test_the_numbered_picture_shows_everyone_even_those_who_come_in_later(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    size, half = (128, 96), (48, 64)  # the masks at half the canvas, as --who reads them
    frames = []
    for n in range(4):
        frames.append(tmp_path / f"{n}.png")
        Image.new("RGB", size, (90, 90, 90)).save(frames[-1])
    part = {"frames": frames, "width": size[0], "height": size[1]}

    def at(left, right, *there):
        mask = np.zeros(half, bool)
        mask[10:40, left:right] = True
        return [mask if n in there else np.zeros(half, bool) for n in range(4)]

    # person 1 is alone in the first frame; 2 and 3 come in at frame 2; 4 is only in the last frame, after 1 has left
    people = {0: at(5, 15, 0, 1, 2), 1: at(25, 35, 2, 3), 2: at(45, 55, 2, 3), 3: at(58, 63, 3), 4: at(0, 0)}
    shown = spark_swap.whos_who(part, people, tmp_path / "who/people.png")
    assert shown == [1, 2, 3, 4]  # an object that was never there has no number
    made = np.asarray(Image.open(tmp_path / "who/people.png").convert("RGB")).astype(int)
    assert made.shape[:2] == (2 * size[1], size[0])  # frame 2 holds three of them; frame 3 is added for the fourth
    grey = (90, 90, 90)
    assert tuple(made[60, 20]) != grey and tuple(made[60, 60]) != grey and tuple(made[60, 100]) != grey and tuple(made[60, 122]) == grey
    assert tuple(made[size[1] + 60, 122]) != grey and tuple(made[5, 60]) == grey
    with pytest.raises(SystemExit) as refused:
        spark_swap.whos_who(part, {0: at(0, 0)}, tmp_path / "none.png")
    assert "no person was found in the part" in str(refused.value)


def test_a_number_picks_its_person_wherever_they_come_in_and_words_pick_one_in_the_first_frame(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    size = (128, 96)

    def at(left, right, *there):
        mask = np.zeros((size[1], size[0]), bool)
        mask[10:90, left:right] = True
        return [mask if n in there else np.zeros((size[1], size[0]), bool) for n in range(30)]

    asked = []

    def everyone(part, work, wanted, scale=1.0):
        asked.append(wanted)
        return {0: at(5, 30, *range(30)), 2: at(90, 120, *range(12, 30)), 5: at(0, 0)}

    first = {"mask": mask_file(tmp_path / "first.png", size, (5, 10, 30, 90)), "area": 2000, "score": 0.97, "x": 17.0, "top": 10}
    monkeypatch.setattr(spark_swap, "everyone", everyone)
    monkeypatch.setattr(spark_swap, "detect", lambda part, work, words="": [first])
    monkeypatch.setattr(spark_swap, "track", lambda part, chosen, work, words="": at(50, 70, *range(30)))
    part = {"width": size[0], "height": size[1], "count": 30}
    tracks = spark_swap.follow_all(part, tmp_path, [{"which": 3, "person": ""}, {"which": 0, "person": "woman in red"}])
    assert asked == [[2]]  # only the numbered person's object is read
    assert not tracks[0][0].any() and tracks[0][12].any() and tracks[1][0][50, 60]  # person 3 comes in at frame 12
    with pytest.raises(SystemExit) as refused:
        spark_swap.follow_all(part, tmp_path, [{"which": 6, "person": ""}])
    assert "there is no person 6 in this part" in str(refused.value)
    monkeypatch.setattr(spark_swap, "track", lambda part, chosen, work, words="": at(5, 30, *range(30)))
    with pytest.raises(SystemExit) as refused:  # person 1, and the largest in the first frame, who is person 1
        spark_swap.follow_all(part, tmp_path, [{"which": 1, "person": ""}, {"which": 0, "person": ""}])
    assert "swaps 1 and 2 name the same person" in str(refused.value)


def test_two_people_standing_close_do_not_draw_over_each_other():
    np = pytest.importorskip("numpy")
    pytest.importorskip("torch")
    left, right = np.zeros((96, 256), bool), np.zeros((96, 256), bool)
    left[20:80, 60:100] = True
    right[20:80, 110:150] = True
    one, two = spark_swap.apart([[left], [right]])
    assert one[0][50, 105] and two[0][50, 105]  # the ground between them is in both grown masks
    assert not (one[0] & right).any() and not (two[0] & left).any()  # neither holds the other person
    # grown by a margin of its own size (0.3 of the root of 2400 pixels: 15), not the 64 of a plain swap
    assert (one[0] & left).sum() == left.sum() and one[0][50, 46] and not one[0][50, 40]
    big = np.zeros((480, 864), bool)
    big[40:440, 200:600] = True  # a close-up: the margin stops at the plain swap's 64 pixels
    grown = spark_swap.grow_to_size([big, np.zeros((480, 864), bool)])
    assert grown[0][240, 137] and not grown[0][240, 135] and not grown[1].any()


def test_a_swap_in_a_crop_is_pasted_back_through_the_mask_and_the_rest_is_the_clips_own(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    size = (256, 160)
    rng = np.random.default_rng(3)
    frames, masks = [], []
    for n in range(3):
        frames.append(tmp_path / f"{n}.png")
        Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(frames[-1])
        mask = np.zeros((size[1], size[0]), bool)
        mask[30:130, 40 + 4 * n:90 + 4 * n] = True
        masks.append(mask)
    part = {"frames": frames, "width": size[0], "height": size[1], "count": 3, "sound": None}
    own, small, box = spark_swap.crop_part(part, frames, masks, tmp_path / "swap1")
    x, y, w, h = box
    assert (own["width"], own["height"]) == plan.crop(40, 30, 98, 130, *size)["size"] and own["count"] == 3 and len(own["frames"]) == 3
    assert Image.open(own["frames"][0]).size == (own["width"], own["height"]) and small[0].shape == (own["height"], own["width"])
    assert x <= 40 and y <= 30 and x + w >= 98 and y + h >= 130 and small[0].any() and small[0].dtype == bool
    drawn = tmp_path / "red.png"
    Image.new("RGB", (own["width"], own["height"]), (255, 0, 0)).save(drawn)
    pasted = spark_swap.paste(frames, [drawn] * 3, masks, box, tmp_path / "swap1")
    for n in range(3):
        before, after = np.asarray(Image.open(frames[n])).astype(int), np.asarray(Image.open(pasted[n])).astype(int)
        far = np.ones(masks[n].shape, bool)
        far[30 - 16:130 + 16, 40 + 4 * n - 16:90 + 4 * n + 16] = False
        assert (before[far] == after[far]).all()  # outside the mask and its soft edge: the clip's own pixels, exactly
        inner = after[50:110, 55 + 4 * n:75 + 4 * n]
        assert (inner[..., 0] == 255).all() and (inner[..., 1:] == 0).all()  # inside: what the swap drew
    # a person who fills the frame: nothing is cut, the frames are the ones given
    full = [np.ones((size[1], size[0]), bool)] * 3
    own, small, box = spark_swap.crop_part(part, frames, full, tmp_path / "swap2")
    assert own["frames"] == frames and box == (0, 0, *size) and not (tmp_path / "swap2/source").exists()


def test_the_swaps_of_a_run_are_read_from_the_command(tmp_path):
    hero = tmp_path / "hero.png"
    hero.write_bytes(b"png")
    base = dict(image=str(hero), which=0, person=" the  tall one ", direction="", seed=7, swaps="")
    assert spark_swap.asked(SimpleNamespace(**base)) == [{"image": str(hero), "which": 0, "person": "the tall one", "direction": "", "seed": 7}]
    two = json.dumps([{"image": str(hero), "which": 2, "person": "ignored beside a number"}, {"image": str(hero), "person": "man", "seed": 9}])
    assert spark_swap.asked(SimpleNamespace(**{**base, "swaps": two})) == [
        {"image": str(hero), "which": 2, "person": "", "direction": "", "seed": 7}, {"image": str(hero), "which": 0, "person": "man", "direction": "", "seed": 9}]
    for bad, said in (("{", "JSON list"), ("[]", "1 to 4"), (json.dumps([{"image": str(hero)}] * 5), "1 to 4"),
                      (json.dumps([{"image": str(hero), "which": 12}]), "1 to 9"), (json.dumps([{"which": 1}]), "no character picture"),
                      (json.dumps([{"image": str(tmp_path / "gone.png")}]), "no character picture at")):
        with pytest.raises(SystemExit) as refused:
            spark_swap.asked(SimpleNamespace(**{**base, "swaps": bad}))
        assert said in str(refused.value)


def test_two_people_are_swapped_one_after_another_each_on_the_result_before(monkeypatch, tmp_path):
    """The whole run with ComfyUI stood in for: both people followed on the clip's own frames first, the second swap
    drawn on frames that already hold the first, and the final frames the clip's own outside both masks."""

    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    from PIL import Image

    size, count = (448, 256), 120
    hero = tmp_path / "hero.png"
    hero.write_bytes(b"png")
    lora = tmp_path / "lora.safetensors"
    lora.write_bytes(b"x")
    work = tmp_path / "work"
    source = tmp_path / "frame.png"
    Image.new("RGB", size, (90, 90, 90)).save(source)

    def prepare(args, folder):
        return {"width": size[0], "height": size[1], "frames": [source] * count, "count": count, "sound": None, "clip": {}}

    boxes = {1: (60, 100, 100, 160), 2: (340, 100, 380, 160)}  # the two people, left and right
    seen = {"tracked": [], "drawn_on": [], "windows": []}

    def follow_all(part, folder, swaps):
        tracks = []
        for one in swaps:
            seen["tracked"].append(one["which"])
            mask = np.zeros((size[1], size[0]), bool)
            left, top, right, bottom = boxes[one["which"]]
            mask[top:bottom, left:right] = True
            tracks.append([mask] * count)
        return tracks

    def window_inputs(one, part, masks, made, folder, anchored):
        # what this swap is drawn on: the middle of its crop's first frame
        seen["drawn_on"].append(Image.open(part["frames"][0]).convert("RGB").getpixel((part["width"] // 2, part["height"] // 2)))
        return {"part": part, "folder": folder}

    def window(one, files, part, args, unet, loras, character, folder, blocks=None):
        seen["windows"].append((folder.name, args.seed, part["width"], part["height"]))
        colour = (255, 0, 0) if folder.name == "swap1" else (0, 0, 255)
        (folder / "window1").mkdir(parents=True, exist_ok=True)
        drawn = folder / "window1" / "drawn.png"
        Image.new("RGB", (part["width"], part["height"]), colour).save(drawn)
        return [drawn] * one["length"]

    final = {}

    def assemble(made, part, target, folder):
        final["first"] = np.asarray(Image.open(made[0]).convert("RGB")).astype(int)
        final["count"] = len(made)

    fake = SimpleNamespace(UPLOADED=[], PREFIX=tmp_path, upload=lambda path: "up", installed=lambda kind, name: True,
                           hub_loras=lambda names: [(names[0].split(":")[0], 1.0)],
                           call=lambda path, body=None: {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[spark_swap.SAM]]}}}})
    monkeypatch.setattr(spark_swap, "sg", fake, raising=False)
    for name, stand in (("prepare", prepare), ("refuse_cuts", lambda part: None), ("server", lambda kind: None), ("need", lambda nodes: None),
                        ("follow_all", follow_all), ("window_inputs", window_inputs), ("window", window), ("assemble", assemble),
                        ("weights", lambda: "minimax_h3_ref2va_pruned_int8_convrot.safetensors"), ("engine_blocks", lambda: None)):
        monkeypatch.setattr(spark_swap, name, stand)
    swaps = json.dumps([{"image": str(hero), "which": 1}, {"image": str(hero), "which": 2, "seed": 11}])
    monkeypatch.setattr(sys, "argv", ["spark_swap.py", "--video", str(hero), "--seconds", "5", "--quality", "480", "--no-anchor", "--swaps", swaps,
                                      "--swap-lora", str(lora), "--turbo-lora", str(lora), "--work", str(work), "--keep", "-o", str(tmp_path / "out.mp4")])
    spark_swap.main()
    assert seen["tracked"] == [1, 2] and len(seen["windows"]) == 2
    assert [w[:2] for w in seen["windows"]] == [("swap1", 904239), ("swap2", 11)]
    assert all(w[2] * w[3] <= size[0] * size[1] and w[2] % 32 == 0 and w[3] % 32 == 0 for w in seen["windows"])
    assert seen["drawn_on"] == [(90, 90, 90), (90, 90, 90)]  # each crop holds its own person, not yet drawn
    first = final["first"]
    assert final["count"] == count
    assert tuple(first[130, 80]) == (255, 0, 0) and tuple(first[130, 360]) == (0, 0, 255)  # both swaps are in the result
    assert tuple(first[130, 220]) == (90, 90, 90) and tuple(first[5, 5]) == (90, 90, 90)  # between and around them: the clip's own
    assert not (work / "swap1").exists() and (work / "swap2/pasted").is_dir()  # the first swap's frames are not kept


def test_the_panel_takes_several_people_and_numbers_them():
    got = page("""
      ACT.state = {};
      SWAP.state = { ready: true, seconds: [5, 15], qualities: [480, 768], quality: 480, anchor: true, anchor_model: "flux3k-fast", most_swaps: 4, once: 10,
                     estimates: { "480": { "5": 331 }, "768": { "5": 739 } }, anchor_seconds: { "480": 41, "768": 54 }, licence: "" };
      const hot = node => find(node, n => n.attrs && n.attrs.class === "hot");
      const button = (node, words) => find(node, n => n.tag === "button" && text(n.kids) === words);
      actOpen("swap", "renders/a.mp4"); out.one = text(actPanel());
      answers["/api/jobs"] = { id: "w1", title: "Character Swap: the people in a.mp4" };
      await button(actPanel(), "Number the people").attrs.onclick(); out.who = calls.slice(-1)[0];
      jobs = [{ id: "w1", status: "done", outputs: ["scouts/w1/people.png"] }];
      out.numbered = text(actPanel());
      SWAP.f.swaps[0].image = "uploads/a.png"; SWAP.f.swaps[0].who = "2";
      await hot(actPanel()).attrs.onclick(); out.byNumber = calls.slice(-1)[0][1].params;
      button(actPanel(), "Add another person").attrs.onclick(); out.two = text(actPanel());
      hot(actPanel()).attrs.onclick(); out.noPicture = toasts.slice(-1)[0];
      SWAP.f.swaps[1].image = "uploads/b.png"; SWAP.f.swaps[1].who = "2";
      hot(actPanel()).attrs.onclick(); out.same = toasts.slice(-1)[0];
      SWAP.f.swaps[1].who = "the man in a blue jacket"; SWAP.f.swaps[1].direction = "he keeps his hat on";
      await hot(actPanel()).attrs.onclick(); out.sent = calls.slice(-1)[0][1].params;
      button(actPanel(), "Add another person").attrs.onclick(); button(actPanel(), "Add another person").attrs.onclick();
      out.full = !button(actPanel(), "Add another person") && SWAP.f.swaps.length === 4;
    """)
    assert "Number the people" in got["one"] and "Add another person" in got["one"] and "about 6 min" in got["one"]
    assert got["who"] == ["/api/jobs", {"kind": "swap", "params": {"video": "renders/a.mp4", "start": 0, "seconds": 5, "quality": 480, "who": True}}]
    assert "The people of the part, numbered" in got["numbered"]
    assert got["byNumber"]["swaps"] == [{"image": "uploads/a.png", "which": 2}] and "image" not in got["byNumber"]
    assert "Person 1" in got["two"] and "Person 2" in got["two"] and "Swap 2 people" in got["two"] and "about 11 min" in got["two"]
    assert got["noPicture"] == "Choose a picture for every person" and "same person" in got["same"]
    assert got["sent"]["swaps"] == [{"image": "uploads/a.png", "which": 2},
                                    {"image": "uploads/b.png", "person": "the man in a blue jacket", "direction": "he keeps his hat on"}]
    assert got["sent"]["seed"] == 904239 and got["sent"]["anchor"] is True and got["full"] is True


def test_a_person_who_comes_in_later_gets_no_anchor_and_no_window_before_they_are_in_view(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    pytest.importorskip("torch")
    from PIL import Image

    size, count = (256, 160), 240  # 10 s: three windows (frames 0-123, 124-229, 230-239)
    hero = tmp_path / "hero.png"
    hero.write_bytes(b"png")
    source = tmp_path / "frame.png"
    Image.new("RGB", size, (90, 90, 90)).save(source)
    mask = np.zeros((size[1], size[0]), bool)
    mask[40:120, 100:140] = True
    seen = {"windows": [], "anchors": 0}

    def window(one, files, part, args, unet, loras, character, folder, blocks=None):
        seen["windows"].append(one["index"])
        drawn = folder / f"drawn{one['index']}.png"
        Image.new("RGB", (part["width"], part["height"]), (255, 0, 0)).save(drawn)
        return [drawn] * one["length"]

    final = {}
    fake = SimpleNamespace(UPLOADED=[], PREFIX=tmp_path, upload=lambda path: "up", installed=lambda kind, name: True,
                           hub_loras=lambda names: [(names[0].split(":")[0], 1.0)],
                           call=lambda path, body=None: {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[spark_swap.SAM]]}}}})
    monkeypatch.setattr(spark_swap, "sg", fake, raising=False)
    for name, stand in (("prepare", lambda args, folder: {"width": size[0], "height": size[1], "frames": [source] * count, "count": count,
                                                         "sound": None, "clip": {}}),
                        ("refuse_cuts", lambda part: None), ("server", lambda kind: None), ("need", lambda nodes: None),
                        ("follow_all", lambda part, folder, swaps: [[np.zeros_like(mask)] * 130 + [mask] * 110]),  # in view from frame 130
                        ("anchor", lambda *a, **k: seen.__setitem__("anchors", seen["anchors"] + 1)),
                        ("window_inputs", lambda *a, **k: {}), ("window", window),
                        ("assemble", lambda made, part, target, folder: final.update(
                            frames=[tuple(Image.open(made[i]).convert("RGB").getpixel((120, 80))) for i in (0, 123, 129, 130, 239)])),
                        ("weights", lambda: "minimax_h3_ref2va_pruned_int8_convrot.safetensors"), ("engine_blocks", lambda: None)):
        monkeypatch.setattr(spark_swap, name, stand)
    monkeypatch.setattr(sys, "argv", ["spark_swap.py", "--video", str(hero), "--image", str(hero), "--which", "2", "--seconds", "10", "--quality", "480",
                                      "--swap-lora", str(hero), "--turbo-lora", str(hero), "--work", str(tmp_path / "work"), "-o", str(tmp_path / "out.mp4")])
    spark_swap.main()
    assert seen["anchors"] == 0  # nobody to put the character on in the first frame
    assert seen["windows"] == [2, 3]  # the first window has nobody in it: the model is not run for it
    grey, red = (90, 90, 90), (255, 0, 0)
    assert final["frames"] == [grey, grey, grey, red, red]  # the clip's own frames until the person is in view


def test_a_plain_swaps_margin_follows_the_person_too_and_the_fixed_one_can_be_kept(monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("torch")
    small = np.zeros((480, 864), bool)
    small[200:350, 400:460] = True  # a dancer 60 pixels across in a wide shot
    monkeypatch.setattr(spark_swap, "track", lambda part, chosen, work, words="": [small])
    grown = spark_swap.follow_person({}, None, None)[0]
    reach = round(spark_swap.MARGIN * (60 * 150) ** 0.5)  # 28 pixels, not 64
    assert grown[275, 400 - reach] and not grown[275, 400 - reach - 1] and reach < 30
    monkeypatch.setenv("SWAP_GROW", "fixed")
    fixed = spark_swap.follow_person({}, None, None)[0]
    assert fixed[275, 400 - 64] and not fixed[275, 400 - 65]


def test_the_characters_picture_goes_at_full_size_only_where_the_guard_warns(tmp_path, monkeypatch):
    s = stand_in(tmp_path)  # a DGX Spark: the measured, scaled picture, and full size refused
    command, _, _ = s.build("swap", GOOD, "j1")
    assert "--ref-size" not in command
    with pytest.raises(HTTPException) as refused:
        s.build("swap", {**GOOD, "ref_size": "max"}, "j1")
    assert "not measured on this machine" in refused.value.detail
    with pytest.raises(HTTPException) as refused:
        s.build("swap", {**GOOD, "ref_size": "huge"}, "j1")
    assert "match or max" in refused.value.detail
    # where the guard warns (a small card): full size is offered, and the default
    home = s.home
    _, build, *_ = swap.attach(home, ROOT, True, lambda p: (home / str(p)).resolve(), lambda p: str(p), lambda path: (8.0, True), tried=lambda: True)
    command, _, _ = build("swap", GOOD, "j2")
    assert command[command.index("--ref-size") + 1] == "max"
    command, _, _ = build("swap", {**GOOD, "ref_size": "match"}, "j3")
    assert "--ref-size" not in command
    # the window's graph carries it
    w, _ = graph(monkeypatch)
    assert w["20"]["inputs"]["ref_image_size"] == "match"
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap, "put", lambda path: "in_" + Path(path).name)
    one = plan.windows(120)[0]
    files = {"pixels": Path("w/p.mp4"), "ref": Path("w/p.mp4"), "mask": Path("w/m.mp4"), "sound": None, "anchor": None}
    w = spark_swap.window_graph(one, files, {"width": 864, "height": 480}, SimpleNamespace(seed=1, direction="", ref_size="max"),
                                FakeComfy.REF2VA["int8"], [], "up_hero.png")
    assert w["20"]["inputs"]["ref_image_size"] == "max"


def test_a_hand_crossing_a_still_picture_is_not_a_cut_and_a_real_cut_still_is(tmp_path, capsys):
    """A person talking to a locked camera, a hand passing the lens: app/shot.py's two figures call it a cut, and a
    portrait clip of one person was refused for it. Most of the picture stayed, so the part is one shot; a part whose
    whole picture changes is still refused."""

    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(7)
    wall = np.full((864, 480), 150.0) + rng.normal(0, 2, (864, 480))
    wall[300:700, 140:340] = 90  # the person, still

    def part(frames: list) -> dict:
        folder = tmp_path / f"part{len(list(tmp_path.iterdir()))}"
        folder.mkdir()
        paths = []
        for index, frame in enumerate(frames):
            paths.append(folder / f"{index + 1:05d}.png")
            Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8)).save(paths[-1])
        return {"width": 480, "height": 864, "frames": paths}

    def hand(at: int | None) -> np.ndarray:
        frame = wall + rng.normal(0, 1, wall.shape)
        if at is not None:
            frame[500:860, at:at + 190] = 235  # a hand near the lens: about a sixth of the frame
        return frame

    gesture = [hand(None)] * 10 + [hand(20), hand(270)] + [hand(None)] * 10
    spark_swap.refuse_cuts(part(gesture))
    assert "taken as one shot" in capsys.readouterr().out

    other = np.full((864, 480), 60.0) + rng.normal(0, 25, (864, 480))
    with pytest.raises(SystemExit) as refused:
        spark_swap.refuse_cuts(part([hand(None)] * 12 + [other + rng.normal(0, 1, other.shape) for _ in range(12)]))
    assert "has a cut 0.50 s in" in str(refused.value)
