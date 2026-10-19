"""Sound effects and foley (app/sound.py, sound_generate.py): python3 -m pytest tests/test_sound.py.
Nothing real runs: the sound environment's python is a stand-in that writes a short WAV and the take file the real
runner writes, so the job, its checks, the takes list and the Library are tested end to end."""
import json
import os
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


def test_what_is_missing_is_said_and_a_spark_does_not_start_them(ready, monkeypatch, tmp_path):
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
    _, build, _, _ = sound.attach(server.HOME, ROOT, True, server.inside, server.relative)
    with pytest.raises(Exception) as error:
        build("sfx", {"prompt": "rain"}, "x")
    assert "DGX Spark" in str(error.value.detail)
    assert set(said["kinds"]) == {"sfx", "foley"} and "CC-BY-NC" in said["kinds"]["foley"]["licence"] and state


def test_the_runner_names_its_licences_and_lays_foley_under_the_speech():
    text = (ROOT / "sound_generate.py").read_text()
    assert "StableAudioPipeline" in text and "large_44k_v2" in text and "CC-BY-NC 4.0" in text
    assert "amix=inputs=2" in text and "limit=0.891" in text  # under the clip's own sound, peaks under -1 dBFS
    setup = (ROOT / "scripts/setup-sound.sh").read_text()
    assert "MMAUDIO_COMMIT=\"974010a026c731054592d8f777218bd9d85a6c24\"" in setup and "sudo" not in setup
