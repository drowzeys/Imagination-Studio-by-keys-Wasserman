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

def stand_in(tmp_path, spark=True, length=8.0, installed=True, room=lambda: 120_000):
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
                                                      lambda path: (length, True), room)
    return SimpleNamespace(home=home, router=router, build=build, title=title, source=source, guess=guess, models=models, prefix=prefix)


@pytest.fixture(autouse=True)
def keep_environment():
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
    for asked in (GOOD, {**GOOD, "seconds": 3}, {**GOOD, "start": 99}):  # a part too short or past the clip's end: still the Mac's reason
        with pytest.raises(HTTPException) as refused:
            mac.build("swap", asked, "j1")
        assert "not on a Mac" in refused.value.detail
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


def person_guesses(monkeypatch, tmp_path, boxes, masks=()):
    """find_person with SAM 3.1's boxes (as PreviewAny shows them) and its masks (pictures) stood in for; the graphs."""

    pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    graphs = []
    monkeypatch.setattr(spark_swap, "sg", FakeComfy(), raising=False)
    monkeypatch.setattr(spark_swap, "run_graph", lambda w, sampler, named=None: graphs.append(w) or f"p{len(graphs)}")
    monkeypatch.setattr(spark_swap, "preview_text", lambda prompt, node: [boxes])
    saved = []
    for i, (w, h) in enumerate(masks):
        mask = Image.new("L", (64, 32))
        mask.paste(255, (0, 0, w, h))
        mask.save(tmp_path / f"m{i}.png")
        saved.append(tmp_path / f"m{i}.png")
    monkeypatch.setattr(spark_swap, "collect", lambda prompt, node: [{"filename": p.name} for p in saved])
    monkeypatch.setattr(spark_swap, "fetch_all", lambda items, folder: saved)
    frame = tmp_path / "f.png"
    Image.new("RGB", (64, 32)).save(frame)
    return graphs, {"frames": [frame], "width": 64, "height": 32}


def test_a_first_frame_with_nobody_in_it_is_refused(monkeypatch, tmp_path):
    # SAM 3.1 found nothing at all (a flat colour): refused before any mask is asked for, so no empty mask breaks ComfyUI
    graphs, part = person_guesses(monkeypatch, tmp_path, [])
    with pytest.raises(SystemExit) as refused:
        spark_swap.find_person(part, tmp_path)
    assert "no person was found in the part's first frame" in str(refused.value) and len(graphs) == 1
    assert graphs[0]["4"]["inputs"]["individual_masks"] is False and graphs[0]["5"]["class_type"] == "PreviewAny"


def test_a_guess_that_is_not_a_person_is_refused_with_its_score(monkeypatch, tmp_path):
    # a gradient: 0.54 over 56% of the frame; an empty car park: 0.49 over 1%. Neither is a person
    for score, width, height in ((0.541, 48, 24), (0.485, 9, 3), (0.97, 4, 4)):  # the last: a person too small to swap
        graphs, part = person_guesses(monkeypatch, tmp_path, [{"x": 0, "y": 0, "width": width, "height": height, "score": score}])
        with pytest.raises(SystemExit) as refused:
            spark_swap.find_person(part, tmp_path)
        said = str(refused.value)
        assert "no person was found" in said and f"scored {score:.2f}" in said and len(graphs) == 1


def test_a_person_is_found_and_the_largest_mask_is_swapped(monkeypatch, tmp_path, capsys):
    graphs, part = person_guesses(monkeypatch, tmp_path, [{"x": 0, "y": 0, "width": 30, "height": 30, "score": 0.97}],
                                  masks=[(10, 10), (30, 30)])
    chosen = spark_swap.find_person(part, tmp_path)
    assert chosen == tmp_path / "person.png" and chosen.read_bytes() == (tmp_path / "m1.png").read_bytes()
    assert graphs[1]["4"]["inputs"]["threshold"] == spark_swap.PERSON_SCORE and graphs[1]["4"]["inputs"]["individual_masks"] is True
    assert "2 people in the first frame" in capsys.readouterr().out


def test_preview_text_reads_what_sam_showed(monkeypatch):
    shown = [[{"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0, "score": 0.9}]]

    class History(FakeComfy):
        text = __import__("json").dumps(shown)

        def history_of(self, prompt_id):
            return {"status": {"status_str": "success"}, "outputs": {"5": {"text": [self.text]}}}

    comfy = History()
    monkeypatch.setattr(spark_swap, "sg", comfy, raising=False)
    assert spark_swap.preview_text("p", "5") == shown and spark_swap.preview_text("p", "9") == []
    comfy.text = "tensor([0.91])"  # not JSON: said, not taken for an empty frame
    with pytest.raises(SystemExit) as unread:
        spark_swap.preview_text("p", "5")
    assert "could not be read" in str(unread.value) and "tensor" in str(unread.value)


def test_boxes_of_another_shape_are_said_not_taken_for_nobody(monkeypatch, tmp_path):
    graphs, part = person_guesses(monkeypatch, tmp_path, [{"x": 0, "y": 0, "w": 30, "h": 30, "confidence": 0.97}])
    with pytest.raises(SystemExit) as unread:
        spark_swap.find_person(part, tmp_path)
    assert "boxes could not be read" in str(unread.value) and "no person" not in str(unread.value)


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
    made = [server.HOME / "masks" / job["id"] / "person.mp4", server.HOME / "scouts" / job["id"] / "anchor.png"]
    for one in made:  # what a swap cancelled after its person and anchor stages has made
        one.parent.mkdir(parents=True, exist_ok=True)
        one.write_bytes(b"x")
    server.perform(job)  # refused when its turn comes (no clip): the job fails, and its folders go with it
    assert job["status"] == "failed" and not left.exists() and not any(one.parent.exists() for one in made)
    for one in made:  # a swap that finished keeps its mask preview and anchor
        one.parent.mkdir(parents=True, exist_ok=True)
        one.write_bytes(b"x")
    swap.tidy(server.HOME, job["id"], done=True)
    assert all(one.is_file() for one in made)
    swap.tidy(server.HOME, job["id"])


# ---------------------------------------------------------------- the studio around it

def test_the_studio_takes_a_swap_job_and_shows_its_stages(tmp_path):
    os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
    import server
    from fastapi.testclient import TestClient

    assert "swap" in server.swap.KINDS
    said = TestClient(server.app).post("/api/jobs", json={"kind": "swap", "params": {"image": "uploads/x.png"}})
    assert said.status_code == 400 and "choose a clip" in said.json()["error"]  # no "a prompt is needed": the swap has none
    def progress(*lines):
        job = {"id": "t1", "log": "", "status": "running", "progress": None}
        code = "; ".join(f"print({line!r}, flush=True)" for line in lines)
        assert server.run(job, [sys.executable, "-c", code], {})
        return job["progress"]

    assert progress("[tensorfold] stage 5/7 window 2 of 3") == {"stage": "window 2 of 3", "at": 5, "of": 7}
    assert progress("[tensorfold] stage 5/7 window 2 of 3", "[tensorfold] step 3/8") == {"stage": "window 2 of 3 · picture", "at": 3, "of": 8}
    assert progress("[tensorfold] stage 2/7 following the person", "[tensorfold] frame 40/192") == {"stage": "following the person · frame", "at": 40, "of": 192}


def test_the_agents_swap_a_character_through_the_studio_tools(monkeypatch):
    spec = importlib.util.spec_from_file_location("studio_mcp_swap", ROOT / "mcp/studio-mcp.py")
    mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp)
    sent = []
    monkeypatch.setattr(mcp, "call", lambda method, path, body=None, **_: sent.append((method, path, body)) or
                        {"id": "j1", "kind": "swap", "status": "queued", "outputs": []})
    listed = {t["name"]: t for t in mcp.answer({"method": "tools/list"})["tools"]}
    assert listed["swap_character"]["inputSchema"]["required"] == ["clip", "picture"]
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
    assert "--swap) SWAP=1;;" in cuda and 'if [ "$SWAP" = 1 ] && [ "$REF2VA" = 0 ] && [ ! -s "$REF2VA_INT8" ]; then REF2VA=1; fi' in cuda and 'bash "$HERE/spark/setup-swap.sh"' in cuda and "--swap \"Swap the character\"" in cuda
    def swap_row(models):
        rows = subprocess.run(["bash", "-c", f'. "{ROOT}/scripts/install-checklist.sh"; checklist_rows_cuda aarch64'], capture_output=True,
                              text=True, env={**os.environ, "SPARK_MODELS": str(models)}).stdout
        return next(r for r in rows.splitlines() if r.startswith("swap|")).split("|")

    models = Path(tempfile.mkdtemp())
    row = swap_row(models)
    assert row[5] == "--swap" and row[7] == "ref2va" and row[4] == "2100"  # no Ref2VA here: ticking the swap ticks it
    int8 = models / "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    int8.parent.mkdir(parents=True)
    int8.write_bytes(b"x")
    assert swap_row(models)[7] == ""  # its 8-bit copy is here: the swap runs on it, no 40 GB more


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
      SWAP.f.image = "scouts/s1/hero.png"; SWAP.f.seconds = 10; SWAP.f.start = 2; SWAP.f.direction = "keep the scarf";
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


# ---------------------------------------------------------------- memory

def test_the_memory_guard_s_swap_line_holds_the_measured_runs():
    import memory_guard as G

    for quality, runs in G.SWAP_MEASURED.items():
        assert runs, quality
        fixed, per_second = G.SWAP_ESTIMATE[quality]
        for seconds, measured in runs:
            line = fixed + per_second * seconds
            assert measured <= line <= measured + 3_000, (quality, seconds, measured, line)  # never under, at most 3 GB over
    assert set(G.SWAP_ESTIMATE) == set(swap.QUALITIES)


def test_a_swap_that_does_not_fit_is_refused_before_it_is_queued(tmp_path):
    import memory_guard as G

    if not G.meminfo().get("MemTotal"):
        pytest.skip("no /proc/meminfo: everything fits on this machine")
    tight = stand_in(tmp_path, room=lambda: 40_000)  # a Spark with 40 GB for the job
    with pytest.raises(HTTPException) as refused:
        tight.build("swap", GOOD, "j1")
    said = refused.value.detail
    assert refused.value.status_code == 400 and "a Character Swap of 5 s at 768 needs about" in said and "refused" in said
    roomy = stand_in(tmp_path / "roomy")
    assert roomy.build("swap", GOOD, "j2")[0]


def test_a_32_gb_card_refuses_a_swap_and_a_spark_takes_one():
    import memory_guard as G

    card = G.swap_check(768, 5, available_mb=30_000, total_mb=128_000, card=True)
    assert card["fits"] is False and ("this GPU" in card["reason"] or "this machine" in card["reason"])
    spark = G.swap_check(480, 15, available_mb=110_000, total_mb=124_000)  # an idle Spark
    assert spark["fits"] is True and spark["route"] == "swap-480"
    assert G.swap_check(1080, 5, available_mb=110_000, total_mb=124_000)["fits"] is False  # not measured: refused
    assert G.swap_check(480, 5, total_mb=0)["fits"] is True  # no /proc/meminfo (a Mac)
