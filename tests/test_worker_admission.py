"""Queue admission uses the selected card's guard, not the head card's free VRAM."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import sparks


def card_guard(available_mb, frames=22):
    return server.memory_guard.check(864, 480, frames, available_mb=available_mb,
                                     total_mb=98304, card=True)


@contextlib.contextmanager
def estimate_peer(available_mb):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/api/estimate":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                body = server.numbers(body, ("seconds", "references"))
                room = server.memory_guard.check(864, 480, body["frames"], references=bool(body.get("referenced")),
                                                 pictures=int(body.get("references") or 0),
                                                 available_mb=available_mb, total_mb=98304, card=True)
                status, result = 200, {"seconds": None, "memory": room}
            except server.HTTPException as error:
                status, result = error.status_code, {"detail": error.detail}
            response = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    peer = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(peer.server_port)
    finally:
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=3)


@pytest.fixture
def isolated_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.setattr(server.wake, "set", lambda: None)
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(server, "order", [])
    monkeypatch.setattr(server, "SPARK", True)
    # The simulated worker has its own host RAM, not this machine's changing model residency.
    monkeypatch.setattr(server.memory_guard, "meminfo", lambda: {"MemTotal": 128000, "MemAvailable": 128000})
    info = {"guard": True, "code": {"VERSION": "test"}, "version": "0.0.0", "active": 0,
            "fasth3": True, "ref2va": True, "ffmpeg": True, "loras": [], "text_encoders": [],
            "presets": ["small"], "qualities": ["fh8"]}
    hub = sparks.Hub(tmp_path / "studio", server.PACK, True, server.jobs, server.order,
                     server.lock, server.wake, lambda: dict(info), lambda p: tmp_path / p,
                     server.perform)
    hub.name = "Test head"
    hub.mine = info["code"]
    address = "worker@example.invalid"
    hub.keep({"workers": [{"address": address, "name": "Test worker", "enabled": True}], "sparks": 1})
    hub.state(address).update(state="ready", info=dict(info))
    monkeypatch.setattr(server, "hub", hub)
    yield TestClient(server.app), hub, address


def video(placement=None):
    return {"kind": "video", "sparks": 1, "placement": placement,
            "params": {"prompt": "A paper boat on still water", "preset": "small", "quality": "fh8",
                       "frames": 22, "shot_check": False}}


@pytest.mark.parametrize("policy", ["require", "prefer"])
def test_selected_fitting_card_admits_a_clip_that_the_head_cannot_fit(isolated_queue, monkeypatch, policy):
    client, hub, address = isolated_queue
    monkeypatch.setattr(server, "memory_of", lambda *a, **kw: card_guard(1000))
    assert client.post("/api/jobs", json=video()).status_code == 400
    with estimate_peer(98304) as base:
        monkeypatch.setattr(hub, "connect", lambda _: base)
        queued = client.post("/api/jobs", json=video({"worker": address, "policy": policy}))
    assert queued.status_code == 200, queued.text
    clip = server.jobs[queued.json()["id"]]
    assert hub.claim() is None
    assert hub.claim(address) is clip
    assert clip["remote"] == address


def test_required_card_refusal_is_not_overridden_by_a_roomy_head(isolated_queue, monkeypatch):
    client, hub, address = isolated_queue
    monkeypatch.setattr(server, "memory_of", lambda *a, **kw: card_guard(98304))
    with estimate_peer(1000) as base:
        monkeypatch.setattr(hub, "connect", lambda _: base)
        rejected = client.post("/api/jobs", json=video({"worker": address, "policy": "require"}))
        fallback = client.post("/api/jobs", json=video({"worker": address, "policy": "prefer"}))
    assert rejected.status_code == 400
    assert fallback.status_code == 200, fallback.text
    assert len(server.jobs) == 1


def test_required_stopped_worker_waits_without_using_the_head(isolated_queue, monkeypatch):
    client, hub, address = isolated_queue
    hub.state(address).update(state="stopped")
    monkeypatch.setattr(server, "memory_of", lambda *a, **kw: card_guard(1000))
    queued = client.post("/api/jobs", json=video({"worker": address, "policy": "require"}))
    assert queued.status_code == 200, queued.text
    assert hub.claim() is None
    assert hub.claim(address) is None
    assert server.jobs[queued.json()["id"]]["status"] == "queued"


def test_required_reference_clip_is_estimated_before_media_upload(isolated_queue, monkeypatch, tmp_path):
    client, hub, address = isolated_queue
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "ref2va_ready", lambda: True)
    monkeypatch.setattr(server, "memory_of", lambda *a, **kw: card_guard(1000))
    reference = tmp_path / "reference.png"
    from PIL import Image
    Image.new("RGB", (64, 64), "blue").save(reference)
    body = video({"worker": address, "policy": "require"})
    body["params"]["references"] = [reference.name]
    with estimate_peer(98304) as base:
        monkeypatch.setattr(hub, "connect", lambda _: base)
        queued = client.post("/api/jobs", json=body)
    assert queued.status_code == 200, queued.text
    clip = server.jobs[queued.json()["id"]]
    assert hub.claim() is None
    assert hub.claim(address) is clip


def test_a_dispatched_clip_does_not_inherit_the_worker_studios_video_default(isolated_queue, monkeypatch, tmp_path):
    client, worker_hub, other = isolated_queue
    config = worker_hub.config()
    config["placement"] = {"worker": other, "policy": "require"}
    worker_hub.keep(config)
    worker_hub.state(other).update(state="stopped")
    monkeypatch.setattr(server, "memory_of", lambda *a, **kw: card_guard(98304))
    monkeypatch.setattr(sparks, "POLL", 0.01)
    seen, accepted = {}, threading.Event()

    class WorkerAPI(BaseHTTPRequestHandler):
        def dispatch(self):
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            answer = client.request(self.command, self.path, content=data,
                                    headers={"Content-Type": "application/json"})
            payload = answer.json()
            if self.path == "/api/estimate":
                seen["estimate"] = payload
            if self.path == "/api/jobs":
                seen["queued"] = payload
                accepted.set()
            response = answer.content
            self.send_response(answer.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        do_POST = dispatch
        do_GET = dispatch

        def log_message(self, *args):
            pass

    peer = ThreadingHTTPServer(("127.0.0.1", 0), WorkerAPI)
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    parent = sparks.Hub(tmp_path / "coordinator", server.PACK, True, {}, [], threading.RLock(),
                        threading.Event(), lambda: {"platform": "spark"}, lambda p: tmp_path / p,
                        lambda job, worker: None)
    parent.name = "Test coordinator"
    monkeypatch.setattr(parent, "connect", lambda _: "http://127.0.0.1:" + str(peer.server_port))
    clip = {"id": "dispatched", "kind": "video", "title": "A paper boat", "params": video()["params"],
            "status": "running", "log": "", "placement": {"worker": "child@example.invalid", "policy": "require"}}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(parent.render, clip, {"address": "child@example.invalid", "name": "Test worker"},
                                 clip["params"], tmp_path / "clip.mp4")
            try:
                assert accepted.wait(5), "the worker did not receive the clip"
                assert seen["estimate"]["memory"]["fits"] is True
                queued = seen["queued"]
                assert queued["placement"] == {"worker": "head", "policy": "require"}
                assert queued["status"] == "queued"
            finally:
                clip["status"] = "cancelled"
            assert result.result(timeout=5) is False
            assert server.jobs[queued["id"]]["status"] == "cancelled"
    finally:
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=3)


def test_fleet_shrink_clamps_the_saved_count_but_not_an_explicit_invalid_count(isolated_queue):
    client, hub, _ = isolated_queue
    config = hub.config()
    config["sparks"] = 8
    hub.keep(config)
    body = video()
    body.pop("sparks")
    queued = client.post("/api/jobs", json=body)
    assert queued.status_code == 200, queued.text
    assert queued.json()["sparks"]["chosen"] == hub.chooser_limit()
    assert client.post("/api/jobs", json={**body, "sparks": 8}).status_code == 400
