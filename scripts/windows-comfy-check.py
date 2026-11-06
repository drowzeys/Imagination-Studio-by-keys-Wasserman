#!/usr/bin/env python3
"""Bounded no-model ComfyUI readiness check; never submits a workflow."""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import asdict, replace
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
from windows_memory import Snapshot, gpu_memory_warning, host_floor_mb, pressure, process_memory  # noqa: E402
from windows_process import OwnedJob  # noqa: E402

SAMPLE_START_TIMEOUT = 30
SAMPLE_FRESHNESS = 2
NORMAL_PRIORITY = 0x20
BELOW_NORMAL_PRIORITY = 0x4000


class MemoryWatchdog:
    """Sample in a separate process so a stalled CUDA query cannot block the supervisor."""

    def __init__(self, job: OwnedJob, device_index: int, log, python: str | None = None,
                 wddm_device_uuid: str | None = None, pressure_policy=None):
        self.job = job
        self.stopping = threading.Event()
        self.failed = threading.Event()
        self.ready = threading.Event()
        self.messages = queue.Queue(maxsize=8)
        self.state_lock = threading.Lock()
        self.reason = ""
        self.latest = None
        self.initial = None
        self.minima = {}
        self.max_commit_used_mb = 0
        self.samples = 0
        self.refused_sample = None
        self.backend_thread = None
        self.backend_stopping = threading.Event()
        self.backend_ready = threading.Event()
        self.backend_samples = 0
        self.backend_min_free_mb = None
        self.backend_budget = None
        self.backend_handle = None
        self.wddm_device_uuid = wddm_device_uuid
        self.pressure_policy = pressure_policy
        self.budget_basis = "owned ComfyUI CUDA context over HTTP"
        self.configured_floor = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
        command = [python or sys.executable, "-X", "utf8", str(APP / "windows_memory.py"), "--watch", "--device-index", str(device_index)]
        if wddm_device_uuid:
            command += ["--wddm-only", "--device-uuid", wddm_device_uuid]
        self.process = job.spawn(
            command,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000 | (NORMAL_PRIORITY if wddm_device_uuid else 0),
        )
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
        if hasattr(self, "process"):
            latest = self.latest
            print("[comfy-check] sampler diagnostics: " + json.dumps({
                "samples": self.samples, "age_s": time.monotonic() - latest.at if latest else None,
                "queued": self.messages.qsize(), "sampler_exit": self.process.poll(),
                "reader_alive": self.reader.is_alive(), "supervisor_alive": self.supervisor.is_alive(),
                "wddm_only": bool(self.wddm_device_uuid),
            }), file=sys.stderr)
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
                # Current freshness is checked independently; old history is not a valid trend baseline.
                trend = previous if previous is None or snapshot.at - previous.at <= SAMPLE_FRESHNESS else None
                reason = (self.pressure_policy(snapshot, trend) if self.pressure_policy is not None
                          else pressure(snapshot, self.configured_floor, trend))
            except (TypeError, ValueError) as error:
                self.fail(f"invalid memory sample: {error}")
                return
            with self.state_lock:
                if self.initial is None:
                    self.initial = snapshot
                self.latest = snapshot
                for name, value in (
                    ("host_available_mb", snapshot.host_free_mb),
                    ("host_commit_headroom_mb", snapshot.host_commit_limit_mb - snapshot.host_commit_used_mb),
                    ("cuda_budget_free_mb", snapshot.gpu_free_mb),
                    ("dedicated_gpu_free_mb", snapshot.gpu_dedicated_free_mb),
                ):
                    self.minima[name] = min(self.minima.get(name, value), value)
                self.max_commit_used_mb = max(self.max_commit_used_mb, snapshot.host_commit_used_mb)
                self.samples += 1
                if reason:
                    self.refused_sample = asdict(snapshot)
            if reason:
                self.fail(reason)
                return
            previous = snapshot
            self.ready.set()

    def check(self):
        with self.state_lock:
            latest = self.latest
        if (latest is not None and not self.stopping.is_set() and not self.backend_stopping.is_set()
                and time.monotonic() - latest.at > SAMPLE_FRESHNESS):
            self.fail("memory sampler stopped answering")
        with self.state_lock:
            if self.failed.is_set():
                raise RuntimeError(f"memory watchdog refused this check: {self.reason}")

    def wait_ready(self):
        if not self.ready.wait(SAMPLE_START_TIMEOUT + 1):
            self.fail("memory sampler startup exceeded its deadline")
        self.check()

    def start_backend(self, base: str, directory: str):
        if self.backend_thread is not None:
            raise RuntimeError("backend budget monitoring is already running")

        def observe():
            previous = None
            while not self.stopping.is_set() and not self.failed.is_set():
                try:
                    try:
                        stats = read_json(base, "/system_stats", timeout=1)
                    except (urllib.error.URLError, TimeoutError, ConnectionError):
                        remaining = SAMPLE_FRESHNESS - (time.monotonic() - previous.at) if previous is not None else 0
                        if remaining <= 0:
                            raise
                        stats = read_json(base, "/system_stats", timeout=min(1, remaining))
                    validate_identity(stats, directory)
                    total, free = backend_budget(stats)
                    with self.state_lock:
                        latest = self.latest
                    if latest is None or time.monotonic() - latest.at > SAMPLE_FRESHNESS:
                        raise RuntimeError("host memory samples are stale")
                    current = replace(latest, gpu_total_mb=total, gpu_free_mb=free, at=time.monotonic())
                    trend = previous if previous is None or current.at - previous.at <= SAMPLE_FRESHNESS else None
                    reason = pressure(current, self.configured_floor, trend)
                    if reason:
                        raise RuntimeError(reason)
                except (OSError, RuntimeError, ValueError) as error:
                    if self.backend_stopping.is_set() or self.stopping.is_set():
                        return
                    self.fail(f"backend budget monitoring failed: {error}")
                    self.backend_ready.set()
                    return
                previous = current
                with self.state_lock:
                    self.backend_samples += 1
                    self.backend_budget = {"total_mb": total, "free_mb": free}
                    self.backend_min_free_mb = free if self.backend_min_free_mb is None else min(self.backend_min_free_mb, free)
                self.backend_ready.set()
                self.stopping.wait(0.5)

        self.backend_thread = threading.Thread(target=observe, daemon=True)
        self.backend_thread.start()
        if not self.backend_ready.wait(2):
            self.fail("backend memory reporting did not answer within its deadline")
        self.check()

    def start_backend_handle(self, process, identity: dict):
        """Use fresh dedicated counters and the owned process cap without blocking its busy CUDA context."""
        if self.backend_thread is not None:
            raise RuntimeError("backend budget monitoring is already running")
        limit = self.job.commitment()["process_commit_limit_bytes"]
        if not limit:
            raise RuntimeError("the handle monitor requires an applied process commit cap")
        from windows_backend_handle import open_owned
        self.backend_handle = open_owned(self.job, identity)
        self.budget_basis = "fresh UUID/LUID-bound WDDM dedicated usage and owned private-commit cap; not a CUDA allocator budget"

        def observe():
            previous = None
            while not self.stopping.is_set() and not self.backend_stopping.is_set() and not self.failed.is_set():
                try:
                    if process.poll() is not None:
                        raise RuntimeError("the owned backend exited")
                    counters = self.backend_handle.read()
                    private = counters["private_commit_bytes"]
                    if type(private) is not int or private < 0:
                        raise RuntimeError("the owned backend returned invalid private commitment")
                    if private >= limit:
                        raise RuntimeError("the owned backend reached its process commit cap")
                    with self.state_lock:
                        latest = self.latest
                    if latest is None or time.monotonic() - latest.at > SAMPLE_FRESHNESS:
                        raise RuntimeError("host/WDDM memory samples are stale")
                    current = replace(latest, at=time.monotonic())
                    reason = pressure(current, self.configured_floor, previous)
                    if reason:
                        raise RuntimeError(reason)
                except (OSError, RuntimeError, ValueError) as error:
                    if self.stopping.is_set() or self.backend_stopping.is_set():
                        return
                    self.fail(f"owned handle budget monitoring failed: {error}")
                    self.backend_ready.set()
                    return
                previous = current
                with self.state_lock:
                    self.backend_samples += 1
                    self.backend_budget = {"total_mb": current.gpu_dedicated_total_mb, "free_mb": current.gpu_dedicated_free_mb,
                                           "private_commit_mb": private // (1 << 20)}
                    self.backend_min_free_mb = current.gpu_dedicated_free_mb if self.backend_min_free_mb is None else min(
                        self.backend_min_free_mb, current.gpu_dedicated_free_mb)
                self.backend_ready.set()
                self.stopping.wait(.25)

        self.backend_thread = threading.Thread(target=observe, daemon=True)
        self.backend_thread.start()
        if not self.backend_ready.wait(2):
            self.fail("owned handle memory reporting did not answer within its deadline")
        self.check()

    def finish(self, check_result=True):
        self.process.wait(timeout=10)
        self.reader.join(timeout=3)
        self.supervisor.join(timeout=SAMPLE_FRESHNESS + 1)
        if self.backend_thread:
            self.backend_thread.join(timeout=2)
        if self.reader.is_alive() or self.supervisor.is_alive() or (self.backend_thread and self.backend_thread.is_alive()):
            raise RuntimeError("memory watchdog threads did not stop within their deadlines")
        self.process.stdout.close()
        if self.backend_handle:
            try:
                self.backend_handle.wait()
            finally:
                self.backend_handle.close()
        if check_result:
            self.check()

    def begin_backend_shutdown(self):
        self.backend_stopping.set()

    def metrics(self):
        with self.state_lock:
            if self.initial is None or self.latest is None:
                raise RuntimeError("the memory watchdog has no valid samples")
            return {"samples": self.samples, "minima": dict(self.minima),
                    "refused_sample": self.refused_sample,
                    "max_host_commit_used_mb": self.max_commit_used_mb,
                    "initial": asdict(self.initial), "final": asdict(self.latest),
                    "sampler_cuda_scope": "WDDM dedicated-memory budget, no sampler CUDA context" if self.wddm_device_uuid else "sampler process context, not backend headroom",
                    "backend_budget_basis": self.budget_basis,
                    "backend_budget": self.backend_budget, "backend_budget_samples": self.backend_samples,
                    "backend_min_free_mb": self.backend_min_free_mb,
                    "host_policy": "Windows low-memory signal and commit headroom",
                    "configured_host_floor_mb": host_floor_mb(self.latest, self.configured_floor),
                    "cuda_floor_mb": 2048}


def verify_source(source: Path) -> None:
    done = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=10, check=True)
    if done.stdout.strip() != COMMIT:
        raise ValueError("ComfyUI must be at the Studio's pinned revision")
    dirty = subprocess.run(["git", "-C", str(source), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10, check=True)
    if dirty.stdout.strip():
        raise ValueError("the check requires an unmodified ComfyUI checkout")


def read_json(base: str, path: str, timeout: float = 3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + path, timeout=timeout) as response:
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


def backend_budget(stats: dict) -> tuple[int, int]:
    devices = [device for device in stats.get("devices", []) if device.get("type") == "cuda"]
    if len(devices) != 1:
        raise RuntimeError("the diagnostic requires exactly one backend CUDA device")
    total, free = devices[0].get("vram_total"), devices[0].get("vram_free")
    if type(total) is not int or type(free) is not int or total <= 0 or not 0 <= free <= total:
        raise RuntimeError("ComfyUI returned an invalid backend memory budget")
    return total // (1 << 20), free // (1 << 20)

def hold_no_model(process, monitor: MemoryWatchdog, seconds: int, base: str) -> float:
    started = time.monotonic()
    deadline, next_queue = started + seconds, started
    while time.monotonic() < deadline:
        monitor.check()
        if process.poll() is not None:
            raise RuntimeError("ComfyUI exited during the sustained check")
        if time.monotonic() >= next_queue:
            state = read_json(base, "/queue")
            if not isinstance(state, dict) or state.get("queue_running") != [] or state.get("queue_pending") != []:
                raise RuntimeError("a workflow appeared during the no-model check")
            next_queue = time.monotonic() + 1
        time.sleep(0.1)
    monitor.check()
    return time.monotonic() - started


class CalibrationCase:
    gpu_limit_mb = 12 * 1024
    process_commit_mb = 12 * 1024
    job_commit_mb = 16 * 1024

    def __init__(self, prepare, exercise, custom_nodes: tuple[str, ...] = (), wddm_device_uuid: str | None = None):
        if any(name not in ("ComfyUI-MMAudio", "studio_mmaudio") for name in custom_nodes):
            raise ValueError("the calibration only permits the explicitly supported extra node packs")
        self.prepare = prepare
        self.exercise = exercise
        self.custom_nodes = custom_nodes
        if wddm_device_uuid and custom_nodes and set(custom_nodes) != {"ComfyUI-MMAudio", "studio_mmaudio"}:
            raise ValueError("the dedicated-counter monitor requires the bounded built-in or MMAudio node set")
        self.wddm_device_uuid = wddm_device_uuid


def run_check(source: Path, timeout: float, device_index: int, guarded: bool = False, hold_seconds: int = 0,
              calibration: CalibrationCase | None = None) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("this lifecycle check requires native Windows")
    if type(hold_seconds) is not int or not 0 <= hold_seconds <= 300 or (hold_seconds and not guarded):
        raise ValueError("a sustained check requires --guarded and 0-300 hold seconds")
    if calibration and (not guarded or hold_seconds or device_index != 0):
        raise ValueError("calibration requires the guarded, single-device path without a no-model hold")
    verify_source(source)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with contextlib.ExitStack() as cleanup:
        workspace = tempfile.TemporaryDirectory(prefix="studio-comfy-check-")

        def cleanup_workspace(_kind, original_error, _traceback):
            try:
                workspace.cleanup()
            except OSError as error:
                message = f"workspace cleanup failed: {error}; retained at {workspace.name}"
                print(f"[comfy-check] {message}", file=sys.stderr)
                if original_error is None:
                    raise
                original_error.add_note(message)

        cleanup.push(cleanup_workspace)
        directory = workspace.name
        log = Path(directory) / "startup.log"
        command = [
            sys.executable, "-X", "utf8", str(source / "main.py"),
            "--listen", "127.0.0.1", "--port", str(port),
            "--base-directory", directory, "--cuda-device", str(device_index),
            "--offline", "--disable-all-custom-nodes", "--disable-auto-launch",
            "--disable-dynamic-vram", "--use-pytorch-cross-attention",
        ]
        if calibration:
            calibration.prepare(Path(directory))
            command = [sys.executable, "-X", "utf8", str(APP.parent / "scripts" / "windows-comfy-calibration-launcher.py"),
                       "--comfy", str(source / "main.py"), "--gpu-limit-mb", str(calibration.gpu_limit_mb), "--",
                       *command[4:], "--disable-cuda-malloc", "--whitelist-custom-nodes", "windows_calibration",
                       *calibration.custom_nodes]
            job = OwnedJob(calibration.process_commit_mb, calibration.job_commit_mb)
        else:
            job = OwnedJob()
        process, monitor = None, None
        console_stop = False
        console_exit_code = None
        try:
            with log.open("wb") as output:
                if guarded:
                    if calibration and calibration.wddm_device_uuid:
                        monitor = MemoryWatchdog(job, device_index, output, wddm_device_uuid=calibration.wddm_device_uuid)
                    else:
                        monitor = MemoryWatchdog(job, device_index, output)
                    monitor.wait_ready()
                process = job.spawn(
                    command, cwd=source, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"},
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | (BELOW_NORMAL_PRIORITY if calibration and calibration.wddm_device_uuid else 0),
                )
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
                    if calibration and calibration.wddm_device_uuid:
                        owner = read_json(base, "/windows_calibration/metrics")["process_identity"]
                        monitor.start_backend_handle(process, owner)
                    else:
                        if calibration:
                            from windows_backend_handle import open_owned
                            owner = read_json(base, "/windows_calibration/metrics")["process_identity"]
                            monitor.backend_handle = open_owned(job, owner)
                        monitor.start_backend(base, directory)
                calibration_result = None
                memory_before = None
                launcher_before = None
                if calibration:
                    if monitor is None or monitor.latest is None:
                        raise RuntimeError("calibration requires a ready memory watchdog")
                    latest = monitor.latest
                    if latest.gpu_dedicated_free_mb < calibration.gpu_limit_mb + 2048:
                        raise RuntimeError(gpu_memory_warning(
                            latest.gpu_dedicated_free_mb, calibration.gpu_limit_mb + 2048,
                            device_index=device_index))
                    limits = job.commitment()
                    if (limits["process_commit_limit_bytes"], limits["job_commit_limit_bytes"]) != (
                        calibration.process_commit_mb << 20, calibration.job_commit_mb << 20,
                    ):
                        raise RuntimeError("Windows did not apply the calibration commit limits")
                    launcher_before = job.process_accounting(process)
                    memory_before = read_json(base, "/windows_calibration/metrics").get("process_memory")
                    if not isinstance(memory_before, dict):
                        raise RuntimeError("the calibration backend did not report its process memory")
                    calibration_result = calibration.exercise(base, Path(directory), process, monitor)
                    monitor.check()
                held = hold_no_model(process, monitor, hold_seconds, base) if hold_seconds else 0
                result = {
                    "ok": True, "commit": COMMIT, "comfyui_version": stats["system"]["comfyui_version"],
                    "pytorch_version": stats["system"]["pytorch_version"], "registered_nodes": len(nodes),
                    "checks": ["owned loopback instance", "CUDA device", "core nodes", "empty queue"],
                    "scope": "no-model startup only; custom/API nodes and dynamic VRAM disabled",
                    "steady_seconds": held,
                }
                if monitor:
                    result["checks"].append("backend process memory budget")
                if calibration:
                    result["scope"] = "explicit model calibration, not a qualified Windows render route"
                    result["calibration"] = calibration_result
                    result["job_commitment"] = job.commitment()
                    result["backend_memory_before_workflow"] = memory_before
                    memory_after = read_json(base, "/windows_calibration/metrics").get("process_memory")
                    if not isinstance(memory_after, dict):
                        raise RuntimeError("the calibration backend did not report final process memory")
                    result["backend_memory_after_workflow"] = memory_after
                    result["launcher_memory_before_workflow"] = launcher_before
                    result["launcher_memory_after_workflow"] = job.process_accounting(process)
                    if calibration_result.get("cancelled"):
                        result["scope"] = "explicit model cancellation test, not completed generation or route qualification"
                        result["checks"].append("targeted sampling cancellation confirmed")
                if monitor:
                    monitor.begin_backend_shutdown()
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    process.wait(timeout=10)
                    console_stop = True
                    console_exit_code = process.returncode
                except (OSError, subprocess.TimeoutExpired) as error:
                    print(f"[comfy-check] console stop did not complete; draining the owned job: {error}", file=sys.stderr)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"[comfy-check] check failed: {error}", file=sys.stderr)
            if calibration:
                print("[comfy-check] failed calibration commitment: " + json.dumps(job.commitment()), file=sys.stderr)
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
            result["memory_guard"].update(monitor.metrics())
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--guarded", action="store_true", help="Fail closed on memory pressure or missing/stale samples.")
    parser.add_argument("--hold-seconds", type=int, default=0, help="Hold a guarded no-model server for 0-300 seconds.")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300 or args.device_index < 0:
        parser.error("timeout must be in (0, 300] and device-index must be nonnegative")
    if not 0 <= args.hold_seconds <= 300 or (args.hold_seconds and not args.guarded):
        parser.error("hold-seconds must be in [0, 300] and a nonzero hold requires --guarded")
    try:
        result = run_check(args.source.resolve(), args.timeout, args.device_index, args.guarded, args.hold_seconds)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
