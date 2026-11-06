"""Windows still admission and persistent storyboard links; no model/GPU probes."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from test_windows_ui import client, server  # noqa: F401
import suite
import windows_storyboard as image


def test_still_preflight_adds_gpu_process_diagnostics_only_on_refusal(monkeypatch):
    from contextlib import nullcontext
    import windows_memory

    hardware = {"physical_total_mb": 48000, "host_total_mb": 22000, "gpu_dedicated_total_mb": 26000}
    free = [4512]
    queries = []

    class Memory:
        def read(self):
            return 26000, free[0]

        def processes(self):
            queries.append(True)
            return {"processes": [{"pid": 11, "name": "model-host.exe", "used_mb": None}], "warnings": []}

    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    monkeypatch.setattr(image, "_backend", None)
    monkeypatch.setattr(image, "status", lambda: {"available": True, "hardware": hardware})
    monkeypatch.setattr(windows_memory, "host_memory", lambda: (22000, 12000))
    monkeypatch.setattr(windows_memory, "physical_memory_mb", lambda: 48000)
    monkeypatch.setattr(windows_memory, "WindowsSignals", lambda: nullcontext(
        SimpleNamespace(read=lambda: (10000, 40000, 1000, False))))
    monkeypatch.setattr(windows_memory, "DedicatedMemory", lambda _: nullcontext(Memory()))
    monkeypatch.setattr(windows_memory, "windows_gpu_process_memory", lambda: {11: 24576})
    monkeypatch.setattr(image.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="selected-device"))
    with pytest.raises(RuntimeError, match="4.4 GiB available; 14.0 GiB required") as error:
        image.preflight()
    assert "model-host.exe (PID 11, 24.00 GiB, Windows total across adapters)" in str(error.value)
    assert queries == [True]
    free[0] = 14336
    image.preflight()
    assert queries == [True]


@pytest.mark.parametrize("release", [image.release_backend, image.shutdown_backend])
def test_admission_waits_for_backend_cleanup_instead_of_reading_closed_process(monkeypatch, release):
    from contextlib import nullcontext
    import windows_memory

    hardware = {"physical_total_mb": 48000, "host_total_mb": 22000, "gpu_dedicated_total_mb": 26000}
    closing, finish_close, waiting = threading.Event(), threading.Event(), threading.Event()
    errors = []

    class Lock:
        def __init__(self):
            self.lock = threading.RLock()

        def __enter__(self):
            if threading.current_thread().name == "admission":
                waiting.set()
            if not self.lock.acquire(timeout=3):
                raise RuntimeError("test lock timed out")
            return self

        def __exit__(self, *_):
            self.lock.release()

    class Backend:
        def close(self):
            closing.set()
            if not finish_close.wait(3):
                raise RuntimeError("test cleanup timed out")

        def accounting(self):
            raise RuntimeError("admission inspected the closing backend")

    def run(function):
        try:
            function()
        except Exception as error:
            errors.append(error)

    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    monkeypatch.setattr(image, "_backend_lock", Lock())
    monkeypatch.setattr(image, "_backend", Backend())
    monkeypatch.setattr(image, "_backend_kind", "scout")
    monkeypatch.setattr(image, "_active_job", None)
    monkeypatch.setattr(image, "status", lambda: {"available": True, "hardware": hardware})
    monkeypatch.setattr(windows_memory, "host_memory", lambda: (22000, 12000))
    monkeypatch.setattr(windows_memory, "physical_memory_mb", lambda: 48000)
    monkeypatch.setattr(windows_memory, "WindowsSignals", lambda: nullcontext(
        SimpleNamespace(read=lambda: (10000, 40000, 1000, False))))
    monkeypatch.setattr(windows_memory, "DedicatedMemory", lambda _: nullcontext(
        SimpleNamespace(read=lambda: (26000, 24000))))
    monkeypatch.setattr(image.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="selected-device"))
    cleanup = threading.Thread(target=run, args=(release,))
    admission = threading.Thread(target=run, args=(image.preflight,), name="admission")
    cleanup.start()
    try:
        assert closing.wait(2)
        admission.start()
        assert waiting.wait(2), "admission did not take the backend lifecycle lock"
    finally:
        finish_close.set()
        cleanup.join(timeout=5)
        if admission.ident is not None:
            admission.join(timeout=5)
    assert not cleanup.is_alive() and not admission.is_alive()
    assert not errors


@pytest.fixture
def enabled(client, monkeypatch):
    monkeypatch.setattr(image, "_backend", None)
    monkeypatch.setattr(image, "_backend_error", "")
    monkeypatch.setattr(image, "_active_job", None)
    monkeypatch.setattr(image, "_active_prompt", None)
    monkeypatch.setattr(image, "status", lambda: {
        "available": True, "why": "", "model": image.MODEL, "width": 512, "height": 512, "max_count": 6,
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
      S.ui_only = true; S.windows_stills = { available: true, model: "sd-turbo", width: 512, height: 512, max_count: 6 };
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
    assert params["suite_import"] == "i1" and params["suite_shots"] == ["s1"]
    assert params["prompts"] == ["Hall Daylight on a door."] and params["seeds"] == [1]


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


@pytest.mark.parametrize("params", [
    {"prompts": []}, {"prompts": ["x"] * 7}, {"prompts": [""]},
    {"prompts": ["x"], "prompt": "y"}, {"prompts": ["x", "y"], "count": 1},
    {"prompts": ["x"], "seeds": [True]}, {"prompts": ["x"], "seeds": []},
    {"prompts": ["x", "y"], "suite_import": "i1", "suite_shots": ["s1", "s1"]},
])
def test_invalid_batches_are_refused_before_loading(params):
    with pytest.raises(ValueError):
        image.parameters(params)


def test_batch_plan_preserves_duplicate_and_zero_seeds_without_overwriting_files():
    params = image.parameters({"prompts": ["first", "second"], "seeds": [0, 0]})
    assert image.image_plan(params) == [
        ("first", 0, "scout_s0_shot1.png"), ("second", 0, "scout_s0_shot2.png"),
    ]
    assert image.image_plan(image.parameters({"prompt": "old", "count": 2, "seed": 7})) == [
        ("old", 7, "scout_s7.png"), ("old", 8, "scout_s8.png"),
    ]


def test_warm_backend_is_reused_until_explicit_unloading(enabled, monkeypatch, tmp_path):
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
        def summary(self):
            return {"running": True, "loaded": self.loaded, "address": "owned", "why": ""}
    monkeypatch.setattr(image, "WindowsComfy", Backend)
    monkeypatch.setattr(image, "verify_weights", lambda *args: None)
    monkeypatch.setattr(image, "tools", lambda: SimpleNamespace(checker=lambda: None))
    monkeypatch.setattr(image, "render_case", lambda *args: SimpleNamespace(
        prepare=lambda *args: None, exercise=lambda *args: {"request_to_files_seconds": 0.1},
    ))
    first = image.render_job({"id": "a", "status": "running", "params": {"prompt": "a"}}, tmp_path)
    second = image.render_job({"id": "b", "status": "running", "params": {"prompt": "b"}}, tmp_path)
    assert not first["backend_reused"] and second["backend_reused"]
    assert events == ["start"] and image.backend_status()["loaded"]
    image.release_backend()
    assert events == ["start", "close"] and not image.backend_status()["running"]


def test_warm_cancellation_targets_only_the_active_prompt(enabled, monkeypatch):
    calls = []
    monkeypatch.setattr(image, "_backend", SimpleNamespace(cancel=lambda pid: calls.append(pid)))
    monkeypatch.setattr(image, "_active_job", "a")
    monkeypatch.setattr(image, "_active_prompt", "owned-prompt")
    image.cancel_job("other")
    image.cancel_job("a")
    assert calls == ["owned-prompt"]


def test_unloading_refuses_pending_work_then_releases_idle_backend(enabled):
    client, _ = enabled
    job = client.post("/api/jobs", json={"kind": "scout", "params": {"prompt": "a"}}).json()
    assert client.post("/api/windows/stills/unload", json={}).status_code == 409
    client.post(f"/api/jobs/{job['id']}/cancel", json={})
    assert client.post("/api/windows/stills/unload", json={}).status_code == 200


def test_warm_jobs_keep_the_total_deadline_without_unloading_the_backend(enabled, monkeypatch):
    callbacks = []
    class Timer:
        def __init__(self, seconds, callback):
            assert seconds == 600
            callbacks.append(callback)
        def start(self):
            pass
        def cancel(self):
            pass
    monkeypatch.setattr(server.threading, "Timer", Timer)
    cancelled = []
    monkeypatch.setattr(image, "cancel_job", lambda job_id: cancelled.append(job_id))
    def render(job, home):
        callbacks[0]()
        return {"cancelled": True}
    monkeypatch.setattr(image, "render_job", render)
    job = {"id": "a", "kind": "scout", "status": "running", "params": {"prompt": "a"}, "log": ""}
    with pytest.raises(RuntimeError, match="600 second deadline"):
        server.take(job, job["params"])
    assert job["status"] == "failed" and cancelled == ["a"]


def test_current_owned_commit_and_reserved_memory_are_used_not_peaks(monkeypatch):
    from windows_comfy import WindowsComfy
    values = {"process_memory": {"private_commit_bytes": 1024 << 20, "peak_commit_bytes": 12000 << 20},
              "reserved_bytes": 256 << 20, "peak_reserved_bytes": 10000 << 20}
    checked = []
    api = SimpleNamespace(
        CalibrationCase=SimpleNamespace(process_commit_mb=12288, gpu_limit_mb=12288),
        read_json=lambda *args: values,
        validate_identity=lambda *args: checked.append("identity"),
    )
    backend = WindowsComfy(api, lambda *args: None)
    backend.ready = True
    backend.directory = Path("owned")
    backend.process = SimpleNamespace(poll=lambda: None)
    backend.job = SimpleNamespace(process_accounting=lambda *args: checked.append("membership"))
    assert backend.accounting() == {"commit_mb": 1024, "reserved_mb": 256}
    assert checked == ["membership", "identity"]


def test_scene_batch_binds_each_result_to_its_own_shot_and_keeps_seeds():
    from test_slate_page import run
    root = Path(__file__).resolve().parents[1]
    out = run("""
      const index = fs.readFileSync(%s, "utf8");
      vm.runInThisContext("const stillParams = " + index.split("const stillParams = ")[1].split("\\nconst imageModelName")[0]);
      S.windows_stills = { available: true, model: "sd-turbo", width: 512, height: 512, max_count: 6 };
      globalThis.poll = async () => {};
      SL.p = film();
      slApply([{ type: "create_scene", name: "Hall" }, { type: "create_shot", scene: "Hall", name: "A", prompt: "A door." },
               { type: "create_shot", scene: "Hall", name: "B", prompt: "A tree." },
               { type: "create_shot", scene: "Hall", name: "Manual", prompt: "Keep this." }]);
      const scene = SL.p.scenes[0], [a, b, manual] = scene.shots;
      slTf(a).seed = 0; slTf(b).seed = 7; slTf(manual).image = "uploads/manual.png";
      answers["/api/jobs"] = { id: "batch" };
      await slSceneStoryboards(scene);
      out.params = calls.find(c => c[0] === "/api/jobs")[1].params;
      jobs.push({ id: "batch", status: "done", outputs: ["scouts/b/scout_s0_shot1.png", "scouts/b/scout_s7_shot2.png", "scouts/b/sheet.jpg"] });
      slStoryboard(a); slStoryboard(b);
      out.images = scene.shots.map(s => slTf(s).image);
    """ % json.dumps(str(root / "app/static/index.html")))
    assert out["params"]["prompts"] == ["A door.", "A tree."] and out["params"]["seeds"] == [0, 7]
    assert out["images"] == ["scouts/b/scout_s0_shot1.png", "scouts/b/scout_s7_shot2.png", "uploads/manual.png"]
