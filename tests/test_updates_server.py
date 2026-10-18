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


def test_an_open_event_stream_does_not_hold_a_restart():
    """A browser keeps /api/events open for as long as the page is up. A restart for an update must end such streams
    itself: before this, a graceful shutdown waited on them for ever and the update was never applied."""
    import http.client
    import socket
    import time

    import telemetry
    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    web = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error", timeout_graceful_shutdown=10))
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    telemetry.STOPPING.clear()
    connection = None
    try:
        deadline = time.time() + 10
        while not web.started and time.time() < deadline:
            time.sleep(0.05)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request("GET", "/api/events")
        answer = connection.getresponse()
        seen = b""
        while b"event: telemetry" not in seen:
            seen += answer.read1(65536)
        # the stream stays open (no close): the app is asked to restart exactly as the updater asks server.py to
        telemetry.STOPPING.set()
        web.should_exit = True
        thread.join(8)
        assert not thread.is_alive(), "the open event stream held the shutdown"
    finally:
        telemetry.STOPPING.clear()
        web.should_exit = True
        if connection is not None:
            connection.close()
        thread.join(10)
