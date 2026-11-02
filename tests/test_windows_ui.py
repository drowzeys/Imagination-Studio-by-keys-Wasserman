"""The Windows preview plans and saves without pretending it can render."""
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import telemetry


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "WINDOWS", True)
    monkeypatch.setattr(server, "SPARK", False)
    return TestClient(server.app, base_url="http://127.0.0.1")


def test_windows_state_is_not_a_mac_or_a_render_backend(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    state = response.json()
    assert state["platform"] == "windows" and state["ui_only"]
    assert "UI-ONLY" in state["platform_label"]
    assert state["platform_note"] == server.WINDOWS_NOTE
    assert state["image_models"] == [] and state["text_encoders"] == []
    assert not any(state[key] for key in ("zig", "turbo", "x2", "ref2va", "ref2va_turbo", "ref_media"))
    assert not state["voice"]["available"] and not state["music"]["available"]
    assert not state["sparks"]["supported"]
    assert server.estimate("small", "fh4", 5) is None


@pytest.mark.parametrize("path", [
    "/api/jobs", "/api/bolt/load", "/api/nodes/run", "/api/discover/scan",
    "/api/slate/brain", "/api/voice/takes", "/api/music/takes",
    "/api/sparks", "/api/updates/apply",
])
def test_unverified_actions_are_refused_before_any_work(client, path):
    before = len(server.jobs)
    response = client.post(path, json={})
    assert response.status_code == 501
    assert response.json()["error"] == server.WINDOWS_NOTE
    assert len(server.jobs) == before


def test_internal_queues_cannot_bypass_windows_refusal(client):
    with pytest.raises(server.HTTPException) as error:
        server.enqueue("voice", "test", {})
    assert error.value.status_code == 501
    with pytest.raises(server.HTTPException) as error:
        server.queue_job({"kind": "video"})
    assert error.value.status_code == 501


def test_windows_projects_can_be_saved_and_loaded(client):
    response = client.post("/api/projects", json={"name": "Windows preview test"})
    assert response.status_code == 200
    project = response.json()
    project["name"] = "Saved Windows project"
    saved = client.put("/api/projects/" + project["id"], json=project)
    assert saved.status_code == 200
    assert any(p["id"] == project["id"] and p["name"] == project["name"]
               for p in client.get("/api/projects").json())


def test_windows_settings_uploads_and_static_page(client):
    assert client.post("/api/settings", json={"appearance": {"accent": "amber"}}).status_code == 200
    image = io.BytesIO()
    Image.new("RGB", (16, 16)).save(image, format="PNG")
    response = client.post("/api/upload", files={"file": ("sample.png", image.getvalue(), "image/png")})
    assert response.status_code == 200
    assert response.json()["path"].startswith("uploads/") and "\\" not in response.json()["path"]
    assert client.get("/files/" + response.json()["path"]).content == image.getvalue()
    assert client.get("/").status_code == 200
    assert client.get("/api/telemetry").status_code == 200


def test_non_windows_requests_keep_existing_validation(client, monkeypatch):
    monkeypatch.setattr(server, "WINDOWS", False)
    assert client.post("/api/jobs", json={"kind": "unknown"}).status_code == 400


def test_windows_shutdown_does_not_run_bash(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Windows UI shutdown must not invoke a Unix model launcher")

    monkeypatch.setattr(server.subprocess, "run", forbidden)
    server.stop_session_comfy()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows telemetry")
def test_windows_memory_and_disk_are_real_readings(tmp_path):
    total, available = telemetry.windows_memory()
    assert total > 0 and 0 <= available <= total
    disk = telemetry.disk_of(tmp_path)
    assert disk["total_gb"] > 0 and disk["free_gb"] >= 0
