#!/usr/bin/env python3
"""Drama 3's CUDA server inside a reservation of the machine's workload governor (``SPARK_GOVERNOR``,
``workload.py``). app/voice.py starts the engine through this file when ``DRAMA3_MANAGED_GUARD=1``.

    python3 spark/managed_voice.py <drama3-generate> --model ... --serve 1   # the engine, under a reservation
    python3 spark/managed_voice.py --check                                   # before every part of a take

Before it asks for a reservation it stops the studio's managed ComfyUI server when that is idle (and refuses while
it renders), so the two never hold the memory together. Inside the reservation the engine runs with the governor's
telemetry beside it and a memory kill-guard of its own (``DRAMA3_GUARD_GB``, 8 GB, four times a second): the
app's guard ends the launcher, this one ends the engine itself. Its stdin and stderr are the app's, as without it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workload  # noqa: E402

GIB_DEFAULT = 55.0  # Drama 3's 41.6 GB peak on a Spark, with Whisper and room beside it


def check() -> int:
    workload.gate("drama3")
    return 0


def free_idle_studio() -> None:
    """The studio's managed renderer stopped when idle; a busy one is waited for, never interrupted."""

    if workload.live_identity("comfy") is None:
        return
    with urllib.request.urlopen(f"http://127.0.0.1:{workload.port()}/queue", timeout=10) as answer:
        queue = json.load(answer)
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("the studio's renderer is busy; the voice waits for it")
    subprocess.run(["bash", str(HERE / "comfy.sh"), "stop"], check=True, capture_output=True, text=True, timeout=40)
    for _ in range(60):
        if workload.live_identity("comfy") is None:
            break
        time.sleep(0.5)
    workload.reconcile_dead(workload.owner())


def available_gb() -> float:
    with open("/proc/meminfo") as info:
        for line in info:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 ** 2
    return -1.0


def run(command: list[str]) -> int:
    telemetry = workload.Telemetry("drama3")
    telemetry.start()
    requested = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: requested.set())
    signal.signal(signal.SIGINT, lambda *_: requested.set())
    floor = float(os.environ.get("DRAMA3_GUARD_GB", 8))
    engine = None
    try:
        time.sleep(2)
        if telemetry.failure:
            raise RuntimeError("; ".join(telemetry.failure))
        engine = subprocess.Popen(command)
        workload.write_identity("drama3", engine.pid)
        while engine.poll() is None:
            free = available_gb()
            if 0 <= free < floor:
                print(f"MemAvailable {free:.1f} GB < {floor:g} GB: the memory guard killed Drama 3 (pid {engine.pid})",
                      file=sys.stderr, flush=True)
                engine.kill()
                break
            if requested.is_set() or telemetry.failure:
                engine.terminate()
                break
            requested.wait(0.25)
        try:
            engine.wait(timeout=20)
        except subprocess.TimeoutExpired:
            engine.kill()
            engine.wait(timeout=10)
        if telemetry.failure:
            raise RuntimeError("; ".join(telemetry.failure))
        return 0 if requested.is_set() else max(0, engine.returncode)
    finally:
        if engine is not None and engine.poll() is None:
            engine.terminate()
            engine.wait(timeout=20)
        telemetry.done.set()
        telemetry.join(timeout=25)
        try:
            if engine is not None and json.loads(workload.pidfile("drama3").read_text()).get("pid") == engine.pid:
                workload.pidfile("drama3").unlink()
        except (OSError, ValueError):
            pass


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        return check()
    if not argv:
        print("usage: managed_voice.py <engine> [args ...] | --check", file=sys.stderr)
        return 2
    if os.environ.get("SPARK_ADMISSION_TOKEN"):
        return run(argv)
    free_idle_studio()
    gib = workload.number("SPARK_GOVERNOR_VOICE_GIB", GIB_DEFAULT, least=1.0)
    launch = workload.client_command(workload.voice_owner(), "llm", gib, [sys.executable, str(Path(__file__).resolve()), *argv])
    os.execv(launch[0], launch)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # noqa: BLE001 - the app reads this line from stderr as the reason
        print(f"Drama 3 was not started or was stopped by the machine's limits: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
