"""The thermal gate and the live thermal stop (app/memory_manager.py) and the machine guard's heat backstop
(scripts/memguard.py): python3 -m pytest tests/test_thermal.py. The sensors are stand-ins: a list of readings the test
plays back, and a clock it moves. Nothing is cancelled on a real machine."""
import importlib.util
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

spec = importlib.util.spec_from_file_location("memguard_heat", ROOT / "scripts/memguard.py")
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)

LIMITS = {"gpu_c": 70, "zone_c": 85, "refuse_c": 88, "stop_c": 90, "wait_s": 0.3, "poll_s": 0.01, "throttle_s": 10}


@pytest.fixture(autouse=True)
def gate_on(monkeypatch):
    monkeypatch.setenv("STUDIO_THERMAL_GATE", "on")  # off for the rest of the suite (conftest.py)
    mm.events.clear()


def gpu(temp, reasons=0, zone=None):
    return {"gpu_c": temp, "zone_c": zone, "zone": "acpitz (thermal_zone0)" if zone else "", "reasons": reasons,
            "mac_state": None, "source": "nvidia"}


def player(readings, job=None, stages=None):
    """Each call answers the next reading (the last one again at the end), noting the job's stage when it is asked."""

    left = list(readings)

    def read():
        if job is not None and stages is not None and job.get("progress"):
            stages.append(job["progress"]["stage"])
        return left.pop(0) if len(left) > 1 else left[0]
    return read


def test_sensor_parsers(tmp_path):
    assert mm.parse_smi_thermal("88, 0x0000000000000048\n71, 0x0000000000000020\n") == (88.0, 0x68)
    assert mm.parse_smi_thermal("[N/A], [N/A]\n") == (None, None)
    for n, (kind, milli) in enumerate((("acpitz", "96600"), ("cpu", "61000"), ("junk", "-273000"))):
        (tmp_path / f"thermal_zone{n}").mkdir()
        (tmp_path / f"thermal_zone{n}" / "temp").write_text(milli)
        (tmp_path / f"thermal_zone{n}" / "type").write_text(kind)
    assert mm.thermal_zones(str(tmp_path)) == (96.6, "acpitz (thermal_zone0)")


def test_a_cool_machine_passes_at_once():
    job = {"id": "j", "kind": "video", "status": "running"}
    assert mm.thermal_admit(job, read=player([gpu(55, zone=70)]), limits=LIMITS) == ""
    assert "progress" not in job


def test_a_hot_gpu_waits_saying_so_then_goes():
    job, stages = {"id": "j", "kind": "video", "status": "running"}, []
    said = mm.thermal_admit(job, read=player([gpu(78), gpu(74), gpu(69)], job, stages), limits=LIMITS)
    assert stages[0] == "Cooling the GPU: 78 °C → 70 °C" and "Cooling the GPU: 74 °C → 70 °C" in stages
    assert "cool enough now" in said and "progress" not in job
    assert mm.events[-1]["what"] == "cooling"


def test_a_hot_zone_waits_too():
    job, stages = {"id": "j", "kind": "sfx", "status": "running"}, []
    mm.thermal_admit(job, read=player([gpu(60, zone=96.6), gpu(60, zone=80)], job, stages), limits=LIMITS)
    assert stages[0] == "Cooling the machine: acpitz (thermal_zone0) 97 °C → 85 °C"


def test_still_warm_after_the_wait_starts_with_a_warning():
    job = {"id": "j", "kind": "video", "status": "running"}
    said = mm.thermal_admit(job, read=player([gpu(78, zone=84)]), limits=LIMITS)
    assert "started anyway" in said and mm.events[-1]["what"] == "started warm"


def test_still_88_after_the_wait_is_refused():
    job = {"id": "j", "kind": "video", "status": "running"}
    with pytest.raises(RuntimeError) as refused:
        mm.thermal_admit(job, read=player([gpu(80, zone=88.5)]), limits=LIMITS)
    assert "refused at 88 °C" in str(refused.value) and "leave a minute between clips" in str(refused.value)
    assert mm.events[-1]["what"] == "refused (heat)"


def test_a_mac_waits_on_its_thermal_state_and_nothing_readable_skips_the_gate():
    job, stages = {"id": "j", "kind": "video", "status": "running"}, []
    mac = lambda state: {"gpu_c": None, "zone_c": None, "zone": "", "reasons": None, "mac_state": state, "source": "mac"}  # noqa: E731
    mm.thermal_admit(job, read=player([mac("serious"), mac("fair")], job, stages), limits=LIMITS)
    assert stages == ["Cooling the Mac: thermal state serious → fair"]
    with pytest.raises(RuntimeError):
        mm.thermal_admit({"id": "k", "kind": "video", "status": "running"}, read=player([mac("critical")]), limits=LIMITS)
    nothing = {"gpu_c": None, "zone_c": None, "zone": "", "reasons": None, "mac_state": None, "source": None}
    assert mm.thermal_admit({"id": "m", "kind": "video", "status": "running"}, read=lambda: nothing, limits=LIMITS) == ""


def test_off_and_gpu_free_jobs_are_not_gated(monkeypatch):
    assert mm.thermal_admit({"id": "e", "kind": "export"}, read=player([gpu(95)]), limits=LIMITS) == ""
    monkeypatch.setenv("STUDIO_THERMAL_GATE", "off")
    assert mm.thermal_admit({"id": "j", "kind": "video"}, read=player([gpu(95)]), limits=LIMITS) == ""


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_live_stop_acts_once_at_the_stop_line():
    acted, clock = [], Clock()
    stop = mm.ThermalStop(lambda job, why: acted.append((job["id"], why)), read=player([gpu(89), gpu(91)]), clock=clock)
    running = [{"id": "r", "kind": "video", "status": "running"}]
    assert stop.tick(running, LIMITS) == "" and acted == []
    assert "reached 91 °C" in stop.tick(running, LIMITS)
    stop.tick(running, LIMITS)
    assert [one[0] for one in acted] == ["r"]  # one action only


def test_the_live_stop_acts_on_sustained_hardware_thermal_slowdown_only():
    acted, clock = [], Clock()
    readings = [gpu(86, 0x48)]
    stop = mm.ThermalStop(lambda job, why: acted.append(why), read=lambda: readings[0], clock=clock)
    running = [{"id": "r", "kind": "video", "status": "running"}]
    for at in (0, 4, 8):
        clock.now = at
        assert stop.tick(running, LIMITS) == ""
    readings[0] = gpu(86, 0x20)  # software slowdown alone: the count starts again
    clock.now = 9
    stop.tick(running, LIMITS)
    readings[0] = gpu(86, 0x68)
    for at in (10, 15):
        clock.now = at
        assert stop.tick(running, LIMITS) == ""
    clock.now = 20
    assert "hardware thermal limit for 10 s" in stop.tick(running, LIMITS) and "0x68" in acted[0]
    assert len(acted) == 1


def test_the_watch_reads_heat_only_while_a_gpu_job_runs(monkeypatch):
    acted = []
    jobs = [{"id": "x", "kind": "export", "status": "running"}]
    watch = mm.Watch(lambda: jobs, on_hot=lambda job, why: acted.append(job["id"]))
    watch.heat.read = lambda: gpu(95)
    assert watch.thermal_tick() == "" and acted == []
    jobs.append({"id": "v", "kind": "video", "status": "running"})
    jobs.append({"id": "w", "kind": "video", "status": "running", "remote": "10.0.0.9"})  # on another Spark: not ours
    assert "95 °C" in watch.thermal_tick() and acted == ["v"]


def test_a_thermal_stop_cancels_and_queues_again_once(monkeypatch):
    cancelled, queued = [], []
    monkeypatch.setattr(server, "cancel", lambda job_id: cancelled.append(job_id) or {})
    monkeypatch.setattr(server, "queue_job", lambda body: queued.append(body) or {"id": f"again{len(queued)}"})
    monkeypatch.setattr(server, "THERMAL_RETRIES", {})
    job = {"id": "first", "kind": "video", "title": "a clip", "params": {"prompt": "rain"}, "log": "", "status": "running"}
    server.thermal_stop(job, "the GPU reached 91 °C (the stop is at 90 °C)")
    assert cancelled == ["first"] and queued[0] == {"kind": "video", "title": "a clip", "params": {"prompt": "rain"}}
    assert "queued again as again1" in job["error"] and "91 °C" in job["log"]
    repeat = {"id": "again1", "kind": "video", "title": "a clip", "params": {"prompt": "rain"}, "log": "", "status": "running"}
    server.thermal_stop(repeat, "the GPU reached 92 °C")
    assert len(queued) == 1 and "not queued again" in repeat["error"]


def test_the_server_gates_after_the_memory_admission_and_the_watch_has_the_stop():
    source = (ROOT / "app/server.py").read_text()
    assert source.index("memory_manager.thermal_admit(job") > source.index("memory_manager.admit(job")
    assert "memory_manager.Watch(lambda: list(jobs.values()), on_hot=thermal_stop)" in source


# ---------- the machine guard's backstop ----------

def test_memguard_heat_logs_then_cancels_only_well_after_the_apps_stop():
    limits, memo = mg.heat_limits(), {}
    assert mg.parse_smi_heat("91, 0x0000000000000068\n") == (91.0, 0x68)
    assert mg.heat_decide({"gpu_c": 88.0, "reasons": 0}, memo, 0, limits, True) == ["heat_log"]
    assert mg.heat_decide({"gpu_c": 91.0, "reasons": 0}, memo, 1, limits, True) == []
    assert mg.heat_decide({"gpu_c": 91.0, "reasons": 0}, memo, 1 + mg.HEAT_GRACE_S - 1, limits, True) == []
    assert mg.heat_decide({"gpu_c": 91.0, "reasons": 0}, memo, 1 + mg.HEAT_GRACE_S, limits, True) == ["heat_cancel"]
    assert mg.heat_decide({"gpu_c": 91.0, "reasons": 0}, memo, 30 + mg.HEAT_GRACE_S, limits, True) == []  # one action
    memo = {}
    for at in range(0, 30, 2):
        assert "heat_cancel" not in mg.heat_decide({"gpu_c": 86.0, "reasons": 0x40}, memo, at, limits, True)
    assert mg.heat_decide({"gpu_c": 86.0, "reasons": 0x40}, memo, 30, limits, True) == ["heat_cancel"]
    memo = {}
    for at in range(0, 60, 2):  # software thermal slowdown alone, and --no-cancel: never a cancel
        assert "heat_cancel" not in mg.heat_decide({"gpu_c": 86.0, "reasons": 0x20}, memo, at, limits, True)
        assert "heat_cancel" not in mg.heat_decide({"gpu_c": 95.0, "reasons": 0x48}, {}, at, limits, False)


def test_memguard_cancels_only_the_renders_of_this_machine(monkeypatch):
    studio = mg.Studio("http://127.0.0.1:1")
    here = mg.socket.gethostname()
    monkeypatch.setattr(studio, "call", lambda method, path, body=None, timeout=15: [
        {"id": "a", "status": "running", "spark": here}, {"id": "b", "status": "running", "spark": "spark-b"},
        {"id": "c", "status": "queued", "spark": None}])
    assert [job["id"] for job in studio.renders()] == ["a"]


def test_agents_are_told_to_leave_cooling_gaps():
    note = (ROOT / "STUDIO-AGENT.md").read_text()
    assert "Cooling the GPU" in note and "cooling gaps" in note.lower()
    assert "Heat" in (ROOT / "docs/memory-guard.md").read_text()
