"""The Studio's memory manager (app/memory_manager.py): idle modules given back in order before a job that does not
fit, a wait and then a refusal naming the other programs, the watch during a render, and GET /api/memory and
POST /api/memory/release. python3 -m pytest tests/test_memory_manager.py. Nothing is loaded: the modules are
stand-ins and the machine's memory is a number the test sets."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
os.environ.setdefault("BOLT_BACKEND", "external")
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import memory_manager as mm  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)


class Machine:
    """Available memory the test sets; each released module gives its memory back."""

    def __init__(self, available, total=128_000, floor=12_800):
        self.available, self.total, self.floor = available, total, floor

    def reading(self):
        return {"available_mb": self.available, "total_mb": self.total, "floor_mb": self.floor, "platform": "linux"}


@pytest.fixture
def machine(monkeypatch):
    box = Machine(40_000)
    monkeypatch.setenv("STUDIO_MEMORY_MANAGER", "on")  # off for the rest of the suite (conftest.py)
    monkeypatch.setattr(mm, "reading", box.reading)
    monkeypatch.setattr(mm, "_modules", [])
    monkeypatch.setattr(mm, "foreign", lambda top=5, rows=None, own=None: [])
    monkeypatch.setattr(mm.time, "sleep", lambda s: None)
    monkeypatch.setattr(mm, "stall_some", lambda: 0.0)
    mm.events.clear()
    return box


def fake(box, name, mb, busy=False, log=None):
    state = {"loaded": True}

    def release(why):
        if not state["loaded"]:
            return False
        state["loaded"] = False
        box.available += mb
        if log is not None:
            log.append(name)
        return True
    mm.register(mm.Module(name, name.title(), lambda: state["loaded"], release, held_mb=lambda: mb if state["loaded"] else 0,
                          busy=lambda: busy))
    return state


def fits_with(box, need):
    return lambda: {"fits": box.available - need >= box.floor, "need_mb": need, "available_mb": box.available, "floor_mb": box.floor}


@pytest.mark.parametrize("spark_flag", [False, True])
def test_windows_small_sfx_does_not_borrow_legacy_sound_memory(monkeypatch, spark_flag):
    monkeypatch.setattr(server, "SPARK", spark_flag)
    monkeypatch.setattr(server, "WINDOWS", True)
    monkeypatch.setattr(server, "WIN32", True)
    monkeypatch.setattr(server.voice, "mem_available_gb", lambda: pytest.fail("Small SFX has its own Windows admission"))
    monkeypatch.setattr(server.memory_guard, "sound_check", lambda *a, **k: pytest.fail("Do not use Linux or legacy sound memory"))
    job = {"kind": "sfx", "params": {"sound_model": server.windows_audio3.MODEL, "seconds": 5}}
    assert server.job_fit(job, 100_000) is None


def test_spark_small_sfx_memory_manager_asks_its_own_route(monkeypatch):
    monkeypatch.setattr(server, "SPARK", True)
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", False)
    monkeypatch.setattr(server.memory_guard, "card_room_applies", lambda: False)
    monkeypatch.setattr(server.memory_guard, "floor_mb", lambda: 25_000)
    calls = []

    def check(route, seconds, **kwargs):
        calls.append((route, seconds, kwargs))
        if route == server.windows_audio3.MODEL:
            return {"need_mb": None}
        return {"need_mb": 8_000, "available_mb": kwargs["available_mb"], "fits": True}

    monkeypatch.setattr(server.memory_guard, "sound_check", check)
    assert server.job_fit({"kind": "sfx", "params": {"sound_model": server.windows_audio3.MODEL, "seconds": 5}}, 100_000) is None
    assert calls[-1] == (server.windows_audio3.MODEL, 5.0, {"available_mb": 100_000, "card": False})
    legacy = server.job_fit({"kind": "sfx", "params": {"sound_model": server.sound.SFX_MODEL, "seconds": 5}}, 100_000)
    assert calls[-1][0] == "sfx" and legacy["need_mb"] == 8_000 and legacy["fits"]


def test_a_job_that_fits_changes_nothing(machine):
    log = []
    fake(machine, "bolt", 28_000, log=log)
    assert mm.admit({"id": "j", "kind": "video", "status": "running"}, fits_with(machine, 20_000)) == ""
    assert log == []


def test_idle_modules_go_in_order_until_the_job_fits_and_never_the_jobs_own(machine):
    log = []
    fake(machine, "voice", 10_000, log=log)
    fake(machine, "music", 10_000, log=log)
    fake(machine, "comfy", 30_000, log=log)  # a video job runs on it: kept
    fake(machine, "bolt", 28_000, log=log)
    # 40 GB available, 50 needed and 12.8 kept free: voice and music give 60, not enough; ComfyUI is the job's; Bolt goes
    said = mm.admit({"id": "j", "kind": "video", "status": "running"}, fits_with(machine, 50_000))
    assert log == ["voice", "music", "bolt"]
    assert said.count("was unloaded to make room") == 3
    assert [e["module"] for e in mm.events if e["what"] == "released"] == ["voice", "music", "bolt"]


def test_a_voice_job_may_have_comfy_stopped_and_stops_as_soon_as_it_fits(machine):
    log = []
    fake(machine, "comfy", 30_000, log=log)
    fake(machine, "bolt", 28_000, log=log)
    mm.admit({"id": "j", "kind": "voice", "status": "running"}, fits_with(machine, 50_000))
    assert log == ["comfy"]  # 70 GB: it fits, Bolt stays


def test_a_busy_module_is_left_alone(machine):
    log = []
    fake(machine, "bolt", 28_000, busy=True, log=log)
    with pytest.raises(RuntimeError):
        mm.admit({"id": "j", "kind": "voice", "status": "running"}, fits_with(machine, 50_000), wait_s=0, mode="strict")
    assert log == []


def test_other_programs_are_waited_for_then_named_in_the_refusal(machine, monkeypatch):
    others = [{"pid": 4242, "name": "vllm", "mb": 22_528, "gpu": True, "command": "python -m vllm.entrypoints.openai.api_server"}]
    monkeypatch.setattr(mm, "foreign", lambda top=5, rows=None, own=None: others[:top])
    job = {"id": "j", "kind": "video", "status": "running"}
    with pytest.raises(RuntimeError) as refused:
        mm.admit(job, fits_with(machine, 50_000), wait_s=0.01, mode="strict")
    assert "vllm (pid 4242, 22.0 GB, on the GPU)" in str(refused.value)
    assert "does not stop programs it did not start" in str(refused.value)
    assert mm.events[-1]["what"] == "refused" and "progress" not in job


def test_memory_that_comes_back_while_waiting_lets_the_job_go(machine, monkeypatch):
    others = [{"pid": 7, "name": "ollama", "mb": 30_000, "gpu": True, "command": "ollama serve"}]
    monkeypatch.setattr(mm, "foreign", lambda top=5, rows=None, own=None: others[:top])
    calls = {"n": 0}

    def fit():
        calls["n"] += 1
        if calls["n"] > 3:
            machine.available = 90_000  # the other program ended
        return fits_with(machine, 50_000)()
    job = {"id": "j", "kind": "video", "status": "running"}
    said = mm.admit(job, fit, wait_s=5, mode="strict")
    assert "came back" in said and "progress" not in job


def test_warn_mode_runs_it_with_the_reason(machine):
    said = mm.admit({"id": "j", "kind": "video", "status": "running"}, fits_with(machine, 50_000), wait_s=0, mode="warn")
    assert "runs anyway (STUDIO_MEMORY_GUARD=warn)" in said


def test_unmeasured_kinds_go_as_before(machine):
    assert mm.admit({"id": "j", "kind": "export"}, lambda: None) == ""


def test_the_watch_releases_the_next_idle_module_under_one_and_a_half_floors(machine):
    log = []
    fake(machine, "voice", 4_000, log=log)
    fake(machine, "comfy", 30_000, log=log)
    fake(machine, "bolt", 28_000, log=log)
    jobs = [{"id": "r", "kind": "video", "status": "running"}]
    watch = mm.Watch(lambda: jobs, poll_s=2)
    machine.available = 30_000  # 1.5 floors = 19.2 GB: nothing to do
    assert watch.tick() == [] and log == []
    machine.available = 15_000
    done = watch.tick()
    assert [d["module"] for d in done] == ["voice", "bolt"] and "comfy" not in log  # 19 GB, then 47 GB: the render's comfy stays
    assert watch.interval() == mm.RENDER_POLL_S  # faster while a job runs here
    machine.available = 10_000
    assert watch.tick() == []  # the cooldown


def test_the_watch_counts_a_memory_stall_only_near_the_floor(machine, monkeypatch):
    monkeypatch.setattr(mm, "stall_some", lambda: 40.0)
    assert mm.pressed(20_000, 12_800)  # under two floors, tasks waiting on memory
    assert not mm.pressed(60_000, 12_800)  # a cold start's page-cache stall with half the machine free


def test_foreign_leaves_out_the_studio_and_puts_gpu_programs_first(monkeypatch):
    me = os.getpid()
    rows = [{"pid": me, "ppid": 1, "mb": 3000, "name": "python", "command": "python app/server.py"},
            {"pid": 900001, "ppid": me, "mb": 50_000, "name": "tf-h3", "command": "worker"},  # the app's child
            {"pid": 900002, "ppid": 1, "mb": 40_000, "name": "python", "command": str(ROOT / "spark/spark_generate.py")},
            {"pid": 900003, "ppid": 1, "mb": 30_000, "name": "chrome", "command": "chrome"},
            {"pid": 900004, "ppid": 1, "mb": 22_000, "name": "vllm", "command": "vllm serve"},
            {"pid": 900005, "ppid": 1, "mb": 200, "name": "tiny", "command": "tiny"}]
    monkeypatch.setattr(mm, "uses_gpu", lambda pid: pid == 900004)
    found = mm.foreign(5, rows=rows)
    assert [r["pid"] for r in found] == [900004, 900003]
    assert found[0]["gpu"] is True


def test_the_routes(machine, monkeypatch):
    log = []
    fake(machine, "music", 8_000, log=log)
    fake(machine, "bolt", 28_000, busy=True, log=log)
    said = client.get("/api/memory").json()
    assert said["headroom_mb"] == 40_000 - 12_800 and said["releasable_mb"] == 8_000
    assert [(m["name"], m["idle"]) for m in said["modules"]] == [("music", True), ("bolt", False)]
    answer = client.post("/api/memory/release", json={"modules": ["music", "bolt"]}).json()
    assert [r["module"] for r in answer["released"]] == ["music"] and log == ["music"]
    assert client.post("/api/memory/release", json={"modules": "all"}).status_code == 400


def test_off_admits_everything_and_the_watch_rests(machine, monkeypatch):
    monkeypatch.setenv("STUDIO_MEMORY_MANAGER", "off")
    machine.available = 1_000
    assert mm.admit({"id": "j", "kind": "video", "status": "running"}, fits_with(machine, 50_000), mode="strict") == ""
    assert mm.Watch(lambda: []).tick() == []


def test_the_server_registers_its_modules_in_order_and_admits_before_a_job_loads():
    source = (ROOT / "app/server.py").read_text()
    order = [source.index(f'memory_manager.Module("{name}"') for name in ("voice", "music", "comfy", "bolt")]
    assert order == sorted(order)
    assert "memory_manager.admit(job, lambda: job_fit(job)" in source and "lenient" in source
    assert source.index("memory_manager.admit(job") > source.index('job["log"] += bolt.admit(job')


def test_a_local_job_named_by_this_machine_still_keeps_its_module():
    """A job rendering here carries this machine's own name in "spark" (app/sparks.py): only "remote" (a worker's
    address) means elsewhere. Read the other way, the watch could unload the ComfyUI a local render runs on."""

    import socket

    jobs = [{"id": "a", "kind": "video", "status": "running", "spark": socket.gethostname(), "remote": None},
            {"id": "b", "kind": "voice", "status": "running", "spark": "spark-b", "remote": "10.0.0.9"}]
    assert mm.running_kinds(jobs) == {"comfy"}
