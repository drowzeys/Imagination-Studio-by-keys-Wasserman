"""Managed local workers keep their control and files private even on loopback."""
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server


@pytest.fixture
def private_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LOCAL_GPU_CHILD", True)
    monkeypatch.setattr(server, "LOCAL_GPU_KEY", "test-private-worker-key")
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setenv("STUDIO_ACCESS_KEY", "off")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "private.png").write_bytes(b"private worker media")
    return TestClient(server.app)


@pytest.mark.parametrize("path", ["/api/state", "/api/spark/self", "/api/key", "/files/uploads/private.png", "/"])
def test_loopback_and_disabled_public_access_checks_do_not_bypass_worker_key(private_worker, path):
    denied = private_worker.get(path)
    assert denied.status_code == 403
    assert "test-private-worker-key" not in denied.text
    assert private_worker.get(path, headers={"X-Studio-Key": "wrong-key"}).status_code == 403


def test_only_the_private_header_key_can_read_worker_media(private_worker):
    path = "/files/uploads/private.png"
    assert private_worker.get(path + "?key=test-private-worker-key").status_code == 403
    assert private_worker.get(path, headers={"X-Studio-Key": server.ACCESS_KEY}).status_code == 403
    allowed = private_worker.get(path, headers={"X-Studio-Key": "test-private-worker-key"})
    assert allowed.status_code == 200
    assert allowed.content == b"private worker media"


def test_private_key_does_not_allow_network_clients_or_forwarded_loopback_identity(private_worker):
    foreign = TestClient(server.app, client=("192.0.2.1", 50000))
    answer = foreign.get("/api/state", headers={"X-Studio-Key": "test-private-worker-key",
                                               "X-Forwarded-For": "127.0.0.1"})
    assert answer.status_code == 403


def test_websocket_cannot_bypass_the_private_worker_boundary(private_worker):
    with pytest.raises(WebSocketDisconnect) as denied:
        with private_worker.websocket_connect("/api/state"):
            pytest.fail("an unauthenticated worker websocket was accepted")
    assert denied.value.code == 4403


@pytest.mark.parametrize("readings_fail", [False, True])
def test_private_self_keeps_gpu_uuid_with_status_or_failed_readings(private_worker, monkeypatch, readings_fail):
    ident = "GPU-00000000-0000-0000-0000-000000000002"
    monkeypatch.setenv("STUDIO_LOCAL_GPU_UUID", ident)
    snapshot = {"synthetic": True}
    status = {"gpu": {"name": "Synthetic GPU", "temp_c": 41}, "job": None}

    def readings():
        if readings_fail:
            raise OSError("synthetic telemetry failure")
        return snapshot

    def compact(found):
        assert found is snapshot
        return status

    monkeypatch.setattr(server.TELEMETRY, "snapshot", readings)
    monkeypatch.setattr(server.telemetry, "compact", compact)
    response = private_worker.get("/api/spark/self", headers={"X-Studio-Key": "test-private-worker-key"})
    assert response.status_code == 200
    info = response.json()
    assert info["gpu_uuid"] == ident
    assert info["status"] == (None if readings_fail else status)
