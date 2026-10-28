"""The Library lists sounds too (app/server.py library_sounds): voice lines and songs by their kept takes, sound
effects from the sound folder. python3 -m pytest tests/test_library_sounds.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)


def test_voice_lines_songs_and_sound_effects_are_in_the_library_by_kind():
    home = server.HOME
    for folder in ("voice", "music", "sound/foley"):
        (home / folder).mkdir(parents=True, exist_ok=True)
    (home / "voice/v1.wav").write_bytes(b"RIFF")
    (home / "voice/v1.take.json").write_text(json.dumps({"id": "v1", "path": "voice/v1.wav", "text": "Who's   there?", "created": 1}))
    (home / "voice/gone.take.json").write_text(json.dumps({"id": "gone", "path": "voice/gone.wav", "text": "deleted"}))
    (home / "music/s1.wav").write_bytes(b"RIFF")
    (home / "music/s1.take.json").write_text(json.dumps({"id": "s1", "path": "music/s1.wav", "caption": "slow synth", "created": 2}))
    (home / "sound/foley/door.wav").write_bytes(b"RIFF")
    (home / "sound/foley/notes.txt").write_text("not a sound")
    sounds = {m["path"]: m for m in client.get("/api/library").json() if m["kind"] == "audio"}
    assert sounds["voice/v1.wav"]["sound"] == "voice" and sounds["voice/v1.wav"]["title"] == "Who's there?"
    assert sounds["music/s1.wav"]["sound"] == "music" and sounds["music/s1.wav"]["title"] == "slow synth"
    assert sounds["sound/foley/door.wav"]["sound"] == "sfx"
    assert "voice/gone.wav" not in sounds and not any(p.endswith(".txt") for p in sounds)
    assert client.delete("/api/library", params={"path": "voice/v1.wav"}).status_code == 400  # kept in Voice, not deleted here


def test_a_sound_deleted_mid_listing_does_not_take_the_library_down(monkeypatch):
    """Trash/clear or a finishing render can remove a file between is_file() and stat(): the listing must survive it."""
    home = server.HOME
    (home / "sound").mkdir(parents=True, exist_ok=True)
    (home / "sound/keep.wav").write_bytes(b"RIFF")
    gone = home / "sound/gone.wav"
    gone.write_bytes(b"RIFF")
    real_stat = Path.stat
    calls = {}

    def flaky_stat(self, **kw):
        if Path(self) == gone:
            calls[gone] = calls.get(gone, 0) + 1
            if calls[gone] > 1:  # survives is_file(), dies on the listing's own stat: the real window
                error = FileNotFoundError(str(gone))
                error.errno = 2
                raise error
        return real_stat(self, **kw)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    try:
        answer = client.get("/api/library")
    finally:
        monkeypatch.setattr(Path, "stat", real_stat)
    assert answer.status_code == 200
    paths = {m["path"] for m in answer.json() if m["kind"] == "audio"}
    assert "sound/keep.wav" in paths and "sound/gone.wav" not in paths


def test_a_take_that_failed_its_check_is_in_the_library_and_says_so():
    """The QA gate and the shot check keep a failed take in qa/; the Director rates a failed Slate take no-good. Both are
    listed, marked failed, and can be deleted like any clip. A clip fetched only to be checked is not listed."""

    home = server.HOME
    (home / "qa" / "fetched").mkdir(parents=True, exist_ok=True)
    (home / "qa" / "job1-take1.mp4").write_bytes(b"x")
    (home / "qa" / "job1-take1.jpg").write_bytes(b"x")
    (home / "qa" / "fetched" / "q9.mp4").write_bytes(b"x")
    (home / "slate" / "p-lib" / "takes").mkdir(parents=True, exist_ok=True)
    for name in ("bad", "good", "mine", "gated"):
        (home / "slate" / "p-lib" / "takes" / f"{name}.mp4").write_bytes(b"x")
    (home / "slate" / "p-lib" / "project.json").write_text(json.dumps({"id": "p-lib", "scenes": [{"shots": [{"takes": [
        {"mediaPath": "slate/p-lib/takes/bad.mp4", "rating": "no-good", "origin": "The Director"}, {"mediaPath": "slate/p-lib/takes/good.mp4", "rating": "good"},
        {"mediaPath": "slate/p-lib/takes/mine.mp4", "rating": "no-good"}, {"mediaPath": "slate/p-lib/takes/gated.mp4", "rating": "good", "qa": {"passed": False}}]}]}]}))
    listed = {m["path"]: m for m in client.get("/api/library").json()}
    assert listed["qa/job1-take1.mp4"]["failed"] is True and listed["qa/job1-take1.mp4"]["kind"] == "video"
    assert listed["slate/p-lib/takes/bad.mp4"]["failed"] is True and "failed" not in listed["slate/p-lib/takes/good.mp4"]
    assert listed["slate/p-lib/takes/gated.mp4"]["failed"] is True  # its own QA gate failed it
    assert "failed" not in listed["slate/p-lib/takes/mine.mp4"]  # rated no good by hand: listed, but no check failed it
    assert "qa/fetched/q9.mp4" not in listed and "qa/job1-take1.jpg" not in listed
    assert client.delete("/api/library", params={"path": "qa/job1-take1.mp4"}).status_code == 200


def test_a_project_file_of_another_shape_does_not_break_the_library():
    home = server.HOME
    for name, film in (("p-odd1", {"scenes": [{"shots": ["not a shot", 3, None]}]}), ("p-odd2", {"scenes": [{"shots": [{"takes": "x"}]}]}),
                       ("p-odd3", {"scenes": "none"}), ("p-odd4", ["a list"])):
        (home / "slate" / name).mkdir(parents=True, exist_ok=True)
        (home / "slate" / name / "project.json").write_text(json.dumps(film))
    try:
        assert client.get("/api/library").status_code == 200
    finally:
        for name in ("p-odd1", "p-odd2", "p-odd3", "p-odd4"):
            (home / "slate" / name / "project.json").unlink()
