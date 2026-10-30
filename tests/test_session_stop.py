"""Quitting the running app drains managed work or stops this install's own ComfyUI."""
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ.setdefault("STUDIO_GPU_LOCK", str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import telemetry  # noqa: E402


def test_importing_the_app_does_not_stop_comfy_on_its_own():
    """Only the running app (register_session_stop, in __main__) stops ComfyUI or drains managed work at shutdown."""
    assert server.stop_session_comfy not in server.app.router.on_shutdown
    assert server.stop_local_jobs not in server.app.router.on_shutdown
    assert not [callback for callback in server.app.router.on_shutdown if getattr(callback, "__qualname__", "").startswith("register_session_stop")]


def test_quitting_stops_this_installs_comfy(monkeypatch, capsys):
    calls = []

    def run(cmd, **options):
        calls.append((cmd, options))
        return SimpleNamespace(stdout="stopped\n")

    monkeypatch.setattr(server, "LOCAL_GPU_CHILD", False)
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.subprocess, "run", run)
    server.stop_session_comfy()
    assert calls == [(["bash", str(server.PACK / "spark/comfy.sh"), "stop"],
                      {"check": False, "capture_output": True, "text": True, "timeout": 60})]
    assert "ComfyUI stopped" in capsys.readouterr().out


def test_quitting_when_comfy_is_already_down_says_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(server, "LOCAL_GPU_CHILD", False)
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.subprocess, "run",
                        lambda cmd, **_options: calls.append(cmd) or SimpleNamespace(stdout="not running\n"))
    server.stop_session_comfy()
    assert calls == [["bash", str(server.PACK / "spark/comfy.sh"), "stop"]]
    assert capsys.readouterr().out == ""


def test_a_mac_quits_its_own_comfy(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "LOCAL_GPU_CHILD", False)
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server.subprocess, "run",
                        lambda cmd, **_options: calls.append(cmd) or SimpleNamespace(stdout="not running\n"))
    server.stop_session_comfy()
    assert calls == [["bash", str(server.PACK / "mac/comfy.sh"), "stop"]]


@pytest.mark.parametrize("spark, initially_managed, finally_managed, lease_active", [
    (False, False, False, False),
    (True, False, False, False),
    (True, False, True, True),
    (True, True, False, True),
])
def test_only_the_running_app_registers_the_stop(monkeypatch, tmp_path, spark, initially_managed, finally_managed, lease_active):
    from fastapi.testclient import TestClient

    marker = tmp_path / "comfy-stopped"
    script = tmp_path / "spark/comfy.sh"
    script.parent.mkdir()
    script.write_text('#!/bin/bash\n[ "$1" = stop ] || exit 1\nprintf stopped > "$STUDIO_TEST_STOP_MARKER"\n')
    monkeypatch.setenv("STUDIO_TEST_STOP_MARKER", str(marker))
    monkeypatch.setattr(server, "PACK", tmp_path)
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server, "SPARK", spark)
    monkeypatch.setattr(server, "LOCAL_GPU_CHILD", False)
    unrelated = {server.bolt.shutdown, server.hub.close, server.updater.stop.set}
    imported_callbacks = [callback for callback in server.app.router.on_shutdown if callback not in unrelated]
    monkeypatch.setattr(server.app.router, "on_shutdown", imported_callbacks)
    monkeypatch.setattr(server.app.router, "on_startup", [])
    monkeypatch.setattr(server.hub, "local", SimpleNamespace(managed=initially_managed, active=lease_active, closing=False))
    monkeypatch.setattr(server.hub, "closing", False)
    monkeypatch.setattr(server.hub, "kick", threading.Event())
    monkeypatch.setattr(server, "local_threads", {})
    monkeypatch.setattr(server, "stopping", threading.Event())
    monkeypatch.setattr(server, "wake", threading.Event())
    running = {"id": "running-take", "kind": "video", "status": "running", "log": "", "params": {}, "outputs": []}
    queued = {"id": "queued-director-take", "kind": "video", "status": "queued", "source": "director"}
    monkeypatch.setattr(server, "jobs", {running["id"]: running, queued["id"]: queued})
    monkeypatch.setattr(server, "running", {"id": None, "process": None})
    # No model engine belongs to this test; cancellation and shutdown dispatch stay real.
    monkeypatch.setattr(server.voice, "release", lambda *_: None)
    monkeypatch.setattr(server.music, "release", lambda *_: None)
    with TestClient(server.app):
        pass
    assert not marker.exists() and running["status"] == "running" and queued["status"] == "queued"

    def release_ownership():
        assert marker.read_text() == "stopped"
        assert queued["status"] == "queued"
        assert running["status"] == ("cancelled" if spark and (finally_managed or lease_active) else "running")

    monkeypatch.setattr(server.app.router, "on_shutdown", [release_ownership])
    server.register_session_stop()
    server.hub.local.managed = finally_managed
    with TestClient(server.app):
        pass
    assert marker.read_text() == "stopped"
    assert queued["status"] == "queued"


def test_ctrl_c_stops_admission_and_event_streams_before_the_server_closes(monkeypatch):
    monkeypatch.setattr(server, "stopping", threading.Event())
    monkeypatch.setattr(telemetry, "STOPPING", threading.Event())
    observed = []
    server.begin_shutdown(lambda *_: observed.append((server.stopping.is_set(), telemetry.STOPPING.is_set())),
                          signal.SIGINT, None)
    assert observed == [(True, True)]
    with pytest.raises(server.HTTPException) as rejected:
        server.accepting_jobs()
    assert rejected.value.status_code == 503


def test_ctrl_c_is_not_raised_again_after_the_hooks():
    import os
    seen = []
    with server.catch_exit(lambda sig, _frame: seen.append(sig)):
        os.kill(os.getpid(), signal.SIGINT)
    assert seen == [signal.SIGINT]


