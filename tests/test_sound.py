"""Sound effects and foley (app/sound.py, sound_generate.py): python3 -m pytest tests/test_sound.py.
Nothing real runs: the sound environment's python is a stand-in that writes a short WAV and the take file the real
runner writes, so the job, its checks, the takes list and the Library are tested end to end."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import sound  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)

RUNNER = r'''
import json, os, sys, time, wave
args = sys.argv[2:]
kind = args[0]
get = lambda k, d=None: args[args.index(k) + 1] if k in args else d
out = get("-o")
with wave.open(out, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\0\0\0\0" * 4410)
if kind == "foley":
    open(get("--clip-out"), "wb").write(open(get("--video"), "rb").read())
home = os.path.realpath(get("--home"))
rel = lambda p: os.path.relpath(os.path.realpath(p), home) if p else p
json.dump({"id": out.rsplit("/", 1)[-1][:-4], "kind": kind, "prompt": get("--prompt", ""), "seed": int(get("--seed")), "path": rel(out),
           "clip": rel(get("--clip-out")), "source": rel(get("--video")), "created": time.time(), "seconds": 0.1,
           "argv": args}, open(get("--take"), "w"))
print("[sound] done")
'''


@pytest.fixture
def ready(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "stable-audio-open-1.0").mkdir(parents=True)
    (models / "stable-audio-open-1.0" / "model_index.json").write_text("{}")
    (models / "mmaudio" / "weights").mkdir(parents=True)
    (models / "mmaudio" / "weights" / "mmaudio_large_44k_v2.pth").write_bytes(b"w")
    stand_in = tmp_path / "python"
    stand_in.write_text(f"#!/bin/sh\nexec {sys.executable} {tmp_path / 'runner.py'} \"$@\"\n")
    stand_in.chmod(0o755)
    (tmp_path / "runner.py").write_text(RUNNER)
    monkeypatch.setenv("SOUND_PYTHON", str(stand_in))
    monkeypatch.setenv("SOUND_MODELS", str(models))
    router, build, title, source = sound.attach(server.HOME, ROOT, False, server.inside, server.relative)
    monkeypatch.setattr(server, "sound_build", build)

    def run(job, command, env, label=""):  # the command as it is (another test file swaps the server's runner for its own)
        done = subprocess.run(command, env={**os.environ, **{k: str(v) for k, v in env.items()}}, capture_output=True, text=True, timeout=120)
        job["log"] += done.stdout + done.stderr
        if done.returncode:
            raise RuntimeError(done.stderr[-300:] or "the runner failed")
        return True
    monkeypatch.setattr(server, "run", run)
    return {"build": build, "models": models}


def finish(job_id, seconds=30):
    ends = time.time() + seconds
    while time.time() < ends:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(job)


def test_a_sound_effect_is_queued_made_and_listed_with_its_words(ready):
    job = client.post("/api/jobs", json={"kind": "sfx", "params": {"prompt": "a door  slams in a stone hall", "seconds": 4, "seed": 7}}).json()
    assert job["title"] == "Sound effect: a door slams in a stone hall"
    done = finish(job["id"])
    assert done["status"] == "done", done.get("error") or done.get("log")
    wav = done["outputs"][0]
    assert wav == f"sound/sfx/{job['id']}.wav" and (server.HOME / wav).is_file()
    take = next(t for t in client.get("/api/sound/takes").json() if t["id"] == job["id"])
    assert take["kind"] == "sfx" and take["seed"] == 7 and take["path"] == wav
    assert "--seconds" in take["argv"] and take["argv"][take["argv"].index("--seconds") + 1] == "4"
    listed = next(m for m in client.get("/api/library").json() if m["path"] == wav)
    assert listed["kind"] == "audio" and listed["sound"] == "sfx" and listed["title"] == "a door slams in a stone hall"


def test_foley_is_made_for_a_library_clip_and_the_clip_with_it_is_a_take(ready):
    clip = server.HOME / "renders" / "walk.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"not really a clip")
    job = client.post("/api/jobs", json={"kind": "foley", "params": {"video": "renders/walk.mp4", "keep_sound": False}}).json()
    assert job["title"] == "Foley: walk.mp4" and job["source"] == "renders/walk.mp4"
    done = finish(job["id"])
    assert done["status"] == "done", done.get("error") or done.get("log")
    film, wav = done["outputs"][:2]
    assert film.endswith(".mp4") and wav.endswith(".wav") and (server.HOME / film).is_file()
    take = next(t for t in client.get("/api/sound/takes").json() if t["id"] == job["id"])
    assert "--replace" in take["argv"] and take["source"] == "renders/walk.mp4"
    assert any(m["path"] == film and m["kind"] == "video" for m in client.get("/api/library").json())


def test_each_setting_is_checked_before_the_job_is_queued(ready):
    bad = [("sfx", {"prompt": ""}, "prompt"), ("sfx", {"prompt": "rain", "seconds": 60}, "1 to 47"),
           ("sfx", {"prompt": "rain", "steps": 5}, "passes"), ("foley", {"video": "renders/none.mp4"}, "pick one from the Library"),
           ("foley", {"video": "../etc/passwd"}, ""), ("sfx", {"prompt": "x" * 700}, "600 characters"),
           ("sfx", {"prompt": "rain", "seed": True}, "seed is a number")]
    for kind, params, words in bad:
        answer = client.post("/api/jobs", json={"kind": kind, "params": params})
        assert answer.status_code in (400, 403), (kind, params, answer.text)
        assert words in answer.json()["error"], (kind, params, answer.json())


def test_what_is_missing_is_said(ready, monkeypatch, tmp_path):
    (ready["models"] / "stable-audio-open-1.0" / "model_index.json").unlink()
    state = sound.attach(server.HOME, ROOT, False, server.inside, server.relative)[0]
    said = client.get("/api/sound/state").json()  # the app's own router: what it sees with these settings
    with pytest.raises(Exception) as error:
        ready["build"]("sfx", {"prompt": "rain"}, "x")
    assert "gated" in str(error.value.detail) and "stable-audio-open-1.0" in str(error.value.detail)
    monkeypatch.setenv("SOUND_PYTHON", str(tmp_path / "nothing"))
    _, build, _, _ = sound.attach(server.HOME, ROOT, False, server.inside, server.relative)
    with pytest.raises(Exception) as error:
        build("foley", {"video": "renders/walk.mp4"}, "x")
    assert "setup-sound.sh" in str(error.value.detail)
    assert set(said["kinds"]) == {"sfx", "foley"} and "CC-BY-NC" in said["kinds"]["foley"]["licence"] and state


def test_the_runner_names_its_licences_and_lays_foley_under_the_speech():
    text = (ROOT / "sound_generate.py").read_text()
    assert "StableAudioPipeline" in text and "large_44k_v2" in text and "CC-BY-NC 4.0" in text
    assert "amix=inputs=2" in text and "limit=0.891" in text  # under the clip's own sound, peaks under -1 dBFS
    setup = (ROOT / "scripts/setup-sound.sh").read_text()
    assert "MMAUDIO_COMMIT=\"974010a026c731054592d8f777218bd9d85a6c24\"" in setup and "sudo" not in setup



# ---------- a DGX Spark: ComfyUI's workflows, the memory guard, the commands ----------

@pytest.fixture
def spark(tmp_path, monkeypatch):
    """A Spark install as app/sound.py looks for it: the studio's venv and ComfyUI, kijai's nodes, the weights."""

    import memory_guard
    prefix, models = tmp_path / "prefix", tmp_path / "spark-models"
    for path in ("venv/bin/python", "ComfyUI/main.py", "ComfyUI/custom_nodes/ComfyUI-MMAudio/nodes.py"):
        (prefix / path).parent.mkdir(parents=True, exist_ok=True)
        (prefix / path).write_text("")
    for kind in sound.SPARK_FILES:
        for name in sound.SPARK_FILES[kind]:
            (models / "Sound-Comfy" / name).parent.mkdir(parents=True, exist_ok=True)
            (models / "Sound-Comfy" / name).write_bytes(b"w")
    monkeypatch.setenv("PREFIX", str(prefix))
    monkeypatch.setenv("SPARK_MODELS", str(models))
    monkeypatch.delenv("SOUND_PYTHON", raising=False)
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 124_500, "MemAvailable": 118_000, "MemFree": 100_000})
    monkeypatch.setattr(memory_guard, "SOUND_ESTIMATE", {"sfx": (8_000, 10), "foley": (9_000, 50)})
    clip = server.HOME / "renders" / "walk.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"not really a clip")
    return {"prefix": prefix, "models": models, "guard": memory_guard, "attach": lambda: sound.attach(server.HOME, ROOT, True, server.inside, server.relative)}


def test_a_spark_runs_both_kinds_through_spark_generate_with_the_macs_flags(spark):
    router, build, _, _ = spark["attach"]()
    command, env, outputs = build("sfx", {"prompt": "rain on a tin roof", "seconds": 8, "seed": 3}, "j1")
    assert command[0] == str(spark["prefix"] / "venv/bin/python") and command[1] == str(ROOT / "spark/spark_generate.py")
    assert command[2] == "sfx" and "--models" not in command and command[command.index("--seconds") + 1] == "8"
    assert outputs == [str(server.HOME / "sound/sfx/j1.wav")] and "--take" in command and "--home" in command
    command, _, outputs = build("foley", {"video": "renders/walk.mp4", "keep_sound": True}, "j2")
    assert command[2] == "foley" and "--clip-out" in command and "--replace" not in command and outputs[0].endswith("j2.mp4")
    # every flag app/sound.py passes is one spark_generate.py's sfx and foley take
    import spark_generate
    parser_flags = {"--prompt", "--negative", "--seed", "--steps", "--cfg", "--seconds", "--video", "--max-seconds",
                    "--foley-db", "--replace", "--clip-out", "--take", "--home", "-o"}
    assert {a for a in command if a.startswith("--") or a == "-o"} <= parser_flags
    text = (ROOT / "spark/spark_generate.py").read_text()
    assert all(f'"{flag}"' in text for flag in parser_flags)
    state = TestClient(_app(router)).get("/api/sound/state").json()
    assert state["spark"] and state["kinds"]["sfx"]["ready"] and state["kinds"]["foley"]["ready"]


def _app(router):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return app


def test_a_spark_refuses_a_kind_the_guard_has_not_measured_or_that_does_not_fit(spark, monkeypatch):
    monkeypatch.setattr(spark["guard"], "SOUND_ESTIMATE", {"sfx": (8_000, 10)})
    router, build, _, _ = spark["attach"]()
    state = TestClient(_app(router)).get("/api/sound/state").json()
    assert state["kinds"]["sfx"]["ready"] and not state["kinds"]["foley"]["ready"]
    assert "not been measured on a DGX Spark" in state["kinds"]["foley"]["why"]
    with pytest.raises(Exception) as error:
        build("foley", {"video": "renders/walk.mp4"}, "x")
    assert "measured memory estimate" in error.value.detail
    monkeypatch.setattr(spark["guard"], "SOUND_ESTIMATE", {"sfx": (110_000, 10), "foley": (9_000, 50)})
    with pytest.raises(Exception) as error:
        build("sfx", {"prompt": "rain", "seconds": 40}, "x")
    assert "refused" in error.value.detail and "floor" in error.value.detail
    # what the Studio's own engines hold counts as room (server.held_room)
    _, build, _, _ = sound.attach(server.HOME, ROOT, True, server.inside, server.relative, lambda: 124_000)
    monkeypatch.setattr(spark["guard"], "meminfo", lambda text=None: {"MemTotal": 124_500, "MemAvailable": 20_000})
    monkeypatch.setattr(spark["guard"], "SOUND_ESTIMATE", {"sfx": (8_000, 10), "foley": (9_000, 50)})
    assert build("sfx", {"prompt": "rain"}, "x")[0][2] == "sfx"


def test_a_spark_says_what_is_not_installed(spark):
    (spark["prefix"] / "ComfyUI/custom_nodes/ComfyUI-MMAudio/nodes.py").unlink()
    (spark["models"] / "Sound-Comfy" / sound.SPARK_FILES["sfx"][0]).unlink()
    router, build, _, _ = spark["attach"]()
    kinds = TestClient(_app(router)).get("/api/sound/state").json()["kinds"]
    assert "gated" in kinds["sfx"]["why"] and "--sound" in kinds["sfx"]["why"]
    assert "ComfyUI-MMAudio" in kinds["foley"]["why"] and "oneshot-setup-spark.sh --sound" in kinds["foley"]["why"]


def test_the_sound_guard_is_measured_or_refuses():
    import memory_guard
    for kind, (fixed, per_second) in memory_guard.SOUND_ESTIMATE.items():  # each line holds its measured runs
        assert memory_guard.SOUND_MEASURED.get(kind), kind
        for asked, peak in memory_guard.SOUND_MEASURED[kind]:
            assert abs(fixed + per_second * asked - peak) <= memory_guard.ERROR_MB, (kind, asked, peak)
            assert asked <= memory_guard.SOUND_LONGEST[kind]
    assert set(memory_guard.SOUND_LONGEST) == set(sound.KINDS)
    assert memory_guard.SOUND_LONGEST["sfx"] == sound.SFX_SECONDS[1] and memory_guard.SOUND_LONGEST["foley"] == sound.FOLEY_SECONDS[1]
    assert memory_guard.sound_check("sfx", 10, total_mb=0)["fits"]  # a Mac
    unknown = memory_guard.sound_check("nothing", 5, available_mb=118_000, total_mb=124_500)
    assert not unknown["fits"] and "not been measured" in unknown["reason"]


def test_the_comfy_workflows(monkeypatch):
    import spark_generate
    w = spark_generate.sfx_workflow("rain on a tin roof", "music", 8, 3, 100, 7.0)
    classes = {k: v["class_type"] for k, v in w.items()}
    assert classes == {"1": "CheckpointLoaderSimple", "2": "CLIPLoader", "3": "CLIPTextEncode", "4": "CLIPTextEncode",
                       "5": "ConditioningStableAudio", "6": "EmptyLatentAudio", "12": "KSampler", "13": "VAEDecodeAudio",
                       "17": "AudioAdjustVolume", "18": "SaveAudio"}
    assert w["2"]["inputs"]["type"] == "stable_audio" and w["5"]["inputs"]["seconds_total"] == 8 and w["6"]["inputs"]["seconds"] == 8
    k = w["12"]["inputs"]
    assert (k["steps"], k["cfg"], k["sampler_name"], k["scheduler"], k["seed"]) == (100, 7.0, "dpmpp_3m_sde_gpu", "exponential", 3)
    assert k["positive"] == ["5", 0] and k["negative"] == ["5", 1] and w["13"]["inputs"]["vae"] == ["1", 2]
    f = spark_generate.foley_workflow("studio_x.mp4", "gravel", "", 5.04, 9)
    assert f["3"] == {"class_type": "LoadVideo", "inputs": {"file": "studio_x.mp4"}}
    s = f["12"]["inputs"]
    assert f["12"]["class_type"] == "StudioMMAudioSampler" and s["images"] == ["4", 0] and s["fps"] == ["4", 2]
    assert (s["steps"], s["cfg"], s["duration"], s["seed"]) == (25, 4.5, 5.04, 9)
    assert f["1"]["inputs"]["mmaudio_model"] == spark_generate.MMAUDIO["model"] and f["2"]["inputs"]["mode"] == "44k"
    # the file names the app checks are the ones the workflows load
    files = {Path(name).name for names in sound.SPARK_FILES.values() for name in names}
    assert {spark_generate.SAO_CHECKPOINT, spark_generate.SAO_TEXT, *spark_generate.MMAUDIO.values()} <= files
    # a sound-only workflow returns its flac (render's SOUND_ONLY)
    assert {"EmptyLatentAudio", "StudioMMAudioSampler"} <= spark_generate.SOUND_ONLY


def test_the_studio_sampler_takes_frames_at_mmaudios_times():
    sys.path.insert(0, str(ROOT / "spark"))
    import studio_mmaudio
    clip = studio_mmaudio.times(120, 24.0, 8.0, 5.0)   # 5 s of a 24 fps clip, CLIP's 8 a second
    assert len(clip) == 40 and clip[:4] == [0, 3, 6, 9] and clip[-1] == 117
    sync = studio_mmaudio.times(120, 24.0, 25.0, 5.0)  # Synchformer's 25 a second
    assert len(sync) == 125 and sync[:3] == [0, 0, 1] and sync[-1] == 119
    assert max(studio_mmaudio.times(30, 30.0, 25.0, 2.0)) == 29  # never past the last frame
    assert "StudioMMAudioSampler" in studio_mmaudio.NODE_CLASS_MAPPINGS


def test_the_spark_install_pins_the_nodes_and_starts_the_sampler_beside_them():
    setup = (ROOT / "oneshot-setup-spark.sh").read_text()
    assert "--sound) SOUND=1" in setup and "MMA_COMMIT=8eaeb72edc3aaf2059b57f2d96a1f6f689f19ae2" in setup
    assert "Sound-Comfy/mmaudio" in setup and "Sound-Comfy/checkpoints" in setup and "Sound-Comfy/text_encoders" in setup
    assert "hf_gate" in setup and "CC-BY-NC 4.0" in setup and "Stability AI Community License" in setup
    section = setup[setup.index('if [ "$SOUND" = 1 ]'):setup.index('if [ "$H3_BASE" = 1 ]')]
    assert "sudo" not in section and "--constraint" in section
    comfy = (ROOT / "spark/comfy.sh").read_text()
    assert "studio_mmaudio" in comfy and "ComfyUI-MMAudio/nodes.py" in comfy
    import sparks
    assert "spark/studio_mmaudio/*.py" in sparks.CODE
