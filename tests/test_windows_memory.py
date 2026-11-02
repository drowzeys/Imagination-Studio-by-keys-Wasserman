"""Synthetic memory budgets and Windows owned-process tests; no CUDA probes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from windows_memory import Profile, Snapshot, admission, host_floor_mb, pressure


def snapshot(**changes):
    values = dict(physical_total_mb=48000, host_total_mb=22000, host_free_mb=12000,
                  gpu_total_mb=36000, gpu_free_mb=24000, at=100.0)
    return Snapshot(**{**values, **changes})


def profile(**changes):
    values = dict(route="synthetic", measurement="synthetic test fixture", physical_total_mb=48000,
                  host_total_mb=22000, host_need_mb=4000, gpu_need_mb=20000)
    return Profile(**{**values, **changes})


def test_unknown_route_is_not_admitted():
    answer = admission(snapshot(), None)
    assert not answer["fits"] and "no measured" in answer["reason"]


def test_independent_budgets_are_not_added():
    assert admission(snapshot(), profile())["fits"]
    assert not admission(snapshot(host_free_mb=11999), profile())["fits"]
    assert not admission(snapshot(gpu_free_mb=22047), profile())["fits"]


def test_profile_must_match_measured_split():
    answer = admission(snapshot(), profile(host_total_mb=24000))
    assert not answer["fits"] and "split" in answer["reason"]


def test_missing_measurement_is_not_a_profile():
    assert not admission(snapshot(), profile(measurement=""))["fits"]


def test_reserves_cannot_be_lowered_with_environment_setting():
    assert host_floor_mb(snapshot(), "1") == 8000
    assert host_floor_mb(snapshot(), "10000") == 10000
    assert host_floor_mb(snapshot(physical_total_mb=128000)) == 12800


@pytest.mark.parametrize("given", ["0", "-1", "bad", "8.5"])
def test_invalid_floor_is_explicit(given):
    with pytest.raises(ValueError):
        host_floor_mb(snapshot(), given)


@pytest.mark.parametrize("changes", [
    {"host_free_mb": -1}, {"host_free_mb": True}, {"host_free_mb": 23000},
    {"gpu_free_mb": 40000}, {"physical_total_mb": 10000}, {"at": float("nan")},
])
def test_invalid_samples_are_refused(changes):
    with pytest.raises(ValueError):
        pressure(snapshot(**changes))


def test_pressure_and_fast_growth_stop_before_exhaustion():
    assert "host memory" in pressure(snapshot(host_free_mb=7999))
    assert "CUDA budget" in pressure(snapshot(gpu_free_mb=2047))
    assert "falling" in pressure(snapshot(host_free_mb=9000, at=100.25), previous=snapshot())
    assert "falling" in pressure(snapshot(gpu_free_mb=6000, at=100.25), previous=snapshot(gpu_free_mb=10000))


def test_stale_or_reversed_sample_intervals_are_refused():
    with pytest.raises(ValueError):
        pressure(snapshot(at=103), previous=snapshot())
    with pytest.raises(ValueError):
        pressure(snapshot(at=99), previous=snapshot())


@pytest.mark.skipif(os.name != "nt", reason="Windows owned jobs; synthetic samples, not GPU hardware")
@pytest.mark.parametrize("mode", ["healthy", "pressure", "stale", "exit", "invalid"])
def test_watchdog_stops_only_its_owned_job(mode, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("memory_check", ROOT / "scripts" / "windows-comfy-check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    real_popen = subprocess.Popen
    unrelated = real_popen([sys.executable, "-c", "import time; time.sleep(60)"])
    values = dict(physical_total_mb=48000, host_total_mb=22000, host_free_mb=12000,
                  gpu_total_mb=36000, gpu_free_mb=24000, at=0)
    if mode == "pressure":
        values["host_free_mb"] = 7999
    code = f"import json,time; data=json.loads({json.dumps(values)!r})\n"
    if mode == "invalid":
        code += "print('not JSON',flush=True); time.sleep(60)\n"
    elif mode == "exit":
        code += "raise SystemExit(0)\n"
    elif mode == "healthy":
        code += "while True:\n data['at']=time.monotonic(); print(json.dumps(data),flush=True); time.sleep(0.05)\n"
    else:
        code += "data['at']=time.monotonic(); print(json.dumps(data),flush=True); time.sleep(60)\n"

    def synthetic(_command, **kwargs):
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(check.subprocess, "Popen", synthetic)
    monkeypatch.setattr(check, "SAMPLE_START_TIMEOUT", 2)
    monkeypatch.setattr(check, "SAMPLE_FRESHNESS", 0.3)
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    job, monitor = check.OwnedJob(), None
    try:
        with (tmp_path / "watch.log").open("wb") as log:
            monitor = check.MemoryWatchdog(job, 0, log)
            if mode == "healthy":
                monitor.wait_ready()
                time.sleep(0.1)
                monitor.check()
                assert monitor.samples >= 1
            elif mode == "stale":
                monitor.wait_ready()
                assert monitor.failed.wait(3)
                with pytest.raises(RuntimeError, match="stopped answering"):
                    monitor.check()
            else:
                assert monitor.failed.wait(3)
                with pytest.raises(RuntimeError):
                    monitor.check()
            assert unrelated.poll() is None
            monitor.stopping.set()
            job.close()
            if mode == "healthy":
                monitor.finish()
            else:
                with pytest.raises(RuntimeError):
                    monitor.finish()
            assert monitor.process.poll() is not None
    finally:
        if monitor:
            monitor.stopping.set()
        job.close()
        if monitor:
            monitor.process.wait(timeout=10)
        unrelated.kill()
        unrelated.wait(timeout=10)
