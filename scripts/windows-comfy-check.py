#!/usr/bin/env python3
"""Bounded no-model ComfyUI readiness check; never submits a workflow."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

COMMIT = "b9b290e43f1eb379946dfeb013faf9bbefa1a618"
APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from windows_memory import Snapshot, host_floor_mb, pressure  # noqa: E402

SAMPLE_START_TIMEOUT = 30
SAMPLE_FRESHNESS = 2


class OwnedJob:
    """Keep cleanup tied to the launched process handle, not a port or reusable PID."""

    def __init__(self):
        self.lock = threading.RLock()
        size = ctypes.c_size_t

        class Limits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", size), ("max_working_set", size),
                ("active_process_limit", wintypes.DWORD), ("affinity", size),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Limits), ("io", Counters), ("process_memory", size), ("job_memory", size),
                ("peak_process_memory", size), ("peak_job_memory", size),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
                ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
                ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD),
            ]

        self.accounting_type = Accounting
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process):
        # Popen retains this exact Windows process handle until it is closed.
        with self.lock:
            if not self.handle:
                raise RuntimeError("the owned job is closed")
            if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        with self.lock:
            if self.handle and not self.api.TerminateJobObject(self.handle, 0):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        with self.lock:
            if not self.handle:
                return
            if not self.api.TerminateJobObject(self.handle, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 10
            while True:
                accounting = self.accounting_type()
                if not self.api.QueryInformationJobObject(
                    self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not accounting.active_processes:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("the owned Windows job did not drain within 10 seconds")
                time.sleep(0.05)
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


class MemoryWatchdog:
    """Sample in a separate process so a stalled CUDA query cannot block the supervisor."""

    def __init__(self, job: OwnedJob, device_index: int, log):
        self.job = job
        self.stopping = threading.Event()
        self.failed = threading.Event()
        self.ready = threading.Event()
        self.messages = queue.Queue(maxsize=8)
        self.state_lock = threading.Lock()
        self.reason = ""
        self.latest = None
        self.samples = 0
        self.configured_floor = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
        self.process = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(APP / "windows_memory.py"), "--watch", "--device-index", str(device_index)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            job.attach(self.process)
        except (OSError, RuntimeError):
            self.process.kill()
            self.process.wait(timeout=10)
            self.process.stdout.close()
            raise
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.supervisor = threading.Thread(target=self.supervise, daemon=True)
        self.reader.start()
        self.supervisor.start()

    def fail(self, reason: str):
        with self.state_lock:
            if self.stopping.is_set() or self.failed.is_set():
                return
            self.reason = reason
            self.failed.set()
            self.ready.set()
        print(f"[comfy-check] memory watchdog: {reason}; stopping the owned job", file=sys.stderr)
        try:
            self.job.terminate()
        except OSError as error:
            with self.state_lock:
                self.reason += f"; owned job termination failed: {error}"
            print(f"[comfy-check] owned job termination failed: {error}", file=sys.stderr)

    def read(self):
        try:
            while not self.stopping.is_set():
                line = self.process.stdout.readline(4097)
                if len(line) > 4096:
                    self.fail("memory sampler response exceeds the limit")
                    return
                self.messages.put_nowait(line or None)
                if not line:
                    return
        except queue.Full:
            self.fail("memory sampler messages are not being consumed")
        except (OSError, ValueError) as error:
            self.fail(f"cannot read memory sampler: {error}")

    def supervise(self):
        previous = None
        while not self.stopping.is_set() and not self.failed.is_set():
            try:
                line = self.messages.get(timeout=SAMPLE_START_TIMEOUT if previous is None else SAMPLE_FRESHNESS)
            except queue.Empty:
                self.fail("memory sampler stopped answering")
                return
            if self.stopping.is_set():
                return
            if line is None:
                self.fail("memory sampler exited")
                return
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("memory sampler returned an invalid response")
                if "error" in data:
                    raise ValueError(f"memory sampler failed: {data['error']}")
                snapshot = Snapshot(**data)
                snapshot.validate()
                if not 0 <= time.monotonic() - snapshot.at <= SAMPLE_FRESHNESS:
                    raise ValueError("memory sampler returned stale data")
                reason = pressure(snapshot, self.configured_floor, previous)
            except (TypeError, ValueError) as error:
                self.fail(f"invalid memory sample: {error}")
                return
            if reason:
                self.fail(reason)
                return
            previous = snapshot
            with self.state_lock:
                self.latest = snapshot
                self.samples += 1
            self.ready.set()

    def check(self):
        with self.state_lock:
            if self.failed.is_set():
                raise RuntimeError(f"memory watchdog refused this check: {self.reason}")

    def wait_ready(self):
        if not self.ready.wait(SAMPLE_START_TIMEOUT + 1):
            self.fail("memory sampler startup exceeded its deadline")
        self.check()

    def finish(self):
        self.process.wait(timeout=10)
        self.reader.join(timeout=3)
        self.supervisor.join(timeout=SAMPLE_FRESHNESS + 1)
        if self.reader.is_alive() or self.supervisor.is_alive():
            raise RuntimeError("memory watchdog threads did not stop within their deadlines")
        self.process.stdout.close()
        self.check()


def verify_source(source: Path) -> None:
    done = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=10, check=True)
    if done.stdout.strip() != COMMIT:
        raise ValueError("ComfyUI must be at the Studio's pinned revision")
    dirty = subprocess.run(["git", "-C", str(source), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10, check=True)
    if dirty.stdout.strip():
        raise ValueError("the check requires an unmodified ComfyUI checkout")


def read_json(base: str, path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + path, timeout=3) as response:
        body = response.read((8 << 20) + 1)
    if len(body) > 8 << 20:
        raise RuntimeError("ComfyUI's readiness response exceeds the check's limit")
    return json.loads(body)


def validate_identity(stats: dict, directory: str) -> None:
    if not isinstance(stats, dict) or not isinstance(stats.get("system"), dict):
        raise RuntimeError("ComfyUI returned invalid system stats")
    argv = stats.get("system", {}).get("argv", [])
    if not isinstance(argv, list):
        raise RuntimeError("ComfyUI returned invalid instance arguments")
    if not any(argv[i:i + 2] == ["--base-directory", directory] for i in range(len(argv))):
        raise RuntimeError("the responding server is not this check's owned instance")
    devices = stats.get("devices")
    if not isinstance(devices, list) or any(not isinstance(device, dict) for device in devices):
        raise RuntimeError("ComfyUI returned an invalid device list")
    if not any(device.get("type") == "cuda" for device in devices):
        raise RuntimeError("the owned ComfyUI server did not report a CUDA device")


def validate_ready(queue: dict, nodes: dict) -> None:
    if not isinstance(queue, dict) or not isinstance(nodes, dict):
        raise RuntimeError("ComfyUI returned an invalid queue or node registry")
    if queue.get("queue_running") != [] or queue.get("queue_pending") != []:
        raise RuntimeError("the no-model check must have an empty queue")
    required = {"UNETLoader", "CLIPTextEncode", "VAEDecode"}
    if not required.issubset(nodes):
        raise RuntimeError("ComfyUI's core loader/text/decoder nodes are missing")


def run_check(source: Path, timeout: float, device_index: int, guarded: bool = False) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("this lifecycle check requires native Windows")
    verify_source(source)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="studio-comfy-check-") as directory:
        log = Path(directory) / "startup.log"
        command = [
            sys.executable, "-X", "utf8", str(source / "main.py"),
            "--listen", "127.0.0.1", "--port", str(port),
            "--base-directory", directory, "--cuda-device", str(device_index),
            "--offline", "--disable-all-custom-nodes", "--disable-auto-launch",
            "--disable-dynamic-vram", "--use-pytorch-cross-attention",
        ]
        job, process, monitor = OwnedJob(), None, None
        console_stop = False
        console_exit_code = None
        try:
            with log.open("wb") as output:
                if guarded:
                    monitor = MemoryWatchdog(job, device_index, output)
                    monitor.wait_ready()
                process = subprocess.Popen(
                    command, cwd=source, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"},
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                try:
                    job.attach(process)
                except (OSError, RuntimeError):
                    process.kill()
                    process.wait(timeout=10)
                    raise
                base, deadline = f"http://127.0.0.1:{port}", time.monotonic() + timeout
                last_error = "server has not answered"
                while True:
                    if monitor:
                        monitor.check()
                    if process.poll() is not None:
                        raise RuntimeError(f"ComfyUI exited before readiness (code {process.returncode})")
                    try:
                        stats = read_json(base, "/system_stats")
                    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                        last_error = str(error)
                    else:
                        validate_identity(stats, directory)
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"ComfyUI startup timed out: {last_error}")
                    time.sleep(0.25)
                nodes = read_json(base, "/object_info")
                validate_ready(read_json(base, "/queue"), nodes)
                if monitor:
                    monitor.check()
                result = {
                    "ok": True, "commit": COMMIT, "comfyui_version": stats["system"]["comfyui_version"],
                    "pytorch_version": stats["system"]["pytorch_version"], "registered_nodes": len(nodes),
                    "checks": ["owned loopback instance", "CUDA device", "core nodes", "empty queue"],
                    "scope": "no-model startup only; custom/API nodes and dynamic VRAM disabled",
                }
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    process.wait(timeout=10)
                    console_stop = True
                    console_exit_code = process.returncode
                except (OSError, subprocess.TimeoutExpired) as error:
                    print(f"[comfy-check] console stop did not complete; draining the owned job: {error}", file=sys.stderr)
        except (OSError, RuntimeError, ValueError):
            if log.exists():
                print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-35:]), file=sys.stderr)
            raise
        finally:
            if monitor:
                monitor.stopping.set()
            try:
                job.close()
            finally:
                if process is not None:
                    process.wait(timeout=10)
                if monitor:
                    monitor.finish()
        result["shutdown"] = "console stop and owned job drained" if console_stop else "owned job drained"
        result["console_exit_code"] = console_exit_code
        result["memory_guard"] = {"active": guarded}
        if monitor:
            result["memory_guard"].update(
                samples=monitor.samples, host_floor_mb=host_floor_mb(monitor.latest, monitor.configured_floor),
                gpu_floor_mb=2048,
            )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--guarded", action="store_true", help="Fail closed on memory pressure or missing/stale samples.")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300 or args.device_index < 0:
        parser.error("timeout must be in (0, 300] and device-index must be nonnegative")
    try:
        result = run_check(args.source.resolve(), args.timeout, args.device_index, args.guarded)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
