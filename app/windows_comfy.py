"""Privately owned, guarded ComfyUI lifecycle for the Windows Studio queue."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from windows_memory import single_gpu_uuid


class WindowsComfy:
    def __init__(self, check, prepare):
        self.api = check
        self.prepare = prepare
        self.job = self.process = self.monitor = self.log = self.temporary = None
        self.base = ""
        self.directory = None
        self.loaded = False
        self.ready = False

    def start(self):
        root = Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio"
        python = str(root / "gpu-venv" / "Scripts" / "python.exe")
        source = root / "ComfyUI"
        self.api.verify_source(source)
        try:
            self.temporary = tempfile.TemporaryDirectory(prefix="studio-comfy-warm-")
            self.directory = Path(self.temporary.name)
            self.prepare(self.directory)
            self.log = (self.directory / "startup.log").open("wb")
            self.job = self.api.OwnedJob(self.api.CalibrationCase.process_commit_mb, self.api.CalibrationCase.job_commit_mb)
            self.monitor = self.api.MemoryWatchdog(self.job, 0, self.log, python=python,
                                                   wddm_device_uuid=single_gpu_uuid())
            self.monitor.wait_ready()
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            self.base = f"http://127.0.0.1:{port}"
            command = [
                python, "-X", "utf8", str(self.api.APP.parent / "scripts" / "windows-comfy-calibration-launcher.py"),
                "--comfy", str(source / "main.py"), "--gpu-limit-mb", str(self.api.CalibrationCase.gpu_limit_mb), "--",
                "--listen", "127.0.0.1", "--port", str(port), "--base-directory", str(self.directory),
                "--cuda-device", "0", "--offline", "--disable-all-custom-nodes", "--disable-auto-launch",
                "--disable-dynamic-vram", "--use-pytorch-cross-attention", "--disable-cuda-malloc",
                "--whitelist-custom-nodes", "windows_calibration",
            ]
            self.process = self.job.spawn(
                command, cwd=source, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                env={**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"},
            )
            deadline = time.monotonic() + 120
            while True:
                self.check()
                try:
                    stats = self.api.read_json(self.base, "/system_stats")
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("the warm ComfyUI backend did not start within 120 seconds")
                    time.sleep(0.25)
                else:
                    self.api.validate_identity(stats, str(self.directory))
                    break
            self.api.validate_ready(self.api.read_json(self.base, "/queue"), self.api.read_json(self.base, "/object_info"))
            owner = self.api.read_json(self.base, "/windows_calibration/metrics")["process_identity"]
            self.monitor.start_backend_handle(self.process, owner)
            self.check()
            self.ready = True
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            self.close()
            raise

    def check(self):
        if self.monitor:
            self.monitor.check()
        if self.process and self.process.poll() is not None:
            raise RuntimeError("the owned ComfyUI backend exited; unload it before retrying")

    def post(self, path: str, body: dict) -> dict:
        self.check()
        request = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10 if path == "/prompt" else 3) as response:
            return json.loads(response.read(1 << 20))

    def pending(self, prompt_id: str) -> bool:
        queue = self.api.read_json(self.base, "/queue")
        for name in ("queue_running", "queue_pending"):
            if not isinstance(queue.get(name), list):
                raise RuntimeError("the owned ComfyUI backend returned an invalid queue")
            for row in queue[name]:
                if not isinstance(row, list) or len(row) < 2:
                    raise RuntimeError("the owned ComfyUI backend returned an invalid queue entry")
                if row[1] == prompt_id:
                    return True
        return False

    def cancel(self, prompt_id: str):
        if self.process is not None and self.process.poll() is not None:
            return
        try:
            answer = self.post(f"/api/jobs/{prompt_id}/cancel", {})
        except urllib.error.HTTPError as error:
            if error.code != 404 or self.pending(prompt_id):
                raise
        else:
            if answer.get("cancelled") is not True and self.pending(prompt_id):
                raise RuntimeError("ComfyUI did not accept cancellation of the owned prompt")

    def drain(self, prompt_id: str):
        self.cancel(prompt_id)
        deadline = time.monotonic() + 15
        while self.pending(prompt_id):
            self.check()
            if time.monotonic() >= deadline:
                self.close()
                raise RuntimeError("the cancelled prompt did not drain; its owned ComfyUI backend was stopped")
            time.sleep(0.1)

    def summary(self) -> dict:
        running = self.process is not None and self.process.poll() is None
        why = self.monitor.reason if self.monitor and self.monitor.failed.is_set() else ""
        return {"running": running and not why, "loaded": running and self.loaded and not why,
                "address": self.base if running and not why else "", "why": why}

    def accounting(self) -> dict:
        self.check()
        job, process = self.job, self.process
        if not self.ready or job is None or process is None:
            return {"commit_mb": 0, "reserved_mb": 0}
        job.process_accounting(process)
        self.api.validate_identity(self.api.read_json(self.base, "/system_stats"), str(self.directory))
        stats = self.api.read_json(self.base, "/windows_calibration/metrics")
        commit = stats.get("process_memory", {}).get("private_commit_bytes")
        reserved = stats.get("reserved_bytes")
        if (type(commit) is not int or not 0 <= commit <= self.api.CalibrationCase.process_commit_mb << 20
                or type(reserved) is not int or not 0 <= reserved <= self.api.CalibrationCase.gpu_limit_mb << 20):
            raise RuntimeError("the owned backend returned invalid current memory accounting")
        return {"commit_mb": commit // (1 << 20), "reserved_mb": reserved // (1 << 20)}

    def close(self):
        self.ready = False
        if self.monitor:
            self.monitor.stopping.set()
        if self.job:
            self.job.close()
        if self.process:
            self.process.wait(timeout=10)
        try:
            if self.monitor:
                self.monitor.finish()
        finally:
            if self.log:
                self.log.close()
            if self.temporary:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        self.temporary.cleanup()
                        break
                    except OSError as error:
                        if getattr(error, "winerror", None) not in (32, 33) or time.monotonic() >= deadline:
                            raise
                        time.sleep(.05)
            self.job = self.process = self.monitor = self.log = self.temporary = None
            self.base = ""
            self.loaded = False
