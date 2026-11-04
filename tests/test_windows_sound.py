"""Measured Windows sound recipe, queue/library wiring and exclusive model switching."""
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_windows_storyboard import client, enabled, image, server  # noqa: F401
import sound
import windows_sound as audio


@pytest.fixture
def ready(enabled, monkeypatch):
    monkeypatch.setattr(sound, "WINDOWS", True)
    monkeypatch.setattr(audio, "status", lambda: {"available": True, "why": ""})
    monkeypatch.setattr(audio, "preflight", lambda: None)
    monkeypatch.setattr(image, "_backend_kind", None)
    return enabled[0]


@pytest.mark.parametrize("change", [
    {"seconds": 6}, {"seconds": float("nan")}, {"steps": 20}, {"cfg": 5}, {"seed": True},
    {"seed": -1}, {"video": "unsupported.mp4"}, {"prompt": ""}, {"negative": []},
])
def test_unmeasured_sound_settings_are_refused(change):
    with pytest.raises(ValueError):
        audio.parameters({"prompt": "Steady rain.", **change})


def test_the_recipe_keeps_user_words_and_zero_seed():
    result = audio.parameters({"prompt": "  A door slams.  ", "negative": "music", "seed": 0})
    assert result == {"prompt": "  A door slams.  ", "negative": "music", "seed": 0,
                      "seconds": 5, "steps": 100, "cfg": 7.0}


def test_windows_sound_ui_state_and_queue_refuse_foley_and_longer_effects(ready, monkeypatch):
    monkeypatch.setattr(sound.shutil, "which", lambda *args, **kwargs: "ffmpeg")
    state = ready.get("/api/sound/state").json()
    assert state["windows"] and state["seconds"]["sfx"] == [5, 5]
    assert state["kinds"]["sfx"]["ready"] and not state["kinds"]["foley"]["ready"]
    response = ready.post("/api/jobs", json={"kind": "sfx", "params": {"prompt": "Rain.", "seed": 0}})
    assert response.status_code == 200 and response.json()["params"]["seed"] == 0
    assert ready.post("/api/jobs", json={"kind": "sfx", "params": {"prompt": "Rain.", "seconds": 10}}).status_code == 400
    assert ready.post("/api/jobs", json={"kind": "foley", "params": {"video": "clip.mp4"}}).status_code == 501
    assert ready.post("/api/windows/stills/unload", json={}).status_code == 409


def test_sound_takes_are_published_only_after_success_and_listed_in_library(ready, monkeypatch):
    response = ready.post("/api/jobs", json={"kind": "sfx", "params": {"prompt": "Rain.", "seed": 7}})
    job = server.jobs[response.json()["id"]]
    def render(job, home):
        path = home / "sound" / "sfx" / job["id"] / "sound.wav"
        path.parent.mkdir(parents=True)
        with wave.open(str(path), "wb") as target:
            target.setnchannels(2)
            target.setsampwidth(2)
            target.setframerate(44100)
            target.writeframes(b"\0\0\0\0" * 100)
        return {"backend_reused": False, "request_to_files_seconds": .1, "audio": {"seconds": 5.0155}}
    monkeypatch.setattr(image, "render_job", render)
    monkeypatch.setattr(server.hub, "record", lambda *args: None)
    monkeypatch.setattr(server.voice, "release", lambda *args: False)
    monkeypatch.setattr(server.music, "release", lambda *args: False)
    monkeypatch.setattr(server.bolt, "admit", lambda *args: "")
    job.update(status="running", started=time.time())
    server.perform_job(job)
    assert job["status"] == "done", job["error"]
    take = next(t for t in ready.get("/api/sound/takes").json() if t["id"] == job["id"])
    assert take["seed"] == 7 and take["path"] == job["outputs"][0]
    assert ready.get("/files/" + take["path"]).status_code == 200
    listed = next(m for m in ready.get("/api/library").json() if m["path"] == take["path"])
    assert listed["kind"] == "audio"


def test_model_switch_drains_old_backend_before_loading_the_next(ready, monkeypatch, tmp_path):
    events = []
    class Backend:
        def __init__(self, *args):
            self.base, self.directory = "owned", tmp_path
            self.process = self.monitor = None
            self.loaded = False
        def start(self):
            events.append("start")
        def check(self):
            pass
        def close(self):
            events.append("close")
    monkeypatch.setattr(image, "WindowsComfy", Backend)
    monkeypatch.setattr(image, "tools", lambda: SimpleNamespace(checker=lambda: None))
    for route, label in ((image, "image"), (audio, "sound")):
        monkeypatch.setattr(route, "verify_weights", lambda *args, kind=label: events.append("verify-" + kind))
        monkeypatch.setattr(route, "render_case", lambda *args: SimpleNamespace(
            prepare=lambda *args: None, exercise=lambda *args: {"request_to_files_seconds": .1},
        ))
    for index, kind in enumerate(("scout", "sfx", "sfx", "scout")):
        result = image.render_job({"id": str(index), "kind": kind, "status": "running", "params": {"prompt": "a"}}, tmp_path)
        assert result["backend_reused"] == (index == 2)
    assert events == ["verify-image", "start", "close", "verify-sound", "start", "close", "verify-image", "start"]


def test_model_switch_waits_for_a_transient_windows_log_lock(monkeypatch):
    from windows_comfy import WindowsComfy
    backend = WindowsComfy(None, None)
    calls = []
    def cleanup():
        calls.append("cleanup")
        if len(calls) == 1:
            error = OSError("the exited backend log is still being released")
            error.winerror = 32
            raise error
    backend.temporary = SimpleNamespace(cleanup=cleanup)
    backend.close()
    assert calls == ["cleanup", "cleanup"] and backend.temporary is None


def test_model_switch_does_not_hide_unrelated_cleanup_failures():
    from windows_comfy import WindowsComfy
    backend = WindowsComfy(None, None)
    def cleanup():
        raise PermissionError("not a transient sharing violation")
    backend.temporary = SimpleNamespace(cleanup=cleanup)
    with pytest.raises(PermissionError):
        backend.close()


def test_the_sound_page_uses_measured_limits_without_linux_install_commands():
    from test_shell_page import run
    out = run("""
      S.ref_media = false;
      answers["/api/sound/state"] = { windows: true, spark: false, seconds: { sfx: [5,5], foley: [1,15] },
        kinds: { sfx: { ready: true, licence: "Stability AI Community License" }, foley: { ready: false, why: "Foley is unqualified" } } };
      answers["/api/sound/takes"] = [];
      await sfxLoad(); const page = viewFoley(); out.text = text(page); out.mode = SFX.mode; out.seconds = SFX.seconds;
      out.duration = find(page, n => n.tag === "input" && n.attrs.max === 5).attrs.disabled;
      SFX.prompt = "Rain."; SFX.seed = 0; await sfxMake();
      out.params = calls.find(c => c[0] === "/api/jobs")[1].params;
      SFX.mode = "foley"; out.foley = text(viewFoley());
    """)
    assert out["mode"] == "sfx" and out["seconds"] == 5 and out["duration"]
    assert out["params"]["seconds"] == 5 and out["params"]["seed"] == 0
    assert "bash scripts/setup-sound.sh" not in out["foley"]
