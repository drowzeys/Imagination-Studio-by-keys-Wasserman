"""Synthetic memory budgets and Windows owned-process tests; no CUDA probes."""
import importlib.util
import ctypes
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from windows_memory import Profile, Snapshot, admission, host_floor_mb, pressure
import windows_memory


@pytest.mark.parametrize("reverse", [False, True])
def test_cuda_adapter_identity_follows_uuid_not_enumeration_order(monkeypatch, reverse):
    import uuid

    identities = [uuid.UUID(int=1), uuid.UUID(int=2)]
    if reverse:
        identities.reverse()

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            self.call(*args)
            return 0

    def integer(pointer, value):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = value

    def identifier(pointer, device):
        ctypes.memmove(pointer, identities[device.value].bytes, 16)

    def luid(pointer, mask, device):
        assert identities[device.value] == uuid.UUID(int=2)
        pointer[0], pointer[1] = 42, 31
        ctypes.cast(mask, ctypes.POINTER(ctypes.c_uint))[0] = 1

    driver = SimpleNamespace(
        cuInit=Function(lambda _: None), cuDeviceGetCount=Function(lambda p: integer(p, len(identities))),
        cuDeviceGet=Function(lambda p, i: integer(p, i)), cuDeviceGetUuid_v2=Function(identifier),
        cuDeviceGetLuid=Function(luid),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: driver, raising=False)
    assert windows_memory.cuda_adapter_luid("GPU-" + str(uuid.UUID(int=2))) == (31, 42)
    with pytest.raises(RuntimeError, match="exactly one"):
        windows_memory.cuda_adapter_luid("GPU-" + str(uuid.UUID(int=3)))


def test_wddm_memory_binds_the_selected_adapter_and_rounds_usage_up(monkeypatch):
    seen = []
    monkeypatch.setattr(windows_memory, "cuda_adapter_luid", lambda selected: (1, 2))

    class Capacity:
        def __init__(self, selected):
            seen.append(selected)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return 32000, 30000

    monkeypatch.setattr(windows_memory, "DedicatedMemory", Capacity)
    counters = [
        ("luid_0x00000001_0x00000002_phys_0", 0, (2000 << 20) + 1),
        ("luid_0x00000003_0x00000004_phys_0", 0, 100 << 20),
    ]
    monkeypatch.setattr(windows_memory, "_windows_gpu_counter", lambda path: counters)
    memory = windows_memory.WddmMemory("selected")
    assert memory.read() == (32000, 29999)
    assert memory.read() == (32000, 29999)
    assert seen == ["selected"]


@pytest.mark.parametrize("rows", [
    [],
    [("luid_0x1_0x2_phys_0", 0xC0000BC6, 100)],
    [("luid_0x1_0x2_phys_0", 0, -1)],
    [("luid_0x1_0x2_phys_0", 0, 40000 << 20)],
    [("luid_0x1_0x2_phys_1", 0, 100)],
    [("luid_0x1_0x2_phys_0", 0, 100)] * 2,
    [("luid_0x3_0x4_phys_0", 0, 100)],
])
def test_wddm_memory_refuses_missing_invalid_or_ambiguous_counters(monkeypatch, rows):
    memory = object.__new__(windows_memory.WddmMemory)
    memory.luid, memory.total = (1, 2), 32000
    monkeypatch.setattr(windows_memory, "_windows_gpu_counter", lambda path: rows)
    with pytest.raises(RuntimeError):
        memory.read()


def test_process_memory_diagnostics_still_sum_adapters_per_pid(monkeypatch):
    monkeypatch.setattr(windows_memory, "_windows_gpu_counter", lambda path: [
        ("pid_11_luid_0x1_0x2_phys_0", 0, 2 << 20),
        ("pid_11_luid_0x3_0x4_phys_0", 1, 3 << 20),
        ("pid_22_luid_0x1_0x2_phys_0", 0xC0000BC6, 9 << 20),
    ])
    assert windows_memory.windows_gpu_process_memory() == {11: 5}


def test_gpu_process_query_deduplicates_kinds_and_preserves_unknown_usage():
    def query(rows):
        def call(_device, count, buffer):
            ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = len(rows)
            for output, (pid, used) in zip(buffer, rows):
                output.pid, output.used_memory = pid, used
            return 0
        return call

    def name(pid, buffer, _size):
        if pid == 33:
            return 6
        value = f"C:\\Tools\\worker-{pid}.exe".encode()
        ctypes.memmove(buffer, value, len(value) + 1)
        return 0

    memory = object.__new__(windows_memory.DedicatedMemory)
    memory.handle = ctypes.c_void_p(1)
    memory.api = SimpleNamespace(
        nvmlDeviceGetComputeRunningProcesses_v2=query([(11, 8 << 30), (22, (1 << 64) - 1)]),
        nvmlDeviceGetGraphicsRunningProcesses_v2=query([(11, 8 << 30), (33, 1 << 30)]),
        nvmlSystemGetProcessName=name,
    )
    result = memory.processes()
    assert result["warnings"] == []
    assert [row["pid"] for row in result["processes"]] == [11, 33, 22]
    assert result["processes"][0] == {"pid": 11, "name": "worker-11.exe", "used_mb": 8192}
    assert result["processes"][-1]["used_mb"] is None
    assert result["processes"][1]["name"] is None
    assert "error 6" in result["processes"][1]["name_error"]


@pytest.mark.parametrize("mode", ["grow", "keeps-growing", "too-large", "unsupported"])
def test_gpu_process_queries_are_bounded_and_partial_failures_explicit(mode):
    calls = []

    def query(_device, count, rows):
        calls.append(len(rows))
        if mode == "unsupported":
            return 3
        if mode == "grow" and len(calls) == 2:
            ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = 0
            return 0
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = 4097 if mode == "too-large" else len(rows) + 1
        return 7

    memory = object.__new__(windows_memory.DedicatedMemory)
    memory.handle = ctypes.c_void_p(1)
    memory.api = SimpleNamespace(nvmlDeviceGetComputeRunningProcesses_v2=query)
    result = memory.processes()
    assert result["processes"] == []
    assert len(calls) <= 3
    assert any("graphics" in warning and "unsupported" in warning for warning in result["warnings"])
    if mode == "grow":
        assert calls == [32, 64] and len(result["warnings"]) == 1
    else:
        assert any("compute" in warning for warning in result["warnings"])


def test_gpu_warning_reports_numbers_processes_and_windows_attribution_limit(monkeypatch):
    class Memory:
        def __init__(self, device):
            assert device == "selected-device"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def processes(self):
            return {"processes": [{"pid": 11, "name": "model-host.exe", "used_mb": None},
                                  {"pid": 22, "name": "renderer.exe", "used_mb": 8192}],
                    "warnings": ["graphics process query is unsupported by this NVML"]}

    monkeypatch.setattr(windows_memory, "DedicatedMemory", Memory)
    monkeypatch.setattr(windows_memory, "windows_gpu_process_memory", lambda: {11: 24576})
    text = windows_memory.gpu_memory_warning(4512, 14336, "selected-device")
    assert "4.4 GiB available; 14.0 GiB required" in text
    assert "model-host.exe (PID 11, 24.00 GiB, Windows total across adapters)" in text
    assert "renderer.exe (PID 22, 8.00 GiB)" in text
    assert text.index("model-host.exe") < text.index("renderer.exe")
    assert "not a complete attribution" in text and "Process query incomplete" in text
    assert "Task Manager" in text and "will not stop unrelated processes" in text


def test_process_query_timeout_does_not_replace_memory_refusal(monkeypatch):
    def timeout(argv, **kwargs):
        assert argv[-1] == "--id=3" and kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(windows_memory.subprocess, "run", timeout)
    text = windows_memory.gpu_memory_warning(1024, 6144, device_index=3)
    assert "1.0 GiB available; 6.0 GiB required" in text
    assert "details unavailable (TimeoutExpired)" in text and "Task Manager" in text


def test_empty_gpu_process_list_is_not_reported_as_unused_memory(monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(windows_memory, "DedicatedMemory", lambda _: nullcontext(
        SimpleNamespace(processes=lambda: {"processes": [], "warnings": []})))
    text = windows_memory.gpu_memory_warning(1024, 6144, "selected-device")
    assert "No GPU processes with measured usage of at least 1 GiB" in text
    assert "Task Manager" in text


def test_gpu_warning_filters_small_and_unknown_users_and_sorts_measurements(monkeypatch):
    from contextlib import nullcontext

    processes = [{"pid": pid, "name": f"worker-{pid}.exe", "used_mb": None} for pid in range(1, 35)]
    monkeypatch.setattr(windows_memory, "DedicatedMemory", lambda _: nullcontext(
        SimpleNamespace(processes=lambda: {"processes": processes, "warnings": []})))
    monkeypatch.setattr(windows_memory, "windows_gpu_process_memory",
                        lambda: {1: 1023, 2: 1024, 33: 24000, 34: 8192, 99: 32000})
    text = windows_memory.gpu_memory_warning(4512, 14336, "selected-device")
    assert "worker-1.exe" not in text and "worker-3.exe" not in text
    assert "PID 99" not in text
    assert "worker-2.exe (PID 2, 1.00 GiB" in text
    assert text.index("worker-33.exe") < text.index("worker-34.exe") < text.index("worker-2.exe")
    assert "usage unavailable" not in text


def test_gpu_warning_does_not_list_unknown_users_if_windows_counters_fail(monkeypatch):
    from contextlib import nullcontext

    def unavailable():
        raise RuntimeError("PDH query unavailable")

    monkeypatch.setattr(windows_memory, "windows_gpu_process_memory", unavailable)
    monkeypatch.setattr(windows_memory, "DedicatedMemory", lambda _: nullcontext(SimpleNamespace(
        processes=lambda: {"processes": [{"pid": 11, "name": "unknown.exe", "used_mb": None},
                                        {"pid": 12, "name": "small.exe", "used_mb": 1023}], "warnings": []})))
    text = windows_memory.gpu_memory_warning(1024, 6144, "selected-device")
    assert "unknown.exe" not in text and "small.exe" not in text
    assert "Windows GPU process counters unavailable (RuntimeError)" in text
    assert "No GPU processes with measured usage of at least 1 GiB" in text


def test_nvml_failure_keeps_the_actionable_memory_refusal(monkeypatch):
    def unavailable(_device):
        raise RuntimeError("NVML could not initialize (error 9)")

    monkeypatch.setattr(windows_memory, "DedicatedMemory", unavailable)
    text = windows_memory.gpu_memory_warning(1024, 6144, "selected-device")
    assert "1.0 GiB available; 6.0 GiB required" in text
    assert "GPU process details unavailable (RuntimeError)" in text
    assert "Unload unused models" in text


def snapshot(**changes):
    values = dict(physical_total_mb=48000, host_total_mb=22000, host_free_mb=12000,
                  gpu_total_mb=36000, gpu_free_mb=24000, at=100.0,
                  host_commit_used_mb=12000, host_commit_limit_mb=40000, host_nonpaged_mb=1000,
                  host_low_memory=False, gpu_dedicated_total_mb=26000, gpu_dedicated_free_mb=23000)
    return Snapshot(**{**values, **changes})


def profile(**changes):
    values = dict(route="synthetic", measurement="synthetic test fixture", physical_total_mb=48000,
                  host_total_mb=22000, host_need_mb=4000, gpu_need_mb=20000,
                  host_commit_need_mb=4000, gpu_dedicated_total_mb=26000)
    return Profile(**{**values, **changes})


def test_unknown_route_is_not_admitted():
    answer = admission(snapshot(), None)
    assert not answer["fits"] and "no measured" in answer["reason"]


def test_independent_budgets_are_not_added():
    assert admission(snapshot(), profile())["fits"]
    assert admission(snapshot(host_free_mb=6000), profile(host_need_mb=8000))["fits"]
    assert not admission(snapshot(gpu_free_mb=22047), profile())["fits"]
    assert not admission(snapshot(), profile(host_need_mb=21000))["fits"]
    assert not admission(snapshot(), profile(host_commit_need_mb=28000))["fits"]


def test_profile_must_match_measured_split():
    answer = admission(snapshot(), profile(host_total_mb=24000))
    assert not answer["fits"] and "split" in answer["reason"]


def test_missing_measurement_is_not_a_profile():
    assert not admission(snapshot(), profile(measurement=""))["fits"]


def test_windows_manages_host_pressure_unless_a_floor_is_explicit():
    assert host_floor_mb(snapshot()) == 0
    assert pressure(snapshot(host_free_mb=6000)) == ""
    assert host_floor_mb(snapshot(), "10000") == 10000
    assert "reserve" in pressure(snapshot(host_free_mb=9000), "10000")
    assert host_floor_mb(snapshot(physical_total_mb=128000)) == 0


@pytest.mark.parametrize("given", ["0", "-1", "bad", "8.5"])
def test_invalid_floor_is_explicit(given):
    with pytest.raises(ValueError):
        host_floor_mb(snapshot(), given)


@pytest.mark.parametrize("changes", [
    {"host_free_mb": -1}, {"host_free_mb": True}, {"host_free_mb": 23000},
    {"gpu_free_mb": 40000}, {"physical_total_mb": 10000}, {"at": float("nan")},
    {"host_commit_used_mb": 40001}, {"host_low_memory": 1}, {"gpu_dedicated_free_mb": 27000},
])
def test_invalid_samples_are_refused(changes):
    with pytest.raises(ValueError):
        pressure(snapshot(**changes))


def test_pressure_and_fast_growth_stop_before_exhaustion():
    assert "low physical" in pressure(snapshot(host_low_memory=True))
    assert "commit headroom" in pressure(snapshot(host_commit_used_mb=40000))
    assert "physical memory" in pressure(snapshot(host_free_mb=0))
    assert "CUDA budget" in pressure(snapshot(gpu_free_mb=2047))
    assert "falling" in pressure(snapshot(host_free_mb=1500, at=100.25), configured="1000", previous=snapshot())
    assert "falling" in pressure(snapshot(gpu_free_mb=6000, at=100.25), previous=snapshot(gpu_free_mb=10000))
    assert "falling" in pressure(snapshot(host_commit_used_mb=39500, at=100.25),
                                 previous=snapshot(host_commit_used_mb=35000))


def test_pageable_ram_drop_does_not_override_healthy_os_signals():
    assert pressure(snapshot(host_free_mb=1500, at=100.25), previous=snapshot()) == ""


def test_dedicated_depletion_is_not_combined_budget_exhaustion():
    assert pressure(snapshot(gpu_dedicated_free_mb=1)) == ""


def test_commit_headroom_is_not_additional_gpu_capacity():
    answer = admission(snapshot(host_commit_limit_mb=1000000), profile(gpu_need_mb=30000))
    assert not answer["fits"] and "CUDA" in answer["reason"]


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
                  gpu_total_mb=36000, gpu_free_mb=24000, at=0,
                  host_commit_used_mb=12000, host_commit_limit_mb=40000, host_nonpaged_mb=1000,
                  host_low_memory=False, gpu_dedicated_total_mb=26000, gpu_dedicated_free_mb=23000)
    if mode == "pressure":
        values["host_low_memory"] = True
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


@pytest.mark.skipif(os.name != "nt", reason="Windows owned job; synthetic budgets, no GPU")
def test_backend_pressure_stops_job_even_when_sampler_cuda_budget_is_high(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("backend_memory_check", ROOT / "scripts" / "windows-comfy-check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    real_popen = subprocess.Popen
    data = json.dumps(asdict(snapshot()))
    code = (
        f"import json,time; data=json.loads({data!r})\n"
        "while True:\n data['at']=time.monotonic(); print(json.dumps(data),flush=True); time.sleep(0.05)\n"
    )
    monkeypatch.setattr(check.subprocess, "Popen", lambda _command, **kwargs:
                        real_popen([sys.executable, "-c", code], **kwargs))
    free = [22000]
    monkeypatch.setattr(check, "read_json", lambda *args, **kwargs:
                        {"system": {"argv": ["main.py", "--base-directory", "owned"]},
                         "devices": [{"type": "cuda", "vram_total": 36000 << 20, "vram_free": free[0] << 20}]})
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    job, monitor = check.OwnedJob(), None
    try:
        with (tmp_path / "backend-watch.log").open("wb") as log:
            monitor = check.MemoryWatchdog(job, 0, log)
            monitor.wait_ready()
            monitor.start_backend("unused", "owned")
            free[0] = 1024
            assert monitor.failed.wait(3)
            with pytest.raises(RuntimeError, match="backend budget.*CUDA"):
                monitor.check()
            assert monitor.latest.gpu_free_mb == 24000
            monitor.stopping.set()
            job.close()
            with pytest.raises(RuntimeError):
                monitor.finish()
    finally:
        if monitor:
            monitor.stopping.set()
        job.close()
        if monitor:
            monitor.process.wait(timeout=10)
