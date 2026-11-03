"""Windows still admission and persistent storyboard links; no model/GPU probes."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from test_windows_ui import client, server  # noqa: F401
import suite
import windows_storyboard as image


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(image, "status", lambda: {
        "available": True, "model": image.MODEL, "width": 512, "height": 512, "max_count": 6,
    })
    checked = []
    monkeypatch.setattr(image, "preflight", lambda: checked.append(True))
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(server, "order", [])
    monkeypatch.setattr(server, "wake", SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(server, "running", {"process": None, "id": None})
    return client, checked


@pytest.mark.parametrize("change", [
    {"width": 768}, {"height": 768}, {"count": 7}, {"count": 0}, {"seed": -1},
    {"image_model": "qwen"}, {"loras": []}, {"count": "1"},
])
def test_unmeasured_still_settings_are_refused(change):
    with pytest.raises(ValueError):
        image.parameters({"prompt": "An empty hall.", **change})


def test_windows_qualified_stills_are_queued_and_internal_other_jobs_stay_closed(enabled):
    client, checked = enabled
    response = client.post("/api/jobs", json={"kind": "scout", "params": {"prompt": "An empty hall."}})
    assert response.status_code == 200, response.text
    assert response.json()["params"] == {
        "prompt": "An empty hall.", "count": 1, "seed": 1, "width": 512, "height": 512, "image_model": image.MODEL,
    }
    assert checked
    state = client.get("/api/state").json()
    assert state["windows_stills"]["available"] and state["image_models"][0]["id"] == image.MODEL
    assert not state["ref2va"] and not state["music"]["available"]
    with pytest.raises(server.HTTPException) as error:
        server.enqueue("voice", "Unqualified", {})
    assert error.value.status_code == 501


def test_bad_origins_and_unqualified_job_kinds_cannot_bypass_admission(enabled):
    client, checked = enabled
    response = client.post("/api/jobs", json={"kind": "scout", "params": {
        "prompt": "Hall.", "suite_import": "../elsewhere", "suite_shot": "x",
    }})
    assert response.status_code == 400 and not checked
    assert client.post("/api/jobs", json={"kind": "video", "params": {"prompt": "Hall."}}).status_code == 501


def test_an_active_windows_job_is_stopped_only_by_its_private_handle(enabled):
    client, _ = enabled
    job = client.post("/api/jobs", json={"kind": "scout", "params": {"prompt": "Hall."}}).json()
    calls = []
    owner = SimpleNamespace(terminate=lambda: calls.append("owned"))
    process = SimpleNamespace(_studio_job=owner)
    server.running.update(id=job["id"], process=process, owned_job=owner)
    response = client.post(f"/api/jobs/{job['id']}/cancel", json={})
    assert response.json()["status"] == "cancelled" and calls == ["owned"]


def test_missing_ownership_does_not_claim_an_active_job_was_cancelled(enabled):
    client, _ = enabled
    job = client.post("/api/jobs", json={"kind": "scout", "params": {"prompt": "Hall."}}).json()
    server.running.update(id=job["id"], process=SimpleNamespace(), owned_job=None)
    response = client.post(f"/api/jobs/{job['id']}/cancel", json={})
    assert response.status_code == 409
    assert server.jobs[job["id"]]["status"] == "queued"


def test_generated_boards_survive_import_reload_and_slate_conversion(enabled):
    client, _ = enabled
    response = client.post("/api/suite/import", files={"file": (
        "board.fountain", b"Title: Board\n\nINT. HALL - DAY\n\nA closed door catches the daylight.\n",
    )})
    imported = response.json()
    path = server.HOME / "scouts" / "test-board" / "scout_s1.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "green").save(path)
    relative = path.relative_to(server.HOME).as_posix()
    suite.keep_storyboard(server.HOME, imported["id"], imported["shots"][0]["id"], relative)
    refreshed = client.get(f"/api/suite/imports/{imported['id']}").json()
    assert refreshed["shots"][0]["image"] == relative
    made = client.post(f"/api/suite/imports/{imported['id']}/slate", json={}).json()
    project = client.get("/api/slate/projects/" + made["project"]).json()
    shot = project["scenes"][0]["shots"][0]
    assert shot["tf"]["image"] == relative and shot["tf"]["start"] == "image"
    assert "closed door" in shot["prompt"]


def test_generated_candidates_do_not_replace_manual_board_images(tmp_path):
    folder = tmp_path / "import"
    folder.mkdir()
    Image.new("RGB", (16, 16)).save(tmp_path / "generated.png")
    (folder / "storyboards.json").write_text(json.dumps({"s1": "generated.png"}))
    found = {"shots": [{"id": "s1", "image": "manual.png"}]}
    assert suite.with_storyboards(folder, found, tmp_path)["shots"][0]["image"] == "manual.png"


def test_the_page_queues_bounded_missing_stills_and_preserves_existing_images():
    from test_slate_page import run
    root = Path(__file__).resolve().parents[1]
    out = run("""
      const index = fs.readFileSync(%s, "utf8");
      vm.runInThisContext("const stillParams = " + index.split("const stillParams = ")[1].split("\\nconst imageModelName")[0]);
      vm.runInThisContext(fs.readFileSync(%s, "utf8"));
      S.ui_only = true; S.windows_stills = { available: true, model: "sd-turbo", width: 512, height: 512 };
      globalThis.imageModel = () => "qwen"; globalThis.poll = async () => {}; globalThis.bayOpen = () => {};
      globalThis.count = (n, word) => n + " " + word;
      const imp = { id: "i1", shots: [{ id: "s1", scene: "Hall", action: "Daylight on a door." },
                                     { id: "s2", action: "Keep my reference.", image: "uploads/manual.png" }] };
      await stillsForAll(imp);
      out.queued = calls.filter(c => c[0] === "/api/jobs"); out.manual = imp.shots[1].image;
    """ % (json.dumps(str(root / "app/static/index.html")), json.dumps(str(root / "app/static/suite.js"))))
    assert len(out["queued"]) == 1 and out["manual"] == "uploads/manual.png"
    params = out["queued"][0][1]["params"]
    assert params["width"] == params["height"] == 512 and params["image_model"] == image.MODEL
    assert params["suite_import"] == "i1" and params["suite_shot"] == "s1"


def test_first_ad_shots_keep_changed_manual_frames_and_pin_completed_candidates():
    from test_slate_page import run
    root = Path(__file__).resolve().parents[1]
    out = run("""
      const index = fs.readFileSync(%s, "utf8");
      vm.runInThisContext("const stillParams = " + index.split("const stillParams = ")[1].split("\\nconst imageModelName")[0]);
      S.windows_stills = { available: true, model: "sd-turbo", width: 512, height: 512 };
      globalThis.poll = async () => {};
      SL.p = film();
      slApply([{ type: "create_scene", name: "Hall" }, { type: "create_shot", scene: "Hall",
        name: "Wide", prompt: "# Subject\\nDaylight on a door.", intent: "Do not put this crew note in the prompt." }]);
      const shot = SL.p.scenes[0].shots[0], tf = slTf(shot); tf.image = "uploads/old.png";
      const make = () => slStoryboard(shot).kids.find(n => n && n.tag === "button");
      answers["/api/jobs"] = { id: "b1" };
      await make().attrs.onclick();
      out.params = calls.find(c => c[0] === "/api/jobs")[1].params;
      tf.image = "uploads/changed.png";
      jobs.push({ id: "b1", status: "done", outputs: ["scouts/b1/scout_s1.png", "scouts/b1/sheet.jpg"] });
      slStoryboard(shot); out.manual = tf.image; out.candidates = [...shot.images];
      answers["/api/jobs"] = { id: "b2" };
      await make().attrs.onclick();
      jobs.push({ id: "b2", status: "done", outputs: ["scouts/b2/scout_s1.png"] });
      slStoryboard(shot); out.pinned = tf.image; out.start = tf.start;
    """ % json.dumps(str(root / "app/static/index.html")))
    assert out["params"]["prompt"] == "# Subject\nDaylight on a door."
    assert out["params"]["width"] == out["params"]["height"] == 512
    assert out["manual"] == "uploads/changed.png"
    assert out["candidates"] == ["scouts/b1/scout_s1.png"]
    assert out["pinned"] == "scouts/b2/scout_s1.png" and out["start"] == "image"
