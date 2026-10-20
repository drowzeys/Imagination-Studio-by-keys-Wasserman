"""A local model server on this machine that steps aside for renders (``STUDIO_LOCAL_CONTROL``), e.g. NVIDIA Nemotron
3.5 Bolt in llama.cpp's server: about 26 GB loaded, which a DGX Spark's larger clips need back.

``STUDIO_LOCAL_CONTROL`` names a program the Studio runs with one word: ``status`` (exit 0 while the server runs),
``stop``, ``start``, and ``pid`` (its process id, printed). Unset, nothing here runs. The Studio uses it as it uses
Drama 3 and MiniMax Music 3 kept loaded (voice.py, music.py):

- before any job of this machine's queue (server.perform), the server is stopped, and its memory counts as that job's
  when a clip is checked against the machine's memory (server.held_room, ``room_mb``);
- when the queue is empty again, a server the Studio stopped is started again (``resume``); one the person stopped
  stays stopped.

An agent turn on the local harness (agents.drive) that asks the server while it is stepped aside waits for it
(``away``, ``stops``): its next answer comes once the queue is empty and the server answers again, and an answer cut
off by a stop is asked again. A single brain question asked during a render finds no server: it answers after."""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time

TIMEOUT = 120  # seconds for the control program's stop or start
mutex = threading.Lock()
state = {"stopped_by_us": False, "starting": False, "stops": 0}


def control() -> list[str]:
    return shlex.split(os.environ.get("STUDIO_LOCAL_CONTROL", "").strip())


def run(word: str, timeout: int = 10) -> subprocess.CompletedProcess | None:
    argv = control()
    if not argv:
        return None
    try:
        return subprocess.run([*argv, word], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def running() -> bool:
    done = run("status")
    return bool(done and done.returncode == 0)


def available_mb() -> int | None:
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


def held_mb() -> int:
    """The memory the server's process holds (on a GB10 its GPU memory is the machine's own): nvidia-smi's answer for
    its pid, else its resident size."""

    done = run("pid")
    pid = done.stdout.strip().splitlines()[-1].strip() if done and done.returncode == 0 and done.stdout.strip() else ""
    if not pid.isdigit():
        return 0
    try:
        smi = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
        for line in smi.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 2 and parts[0] == pid and parts[1].isdigit():
                return int(parts[1])
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        with open(f"/proc/{pid}/status") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def room_mb() -> int | None:
    """While the server runs: the memory a job would have once it is stopped (MB). None when it is not running."""

    if not control() or not running():
        return None
    now = available_mb()
    return None if now is None else now + held_mb()


def release(why: str) -> bool:
    """Before a job of this machine's queue: stop the server if it runs. True when it was stopped here."""

    if not control():
        return False
    with mutex:
        if not running():
            return False
        done = run("stop", TIMEOUT)
        if not done or done.returncode != 0:
            return False
        state["stopped_by_us"] = True
        state["stops"] += 1
        before = None
        for _ in range(30):  # the memory comes back a little after the process ends
            now = available_mb()
            if now is None or (before is not None and now - before < 200):
                break
            before = now
            time.sleep(1)
        return True


def resume() -> bool:
    """When the queue is empty: start the server again if the Studio stopped it. True when it was started."""

    if not control():
        return False
    with mutex:
        if not state["stopped_by_us"]:
            return False
        state["stopped_by_us"], state["starting"] = False, True
    try:
        done = run("start", TIMEOUT)
        return bool(done and done.returncode == 0)
    finally:
        state["starting"] = False


def away() -> bool:
    """The server is stepped aside for renders, or being started again after them (a turn waits; it did not fail)."""

    return bool(control()) and (state["stopped_by_us"] or state["starting"])
