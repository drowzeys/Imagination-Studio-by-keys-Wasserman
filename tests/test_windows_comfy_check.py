"""No model or GPU probes: protocol checks and Windows job ownership."""
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

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
