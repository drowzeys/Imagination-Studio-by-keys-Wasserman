"""Pinned, small local brain for the experimental Windows ARM64 preview."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

RUNTIME_VERSION = "b12063"
MODEL_REVISION = "bc640142c66e1fdd12af0bd68f40445458f3869b"
MODEL_NAME = "Qwen3-4B-Q4_K_M.gguf"
MODEL_ID = "Qwen3-4B"
RELEASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_VERSION}"
FILES = {
    "llama.zip": (f"{RELEASE}/llama-{RUNTIME_VERSION}-bin-win-cuda-13.4-arm64.zip", 156641439,
                  "0456c5ecb50889f3952e60730e8d3aeca872b89e789385aaa41f9fa83dcc7bc2"),
    "cudart.zip": (f"{RELEASE}/cudart-llama-bin-win-cuda-13.4-arm64.zip", 153262407,
                   "642dcde8805b3e3165ca710a5443b3b4044b27d96bd3ee3132473988c9bcb774"),
    MODEL_NAME: (f"https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/{MODEL_REVISION}/{MODEL_NAME}",
                 2497280256, "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"),
}
LICENSES = {
    "LICENSE-llama.cpp": "https://raw.githubusercontent.com/ggml-org/llama.cpp/2cbca389f515df455412dafa07e2b1359cc72a6d/LICENSE",
    "LICENSE-Qwen3": "https://huggingface.co/Qwen/Qwen3-4B/resolve/1cfa9a7208912126459214e8b04321603b3df60c/LICENSE",
}


def verify_file(path: Path, size: int, expected: str) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise RuntimeError(f"missing or wrong-sized file: {path.name}; run setup-windows-brain.ps1")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 << 20):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError(f"SHA-256 verification failed: {path.name}")


def fetch(url: str, path: Path, size: int, digest: str) -> None:
    if path.exists():
        verify_file(path, size, digest)
        return
    partial = path.with_suffix(path.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        raise RuntimeError(f"oversized partial download: {partial.name}")
    if offset < size:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        print(f"[windows-brain] downloading {path.name}", flush=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as answer:
            append = bool(offset and answer.status == 206)
            if append and not answer.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                raise RuntimeError(f"download resumed at the wrong offset: {path.name}")
            written = offset if append else 0
            deadline = time.monotonic() + 1800
            with partial.open("ab" if append else "wb") as target:
                while chunk := answer.read(8 << 20):
                    written += len(chunk)
                    if written > size or time.monotonic() > deadline:
                        raise RuntimeError(f"download exceeded its size or time limit: {path.name}")
                    target.write(chunk)
    verify_file(partial, size, digest)
    partial.replace(path)


def install(root: Path, download: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if download and shutil.disk_usage(root).free < 4 << 30:
        raise RuntimeError("the small-brain setup needs at least 4 GiB of free disk")
    for name, (url, size, digest) in FILES.items():
        if download:
            fetch(url, root / name, size, digest)
        else:
            verify_file(root / name, size, digest)
    for name, url in LICENSES.items():
        path = root / name
        if download and not path.exists():
            with urllib.request.urlopen(url, timeout=30) as answer:
                data = answer.read(1 << 20)
            if not data:
                raise RuntimeError(f"empty license download: {name}")
            path.write_bytes(data)
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"missing license: {name}")
    runtime = root / "bin"
    if download:
        runtime.mkdir(exist_ok=True)
        for name in ("llama.zip", "cudart.zip"):
            with zipfile.ZipFile(root / name) as archive:
                for entry in archive.infolist():
                    destination = (runtime / entry.filename).resolve()
                    if not destination.is_relative_to(runtime.resolve()):
                        raise RuntimeError("the runtime archive contains a path outside its folder")
                archive.extractall(runtime)
    executables = list(runtime.rglob("llama-server.exe"))
    if len(executables) != 1:
        raise RuntimeError("the verified runtime must contain exactly one llama-server.exe")
    return executables[0]


def serve(root: Path, port: int, cpu: bool, check: bool) -> dict:
    from windows_memory import DedicatedMemory, WindowsSignals, gpu_memory_warning, host_memory
    from windows_process import OwnedJob

    executable = install(root, False)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"port {port} is already in use; no existing process was stopped")
    floor = os.environ.get("SPARK_MEMORY_FLOOR_MB", "")
    if floor and (not floor.isdigit() or int(floor) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer")
    env = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_")}
    dedicated = None
    if not cpu:
        device = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader", "--id=0"],
                                capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        dedicated = DedicatedMemory(device)
        env["CUDA_VISIBLE_DEVICES"] = device
    job = None
    process = None
    log_path = root / "server.log"
    try:
        with WindowsSignals() as signals:
            def room():
                used, limit, _, low = signals.read()
                _, free = host_memory()
                if low or not free or free < int(floor or "0"):
                    raise RuntimeError("Windows reports low physical memory or the configured host reserve was reached")
                if limit - used < 2048:
                    raise RuntimeError("Windows commit headroom is below 2048 MiB")
                if dedicated:
                    free_gpu = dedicated.read()[1]
                    if free_gpu < 2048:
                        raise RuntimeError(gpu_memory_warning(free_gpu, 2048, device))

            room()
            if dedicated:
                free_gpu = dedicated.read()[1]
                if free_gpu < 6144:
                    raise RuntimeError(gpu_memory_warning(free_gpu, 6144, device))
            job = OwnedJob(process_commit_mb=12288, job_commit_mb=12288)
            with log_path.open("w", encoding="utf-8") as log:
                argv = [
                    str(executable), "--model", str(root / MODEL_NAME), "--alias", MODEL_ID,
                    "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "4096", "--parallel", "1",
                    "--n-predict", "1024", "--batch-size", "256", "--ubatch-size", "128",
                    "--gpu-layers", "0" if cpu else "99", "--jinja", "--no-context-shift",
                    "--reasoning", "off", "--cors-origins", "localhost", "--no-cors-credentials",
                    "--no-webui", "--log-verbosity", "4",
                ]
                if not cpu:
                    argv += ["--device", "CUDA0"]
                process = job.spawn(
                    argv,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                )
                deadline = time.monotonic() + 120
                ready = False
                while process.poll() is None:
                    room()
                    if not ready:
                        try:
                            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as answer:
                                ready = answer.status == 200
                        except (OSError, urllib.error.URLError):
                            if time.monotonic() >= deadline:
                                raise RuntimeError("the local brain did not become ready within 120 seconds")
                        if ready:
                            print(f"[windows-brain] ready: http://127.0.0.1:{port}/v1 ({'CPU' if cpu else 'CUDA'})", flush=True)
                            if check:
                                return {"ok": True, "model": MODEL_ID, "backend": "cpu" if cpu else "cuda",
                                        **job.process_accounting(process)}
                    time.sleep(0.25)
                raise RuntimeError(f"the local brain exited with code {process.returncode}; see {log_path.name}")
    finally:
        if job:
            job.close()
        if process:
            process.wait(timeout=10)
        if dedicated:
            dedicated.__exit__(None, None, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        if os.name != "nt" or platform.machine().lower() not in ("arm64", "aarch64"):
            raise RuntimeError("this pinned runtime requires native Windows ARM64")
        if not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if args.serve or args.check:
            result = serve(args.root, args.port, args.cpu, args.check)
        else:
            install(args.root, args.download)
            result = {"ok": True, "runtime": RUNTIME_VERSION, "model": MODEL_ID, "scope": "files verified; no inference"}
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
