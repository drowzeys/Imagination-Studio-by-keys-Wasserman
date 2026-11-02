"""No model or GPU probes: protocol checks and Windows job ownership."""
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import threading
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_comfy_check", ROOT / "scripts" / "windows-comfy-check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def stats(directory):
    return {"system": {"argv": ["main.py", "--base-directory", directory]}, "devices": [{"type": "cuda"}]}


def test_owned_instance_identity():
    check.validate_identity(stats("owned"), "owned")


@pytest.mark.parametrize("given", [None, {}, {"system": {}}, stats("another")])
def test_unknown_instance_is_refused(given):
    with pytest.raises(RuntimeError):
        check.validate_identity(given, "owned")


def test_cpu_server_is_not_a_cuda_success():
    given = stats("owned")
    given["devices"] = [{"type": "cpu"}]
    with pytest.raises(RuntimeError, match="CUDA"):
        check.validate_identity(given, "owned")


def test_invalid_devices_are_refused():
    given = stats("owned")
    given["devices"] = None
    with pytest.raises(RuntimeError, match="device list"):
        check.validate_identity(given, "owned")


def test_empty_queue_and_core_nodes():
    check.validate_ready({"queue_running": [], "queue_pending": []},
                         {"UNETLoader": {}, "CLIPTextEncode": {}, "VAEDecode": {}})


@pytest.mark.parametrize("queue,nodes", [
    ({"queue_running": [1], "queue_pending": []}, {}),
    ({"queue_running": [], "queue_pending": [1]}, {}),
    ({}, {}),
    ({"queue_running": [], "queue_pending": []}, {"UNETLoader": {}}),
])
def test_incomplete_readiness_is_refused(queue, nodes):
    with pytest.raises(RuntimeError):
        check.validate_ready(queue, nodes)


def test_wrong_source_pin_is_not_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, "wrong\n", ""))
    with pytest.raises(ValueError, match="pinned"):
        check.verify_source(tmp_path)


def test_sustained_check_holds_for_the_requested_duration(monkeypatch):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(check.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(check.time, "sleep", sleep)
    monkeypatch.setattr(check, "read_json", lambda *args: {"queue_running": [], "queue_pending": []})
    held = check.hold_no_model(SimpleNamespace(poll=lambda: None), SimpleNamespace(check=lambda: None), 1, "unused")
    assert held >= 1


def test_sustained_check_refuses_a_workflow(monkeypatch):
    monkeypatch.setattr(check, "read_json", lambda *args: {"queue_running": [1], "queue_pending": []})
    with pytest.raises(RuntimeError, match="workflow"):
        check.hold_no_model(SimpleNamespace(poll=lambda: None), SimpleNamespace(check=lambda: None), 1, "unused")


def test_backend_budget_uses_backend_reported_headroom():
    given = {"devices": [{"type": "cuda", "vram_total": 36000 << 20, "vram_free": 1024 << 20}]}
    assert check.backend_budget(given) == (36000, 1024)


@pytest.mark.parametrize("free", [None, -1, True, 40000 << 20])
def test_invalid_backend_headroom_is_refused(free):
    with pytest.raises(RuntimeError):
        check.backend_budget({"devices": [{"type": "cuda", "vram_total": 36000 << 20, "vram_free": free}]})


def test_backend_monitor_starts_only_after_server_readiness(monkeypatch, tmp_path):
    ready = {"stats": False, "core": False, "attempts": 0}

    class Monitor:
        def __init__(self, *args):
            self.stopping = threading.Event()

        def wait_ready(self):
            pass

        def check(self):
            pass

        def start_backend(self, *args):
            assert ready["stats"] and ready["core"]

        def finish(self):
            pass

        def begin_backend_shutdown(self):
            pass

        def metrics(self):
            return {"samples": 1}

    process = SimpleNamespace(poll=lambda: None, wait=lambda **kwargs: None,
                              send_signal=lambda *args: None, returncode=0)
    monkeypatch.setattr(check.sys, "platform", "win32")
    monkeypatch.setattr(check, "verify_source", lambda *args: None)
    monkeypatch.setattr(check, "OwnedJob", lambda: SimpleNamespace(attach=lambda *args: None, close=lambda: None))
    monkeypatch.setattr(check, "MemoryWatchdog", Monitor)
    monkeypatch.setattr(check.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(check.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(check.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(check.time, "sleep", lambda *args: None)
    monkeypatch.setattr(check, "validate_identity", lambda *args: None)

    def response(base, path, **kwargs):
        if path == "/system_stats":
            ready["attempts"] += 1
            if ready["attempts"] == 1:
                raise urllib.error.URLError("not ready")
            ready["stats"] = True
            return {"system": {"comfyui_version": "test", "pytorch_version": "test"}}
        if path == "/object_info":
            ready["core"] = True
            return {"UNETLoader": {}, "CLIPTextEncode": {}, "VAEDecode": {}}
        return {"queue_running": [], "queue_pending": []}

    monkeypatch.setattr(check, "read_json", response)
    assert check.run_check(tmp_path, 5, 0, guarded=True)["ok"]


@pytest.mark.parametrize("planned", [True, False])
def test_inflight_backend_disconnect_is_ignored_only_during_planned_shutdown(monkeypatch, planned):
    from windows_memory import Snapshot

    monitor = check.MemoryWatchdog.__new__(check.MemoryWatchdog)
    for name in ("stopping", "failed", "ready", "backend_ready", "backend_stopping"):
        setattr(monitor, name, threading.Event())
    monitor.state_lock = threading.Lock()
    monitor.backend_thread = None
    monitor.backend_samples = 0
    monitor.backend_min_free_mb = None
    monitor.backend_budget = None
    monitor.configured_floor = ""
    monitor.reason = ""
    terminated = []
    monitor.job = SimpleNamespace(terminate=lambda: terminated.append(True))
    monitor.latest = Snapshot(48000, 22000, 12000, 36000, 24000, time.monotonic(),
                              12000, 40000, 1000, False, 26000, 23000)
    entered, release = threading.Event(), threading.Event()
    calls = [0]

    def response(*args, **kwargs):
        calls[0] += 1
        if calls[0] > 1:
            entered.set()
            assert release.wait(2)
            raise ConnectionResetError("closed")
        return {"system": {"argv": ["main.py", "--base-directory", "owned"]},
                "devices": [{"type": "cuda", "vram_total": 36000 << 20, "vram_free": 24000 << 20}]}

    monkeypatch.setattr(check, "read_json", response)
    try:
        monitor.start_backend("unused", "owned")
        assert entered.wait(2)
        if planned:
            monitor.begin_backend_shutdown()
        release.set()
        monitor.backend_thread.join(2)
        assert not monitor.backend_thread.is_alive()
        assert monitor.failed.is_set() is (not planned)
        assert bool(terminated) is (not planned)
        assert not monitor.stopping.is_set()
    finally:
        release.set()
        monitor.stopping.set()
        if monitor.backend_thread:
            monitor.backend_thread.join(2)


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership; no GPU is used")
def test_owned_job_drains_parent_and_child(tmp_path):
    child_code = "import time; time.sleep(60)"
    receipt = tmp_path / "child-pid"
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path; sys.stdin.readline(); "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"Path({str(receipt)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    job = check.OwnedJob()
    api = job.api
    api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    api.WaitForSingleObject.restype = ctypes.c_ulong
    child_handle = None
    process = subprocess.Popen([sys.executable, "-c", parent_code], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        job.attach(process)
        process.stdin.write("start\n")
        process.stdin.flush()
        deadline = time.monotonic() + 5
        child_text = ""
        while time.monotonic() < deadline:
            if receipt.exists():
                child_text = receipt.read_text()
                if child_text.isdigit():
                    break
            time.sleep(0.05)
        assert child_text.isdigit(), "owned parent did not start its child within the deadline"
        child_pid = int(child_text)
        child_handle = api.OpenProcess(0x100000, False, child_pid)
        assert child_handle
        job.close()
        process.wait(timeout=10)
        assert api.WaitForSingleObject(child_handle, 5000) == 0
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdin.close()
        if child_handle:
            api.CloseHandle(child_handle)
