#!/usr/bin/env python3
"""The studio's ComfyUI server inside a reservation of the machine's workload governor (``SPARK_GOVERNOR``,
``workload.py``). ``comfy.sh`` uses it only when that is set; otherwise it starts ComfyUI as it always has.

    python3 spark/managed_comfy.py run <the ComfyUI command ...>   # comfy.sh run
    python3 spark/managed_comfy.py stop                            # comfy.sh stop

``run`` first releases this owner's reservations whose servers have ended, frees another ComfyUI's idle cache when
``SPARK_GOVERNOR_FREE_URL`` names one, waits for the governor's preflight to allow the start, then starts itself
again under ``admission_client.py --run``. Inside the reservation it starts the server at low priority, with:

- the memory guard beside it (``memory_guard.py watch``, twenty times a second), exactly as ``comfy.sh`` starts it
  without a governor, and the same plain loop on ``SPARK_MEMORY_FLOOR_MB`` if neither Python can run the guard;
- the governor's telemetry (``workload.Telemetry``): memory under the floor, a hot or throttled GPU, a hot platform
  zone or sustained memory pressure stop the server.

The server's process identity is written to ``$PREFIX/managed-comfy-<port>.json``: the start gate checks it, and
``stop`` ends that server only.
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

PREFIX = workload.PREFIX
GIB_DEFAULT = 85.0  # a cold render's peak on a Spark with room to spare (memory_guard.py's measured routes)


def stop() -> int:
    current = workload.live_identity("comfy")
    if current is None:
        print("the studio's managed server is not running")
        return 0
    os.kill(current["pid"], signal.SIGTERM)
    _, process_binding, _ = workload.modules()
    for _ in range(100):
        live = process_binding.process(current["pid"])
        if not live or live["state"] == "Z":
            break
        time.sleep(0.1)
    print("stopped the studio's managed server")
    return 0


def admit(command: list[str]) -> int:
    """Outside a reservation: ask for one, and run this file's ``run`` again inside it."""

    who = workload.owner()
    gib = workload.number("SPARK_GOVERNOR_RESERVE_GIB", GIB_DEFAULT, least=1.0)
    workload.reconcile_dead(who)
    workload.free_other_comfy()
    # the preflight is advisory (a freed cache comes back slowly); the reservation below is what counts
    for _ in range(15):
        result = workload.rpc({"op": "preflight", "owner": who, "workload": "h3", "phase": "start", "residency": "new",
                               "estimated_incremental_gib": gib, "min_free_gib": workload.min_free_gib()})
        if result.get("allowed"):
            break
        time.sleep(2)
    else:
        raise RuntimeError("the workload governor did not admit the renderer: " + json.dumps(result.get("detail", result))[:600])
    launch = workload.client_command(who, "h3", gib, [sys.executable, str(Path(__file__).resolve()), "run", *command])
    code = 3
    # a denied launch with no live reservation of ours is tried again: the other server's release may still be settling
    for _ in range(3):
        code = subprocess.call(launch)
        if code != 3:
            return code
        status = workload.rpc({"op": "status", "owner": who})
        if not status.get("allowed") or any(r.get("owner") == who for r in status.get("reservations", [])):
            return code
        time.sleep(3)
    return code


def floor_mb() -> int:
    try:
        return int(os.environ.get("SPARK_MEMORY_FLOOR_MB") or 12000)
    except ValueError:
        return 12000


def available_mb() -> int:
    with open("/proc/meminfo") as info:
        for line in info:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return -1


def guard(pid: int, pythons: list[str]) -> subprocess.Popen | None:
    """The memory guard beside the server, with the first Python that can run it (None when none is left)."""

    while pythons:
        python = pythons.pop(0)
        try:
            return subprocess.Popen([python, str(HERE / "memory_guard.py"), "watch", "--pid", str(pid)])
        except OSError:
            continue
    return None


def run(command: list[str]) -> int:
    """Inside the reservation: the server, its memory guard and the telemetry, until one of them ends it."""

    telemetry = workload.Telemetry("comfy")
    telemetry.start()
    requested = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: requested.set())
    signal.signal(signal.SIGINT, lambda *_: requested.set())
    time.sleep(2)
    if telemetry.failure:
        raise RuntimeError("; ".join(telemetry.failure))
    # a kernel cache of the studio's own: a shared one may belong to another user or a container
    os.environ.setdefault("TRITON_CACHE_DIR", str(PREFIX / "triton-cache"))
    Path(os.environ["TRITON_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(["nice", "-n", "19", *command])
    pythons = [p for p in (str(PREFIX / "venv/bin/python"), "python3") if p == "python3" or os.access(p, os.X_OK)]
    watch, plain = guard(server.pid, pythons), False
    workload.write_identity("comfy", server.pid)
    why = ""
    try:
        while server.poll() is None:
            if watch is not None and watch.poll() is not None and watch.returncode not in (0, 3):
                watch = guard(server.pid, pythons)  # it could not run with this Python: the next one
            if watch is None and not plain:
                plain = True
                print(f"[tensorfold] memory floor: the guard could not start; holding {floor_mb()} MB with a plain loop", flush=True)
            if plain:
                free = available_mb()
                if 0 <= free < floor_mb():
                    print(f"[tensorfold] memory floor: {free} MB available, under the floor of {floor_mb()} MB: stopping ComfyUI", flush=True)
                    server.kill()
                    break
            if telemetry.failure or requested.is_set():
                why = "; ".join(telemetry.failure) or "a stop was asked for"
                print("[studio] stopping the managed server: " + why, flush=True)
                try:
                    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{workload.port()}/interrupt", data=b"{}",
                                                                  headers={"Content-Type": "application/json"}), timeout=5).close()
                except OSError:
                    pass
                server.terminate()
                break
            requested.wait(0.2)
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        return 1 if telemetry.failure else (0 if requested.is_set() else max(0, server.returncode))
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=20)
        if watch is not None:
            if watch.poll() is None:
                watch.terminate()
            watch.wait(timeout=10)
        telemetry.done.set()
        telemetry.join(timeout=25)
        try:
            if json.loads(workload.pidfile("comfy").read_text()).get("pid") == server.pid:
                workload.pidfile("comfy").unlink()
        except (OSError, ValueError):
            pass


def main(argv: list[str]) -> int:
    if argv == ["stop"]:
        return stop()
    if not argv or argv[0] != "run" or len(argv) < 2:
        print("usage: managed_comfy.py run <command ...> | stop", file=sys.stderr)
        return 2
    command = argv[1:]
    return run(command) if os.environ.get("SPARK_ADMISSION_TOKEN") else admit(command)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # noqa: BLE001 - the reason goes to comfy.log, as the guard's does
        print(f"[studio] the managed server stopped: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
