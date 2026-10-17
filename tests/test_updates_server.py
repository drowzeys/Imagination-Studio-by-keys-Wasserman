"""Admission and idle checks at the actual Studio boundary."""
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "studio"))
sys.path.insert(0, str(ROOT / "app"))
import server


def test_restarting_studio_keeps_reads_available_but_refuses_new_writes():
    client = TestClient(server.app)
    server.updater.maintenance.set()
    try:
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/updates").status_code == 200
        assert client.post("/api/projects", json={"title": "wait"}).status_code == 503
        assert client.post("/api/jobs", json={"kind": "video", "params": {}}).status_code == 503
        # Internal voice/music queue entry points also share the maintenance gate.
        with pytest.raises(HTTPException) as error:
            server.enqueue("voice", "wait", {})
        assert error.value.status_code == 503
    finally:
        server.updater.maintenance.clear()


def test_an_inflight_save_prevents_an_update(monkeypatch, tmp_path):
    started, finish = threading.Event(), threading.Event()
    monkeypatch.setenv("STUDIO_GPU_LOCK", str(tmp_path / "no-engine-lock"))
    client = TestClient(server.app)

    def save():
        started.set()
        finish.wait(timeout=5)
        return {"saved": True}

    server.app.add_api_route("/api/update-test-save", save, methods=["POST"])
    route = server.app.routes[-1]
    # StaticFiles is the catch-all last route; put this temporary test endpoint before it.
    server.app.routes.remove(route)
    server.app.routes.insert(0, route)
    thread = threading.Thread(target=lambda: client.post("/api/update-test-save", json={}))
    thread.start()
    try:
        assert started.wait(timeout=3)
        with server.lock:
            assert server.update_busy()
    finally:
        finish.set()
        thread.join(timeout=5)
        server.app.routes.remove(route)
    assert not thread.is_alive()
    with server.lock:
        assert not server.update_busy()


def test_worker_management_and_resident_engine_prevent_updates(monkeypatch, tmp_path):
    gpu = tmp_path / "engine.lock"
    monkeypatch.setenv("STUDIO_GPU_LOCK", str(gpu))
    monkeypatch.setattr(server.hub, "live", {"worker": {"working": "updating and starting", "job": None}})
    with server.lock:
        assert server.update_busy()
    server.hub.live["worker"]["working"] = ""
    gpu.mkdir()
    with server.lock:
        assert server.update_busy()
    gpu.rmdir()
    with server.lock:
        assert not server.update_busy()
