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
