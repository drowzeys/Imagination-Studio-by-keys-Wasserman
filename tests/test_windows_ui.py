"""The Windows preview plans and saves without pretending it can render."""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import agents
import telemetry


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "WINDOWS", True)
    monkeypatch.setattr(agents, "WINDOWS", True)
    monkeypatch.setattr(server, "SPARK", False)
    return TestClient(server.app, base_url="http://127.0.0.1")


def test_windows_state_is_not_a_mac_or_a_render_backend(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    state = response.json()
    assert state["platform"] == "windows" and state["ui_only"]
    assert state["platform_label"] == "NATIVE WINDOWS"
    assert state["platform_note"] == ""
    assert state["image_models"] == [] and state["text_encoders"] == []
    assert not any(state[key] for key in ("zig", "turbo", "x2", "ref2va", "ref2va_turbo", "ref_media"))
    assert not state["voice"]["available"] and not state["music"]["available"]
    assert not state["sparks"]["supported"]
    assert server.estimate("small", "fh4", 5) is None


def test_windows_keeps_upstream_paired_gpu_and_hdr_features_unqualified(client, monkeypatch):
    monkeypatch.setenv("STUDIO_GPUS", "0,1")
    monkeypatch.setenv("STUDIO_GPU", "0")
    monkeypatch.setattr(server.memory_guard, "paired_gpus", lambda: pytest.fail("Windows must not advertise paired GPUs"))
    state = client.get("/api/state").json()
    assert state["gpu_index"] == "0" and state["ui_only"]
    assert not state["capabilities"]["flux"]["available"] and not state["capabilities"]["hlg"]["available"]
    assert client.post("/api/jobs", json={"kind": "hlg", "params": {"video": "uploads/unqualified.mp4"}}).status_code == 501
    with pytest.raises(server.HTTPException) as error:
        server.enqueue("hlg", "Unqualified", {})
    assert error.value.status_code == 501
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", False)
    monkeypatch.setattr(server.memory_guard, "paired_gpus", lambda: ["0", "1"])
    assert client.get("/api/state").json()["gpu_index"] == "0,1"


@pytest.mark.parametrize("path", [
    "/api/bolt/load", "/api/nodes/run", "/api/discover/scan",
    "/api/voice/takes", "/api/music/takes",
    "/api/sparks", "/api/updates/apply",
    "/api/motion/analyse", "/api/slate/render",
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
    assert client.post("/api/jobs", json={}).status_code == 400
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


def test_windows_script_and_manual_board_save_reload_and_export(client):
    before = dict(server.jobs)
    text = "Title: Windows board\n\nINT. HALL - DAY\n\nMARA waits.\n\nMARA\nNow.\n"
    response = client.post("/api/suite/import", files={"file": ("plan.fountain", text.encode())})
    assert response.status_code == 200, response.text
    imported = response.json()
    assert imported["app"] == "fountain" and len(imported["shots"]) == 1
    made = client.post(f"/api/suite/imports/{imported['id']}/slate", json={})
    assert made.status_code == 200, made.text
    url = "/api/slate/projects/" + made.json()["project"]
    project = client.get(url).json()
    shot = project["scenes"][0]["shots"][0]
    character = project["characters"][0]
    assert shot["tf"]["cast"] == [character["id"]]
    assert shot["tf"]["lines"][0]["characterId"] == character["id"]
    assert shot["tf"]["lines"][0]["text"] == "Now."
    shot["intent"] = "Keep this planning note out of the prompt."
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(image, format="PNG")
    uploaded = client.post("/api/upload", files={"file": ("board.png", image.getvalue(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    path = uploaded.json()["path"]
    shot["tf"].update(start="image", image=path)
    character["images"] = [path]
    project["references"] = [{"id": "ref-board", "name": "Manual board", "mediaPath": path}]
    assert client.put(url, json=project).status_code == 200
    saved = client.get(url).json()
    assert saved["scenes"] == project["scenes"]
    assert saved["characters"] == project["characters"]
    assert saved["references"] == project["references"]
    assert client.get("/files/" + path).content == image.getvalue()
    compiled = client.post("/api/slate/compile", json={"projectId": saved["id"], "shotId": shot["id"]})
    assert compiled.status_code == 200, compiled.text
    assert "Now." in compiled.json()["prompt"]
    assert shot["intent"] not in compiled.json()["prompt"]
    assert compiled.json()["plan"]["image"] == path
    prompts = client.get(f"/api/suite/imports/{imported['id']}/prompts").json()
    assert len(prompts) == 1 and "Now." in prompts[0]["prompt"]
    # The page downloads this JSON; importing it again must preserve the film's internal links.
    exported = json.dumps(saved).encode()
    reimported = client.post("/api/slate/projects", json={"project": json.loads(exported)})
    assert reimported.status_code == 200, reimported.text
    clone = client.get("/api/slate/projects/" + reimported.json()["id"]).json()
    assert clone["id"] != saved["id"]
    assert clone["scenes"] == saved["scenes"] and clone["characters"] == saved["characters"]
    assert clone["references"] == saved["references"]
    assert server.jobs == before


def test_windows_storyboard_zip_attaches_existing_and_new_shots(client):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(image, format="PNG")
    data = io.BytesIO()
    board = {"project": "Manual storyboard", "frames": [
        {"index": 1, "folder": "01_wide", "label": "Wide", "prompt": "A quiet hall.", "notes": "Establish the place."},
        {"index": 2, "folder": "02_close", "label": "Close", "prompt": "A hand on the door."},
    ]}
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("board/prompts.json", json.dumps(board))
        bundle.writestr("board\\01_wide\\still.png", image.getvalue())
        bundle.writestr("board/02_close/still.png", image.getvalue())
    imported = client.post("/api/suite/import", files={"file": ("board.zip", data.getvalue())})
    assert imported.status_code == 200, imported.text
    found = imported.json()
    assert found["app"] == "storyboard-reference" and not found["warnings"]
    prompts = client.get(f"/api/suite/imports/{found['id']}/prompts").json()
    assert len(prompts) == 2
    assert all("\\" not in one["image"] for one in prompts)
    assert all(client.get("/files/" + one["image"]).content == image.getvalue() for one in prompts)
    made = client.post(f"/api/suite/imports/{found['id']}/slate", json={})
    assert made.status_code == 200, made.text
    url = "/api/slate/projects/" + made.json()["project"]
    film = client.get(url).json()
    first = film["scenes"][0]["shots"][0]
    assert first["tf"]["image"] == prompts[0]["image"] and first["tf"]["start"] == "image"
    first.update(prompt="# Subject\nMy own unchanged words.", intent="My own unchanged intent.")
    assert client.put(url, json=film).status_code == 200
    attached = client.post(f"/api/suite/imports/{found['id']}/attach", json={
        "film": film["id"], "mapping": [
            {"shot": "s001", "to": first["id"]},
            {"shot": "s002", "to": "new", "scene": film["scenes"][0]["id"]},
        ]})
    assert attached.status_code == 200, attached.text
    kept = client.get(url).json()["scenes"][0]["shots"]
    assert len(kept) == 3 and kept[0]["id"] == first["id"]
    assert kept[0]["prompt"] == first["prompt"] and kept[0]["intent"] == first["intent"]
    assert kept[-1]["name"] == "Close" and "A hand on the door." in kept[-1]["prompt"]
    for one in (kept[0], kept[-1]):
        path = one["tf"]["image"]
        assert path.startswith(f"slate/{film['id']}/suite/") and "\\" not in path
        assert client.get("/files/" + path).content == image.getvalue()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows ZIP filename rules")
@pytest.mark.parametrize("name", [
    "CON.png", "dir/NUL.txt", "COM1.json", "LPT1.png", "still.png:stream", "dir./still.png", "dir/still.png ",
])
def test_windows_reserved_zip_names_are_refused(client, name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr(name, b"not an allowed file")
    before = set((server.HOME / "uploads" / "suite").glob("*"))
    response = client.post("/api/suite/import", files={"file": ("invalid.zip", data.getvalue())})
    assert response.status_code == 400 and "invalid name" in response.json()["error"]
    assert set((server.HOME / "uploads" / "suite").glob("*")) == before


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
