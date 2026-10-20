"""What this machine is doing, for the page's status bar and its sparklines: the GPU, memory, disk, the GPU lock,
the renderer, the queue and (on a DGX Spark) the workload governor, read in a few milliseconds.

    GET  /api/telemetry           one snapshot
    GET  /api/telemetry/history   the last ten minutes, a sample every 2 s
    GET  /api/events              the same as Server-Sent Events: "telemetry" every 2 s, "jobs" when the queue changes
    GET  /api/storage             what the studio folder and the model folders hold (worked out in the background)
    POST /api/storage/refresh     work it out again now

Everything here reads; nothing is reserved, stopped or deleted. On a GB10 nvidia-smi reports memory.used as [N/A]
(the GPU shares the machine's memory), so memory is read from /proc/meminfo; on a Mac from sysconf and vm_stat, and
there is no GPU line. Every tool runs with a time limit and its answer is kept for a second.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import suite  # the same-computer check of the mutating routes, beside this file

SPARK = sys.platform.startswith("linux")
TICK = 2.0                      # seconds between the open stream's events (a page is watching)
SAMPLE = 10.0                   # seconds between the history's samples, taken whether or not a page is open
KEEP = int(600 / SAMPLE)        # ten minutes of samples
FRESH = 1.0                     # a tool's answer is kept this long
GPU_FRESH, GPU_BUSY = 5.0, 30.0  # nvidia-smi's answer is kept this long; while a job or the render engine runs, longer
STORAGE_FRESH = 600             # the folder sizes are worked out again after ten minutes
DISK_FULL, GPU_HOT = 90, 85     # warnings: a disk this full (%), a GPU this hot (°C, the governor's alert line)
GPU_FIELDS = "utilization.gpu,temperature.gpu,power.draw,clocks.sm,name"
# the machine's workload governor (spark/workload.py), read only where SPARK_GOVERNOR (or SPARK_GOVERNOR_DIR) names its folder
GOVERNOR = Path(os.environ.get("SPARK_GOVERNOR_DIR") or os.environ.get("SPARK_GOVERNOR") or "/nonexistent-governor")
STUDIO_FOLDERS = (("renders", "Renders"), ("scouts", "Scouts"), ("voice", "Voice takes"), ("music", "Songs"),
                  ("motion", "Motion packs"), ("uploads", "Uploads"), ("exports", "Exports"), ("trash", "Trash"),
                  ("projects", "Production projects"), ("slate", "Slate films"), ("loras", "LoRAs"), ("agent", "Agent"))


def number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None  # "[N/A]", "[Not Supported]"


def parse_gpu(line: str) -> dict | None:
    """One line of `nvidia-smi --query-gpu=<GPU_FIELDS> --format=csv,noheader,nounits`."""

    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 5:
        return None
    util, temp, power, clock = (number(part) for part in parts[:4])
    return {"name": ",".join(parts[4:]).strip(), "util_pct": util, "temp_c": temp, "power_w": power,
            "clock_mhz": int(clock) if clock is not None else None}


def parse_vm_stat(text: str, total_mb: int) -> int | None:
    """A Mac's available memory from vm_stat, in MB: free, file-backed and purgeable pages, what the kernel gets back
    without compressing anything. Inactive pages are not counted: most are a process's own memory, which macOS must
    compress before it can reuse them (at the Studio's panic of 2026-10-20, 57 GB of them stood "inactive" while 138 GB
    was compressed and 15 MB was free). A vm_stat without the file-backed line (an older macOS) counts free, inactive,
    speculative and purgeable as before."""

    size, pages = 16384, {}
    for line in text.splitlines():
        if "page size of" in line:
            try:
                size = int(line.split("page size of")[1].split()[0])
            except (IndexError, ValueError):
                pass
        name, _, value = line.partition(":")
        if value.strip().rstrip(".").isdigit():
            pages[name.strip()] = int(value.strip().rstrip("."))
    names = (("Pages free", "File-backed pages", "Pages purgeable") if "File-backed pages" in pages
             else ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
    free = sum(pages.get(name, 0) for name in names)
    return min(total_mb, free * size // 2 ** 20) if pages else None


def mac_pressure_critical() -> bool:
    """Whether macOS says its memory pressure is critical (kern.memorystatus_vm_pressure_level 4): it is compressing
    and swapping to keep going, and the Studio's renders must stop before the window server stalls."""

    try:
        done = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True, timeout=2)
        return int(done.stdout.strip() or 0) >= 4
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def walk(path: Path, seen: set) -> tuple[int, int]:
    """(bytes on disk, files) under a folder, without following links; a hard-linked file counts once."""

    size = files = 0
    stack = [str(path)]
    while stack:
        try:
            entries = os.scandir(stack.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        info = entry.stat(follow_symlinks=False)
                        if info.st_nlink > 1:
                            if (info.st_dev, info.st_ino) in seen:
                                continue
                            seen.add((info.st_dev, info.st_ino))
                        size += getattr(info, "st_blocks", 0) * 512 or info.st_size
                        files += 1
                except OSError:
                    continue
    return size, files


def model_folders() -> list[tuple[str, str, Path]]:
    """The model folders this install reads, where the scripts look for them (README, oneshot-setup*.sh)."""

    home = Path.home()
    prefix = Path(os.environ.get("PREFIX", home / ".local/opt/tensorfold-studio"))
    qwen = Path(os.environ.get("QWEN_MODEL_DIR", home / "qwen-models/Qwen-Image-2.1-Turbo"))
    found = [("qwen", "Qwen-Image-2.1 Turbo", qwen),
             ("qwen-full", "Qwen-Image-2.1", Path(os.environ.get("QWEN_BASE_DIR", qwen.parent / "Qwen-Image-2.1"))),
             ("h3", "MiniMax H3", Path(os.environ.get("H3_MODEL_DIR", home / "h3-models/MiniMax-H3"))),
             ("fasth3", "FastH3", Path(os.environ.get("FASTH3_DIR", home / "h3-models/FastH3-8-Step-V2"))),
             ("flux3", "FLUX3-K", Path(os.environ.get("FLUX3_MODEL_DIR", home / "flux3-models"))),
             ("spark-models", "Spark models (ComfyUI)", Path(os.environ.get("SPARK_MODELS", home / "spark-studio-models"))),
             ("music3", "MiniMax-Music3", Path(os.environ.get("MUSIC3_MODEL", home / "music3-models/MiniMax-Music3-Comfy"))),
             ("drama3", "Fish Audio Drama 3", Path(os.environ.get("DRAMA3_CUDA_MODEL" if SPARK else "DRAMA3_MODEL",
                                                                  home / ("models/drama-3" if SPARK else "models/drama-3-mlx")))),
             ("motion-models", "Motion Previs models", Path(os.environ.get("MOTION_MODELS", prefix / "motion-models"))),
             ("engines", "Engines and their environments", prefix)]
    out = []
    for index, (key, label, path) in enumerate(found):
        try:
            path = path.expanduser().resolve()
        except OSError:
            continue
        if path.is_dir():
            out.append((index, key, label, path))
    # a folder inside another one listed (Motion Previs models under the engines' prefix) is counted with it
    kept: list = []
    for one in sorted(out, key=lambda item: len(str(item[3]))):
        if not any(one[3] == other[3] or other[3] in one[3].parents for other in kept):
            kept.append(one)
    out = [(key, label, path) for _, key, label, path in sorted(kept)]
    return out


STOPPING = threading.Event()  # set when the app is going down (an update's restart): every event stream ends at once


def disk_of(path: Path) -> dict:
    """The disk holding ``path`` as `df -h` shows it: Use% is used / (used + available to the user), the blocks kept
    for root counted as neither, and "free" is what this user can still write (statvfs f_bavail)."""

    st = os.statvfs(Path(path).resolve())
    total, used, avail = st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bfree) * st.f_frsize, st.f_bavail * st.f_frsize
    return {"total_gb": round(total / 1e9, 1), "free_gb": round(avail / 1e9, 1),
            "used_pct": round(100 * used / (used + avail), 1) if used + avail else None}


def attach(home: Path, jobs: dict, order: list, lock: threading.Lock, guard=None, comfy_port: int | None = None,
           engines=None) -> APIRouter:
    """``guard`` is spark/memory_guard.py (its floor and the ComfyUI server's pid); ``comfy_port`` the port of the
    studio's ComfyUI (spark/comfy.sh, SPARK_COMFY_PORT, 8190); ``engines`` what the Studio's resident engines hold
    (NVIDIA Nemotron 3.5 Bolt: state, backend, memory), asked on every reading, so cheap."""

    router = APIRouter()
    port = int(comfy_port or os.environ.get("SPARK_COMFY_PORT", "8190"))
    kept: dict = {}  # tool answers by name: (time, value)
    history: collections.deque = collections.deque(maxlen=KEEP)
    storage: dict = {"computed_at": None, "computing": False, "buckets": [], "error": None}
    storage_lock = threading.Lock()

    def cached(name: str, make, fresh: float = FRESH):
        at, value = kept.get(name, (0.0, None))
        if time.monotonic() - at < fresh:
            return value
        value = make()
        kept[name] = (time.monotonic(), value)
        return value

    def gpu() -> dict | None:
        if not shutil.which("nvidia-smi"):
            return None
        try:
            done = subprocess.run(["nvidia-smi", f"--query-gpu={GPU_FIELDS}", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=2)
            lines = done.stdout.strip().splitlines()
            return parse_gpu(lines[0]) if done.returncode == 0 and lines else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def memory() -> dict:
        info = guard.meminfo() if guard else {}
        total = available = None
        if info.get("MemTotal"):
            total, available = info["MemTotal"], info.get("MemAvailable")
        elif sys.platform == "darwin":
            try:
                total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2 ** 20
                done = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
                available = parse_vm_stat(done.stdout, total)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        floor = guard.floor_mb(total) if guard and SPARK and total else None  # the guard covers a DGX Spark only
        used = round(100 * (1 - available / total), 1) if total and available is not None else None
        return {"total_mb": total, "available_mb": available, "floor_mb": floor, "used_pct": used}

    def gpu_lock() -> dict:
        folder = Path(os.environ.get("STUDIO_GPU_LOCK", Path.home() / "studio-gpu.lock.d"))
        if not folder.exists():
            return {"held": False, "holder": None}
        holder = None
        try:
            holder = (folder / "owner").read_text().strip() or None  # "studio-voice <job> <pid>" (voice.take_lock)
        except OSError:
            try:
                holder = ", ".join(sorted(os.listdir(folder))[:5]) or None if folder.is_dir() else folder.read_text().strip()[:200] or None
            except OSError:
                pass
        return {"held": True, "holder": holder}

    def comfy() -> dict:
        if guard and SPARK:
            return {"running": bool(guard.server_pid()), "port": port}
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return {"running": True, "port": port}
        except OSError:
            return {"running": False, "port": port}

    def governor() -> dict | None:
        """The Spark workload governor's own last sample (state/monitor.json) and its reservations, read from its
        files: its status call takes the governor's lock and may retire reservations, so it is not made from here."""

        state = GOVERNOR / "state"
        try:
            monitor = json.loads((state / "monitor.json").read_text())
        except (OSError, ValueError):
            return None
        try:
            rows = json.loads((state / "reservations.json").read_text()).get("reservations") or []
        except (OSError, ValueError, AttributeError):
            rows = []
        try:
            with open("/proc/uptime") as handle:  # last_seen is the governor's monotonic clock
                age = round(float(handle.read().split()[0]) - float(monitor.get("last_seen")), 1)
        except (OSError, ValueError, TypeError, IndexError):
            age = None
        policy = monitor.get("policy") or {}
        return {"status": monitor.get("status"), "healthy": monitor.get("sample_healthy"),
                "active": sorted(name for name, one in (monitor.get("conditions") or {}).items() if isinstance(one, dict) and one.get("active")),
                "sensor_errors": monitor.get("sensor_errors") or [], "age_s": age,
                "reservations": [{"owner": row.get("owner"), "gib": row.get("gib")} for row in rows if isinstance(row, dict)],
                "admission_temperature_c": policy.get("admission_temperature_c"), "alert_temperature_c": policy.get("alert_temperature_c")}

    def queue() -> dict:
        with lock:
            listed = [jobs[i] for i in order if i in jobs]
        running = [job for job in listed if job.get("status") == "running"]
        current = running[0] if running else None
        return {"running": len(running), "queued": sum(1 for job in listed if job.get("status") == "queued"),
                "current": {"id": current["id"], "title": current.get("title"), "kind": current.get("kind"),
                            "progress": current.get("progress"), "estimate": current.get("estimate"),
                            "elapsed": round(time.time() - current["started"], 1) if current.get("started") else None} if current else None}

    def compact() -> list[dict]:
        with lock:
            listed = [jobs[i] for i in order[-50:] if i in jobs]
        return [{"id": job["id"], "status": job.get("status"), "title": job.get("title"), "kind": job.get("kind"),
                 "progress": job.get("progress"), "spark": job.get("spark")} for job in reversed(listed)]

    def held_by_engines() -> dict | None:
        try:
            return engines() if engines else None
        except Exception:  # noqa: BLE001 - a reading never fails over an engine's status
            return None

    def snapshot() -> dict:
        # nvidia-smi is asked at most every GPU_FRESH s, and every GPU_BUSY s while something renders: one GPU query is
        # cheap, but on every node every two seconds it added up beside a render
        lock_held = gpu_lock()
        busy = lock_held["held"] or any(job.get("status") == "running" for job in list(jobs.values()))
        card = cached("gpu", gpu, GPU_BUSY if busy else GPU_FRESH)
        mem = cached("memory", memory)
        try:
            drive = {"path": str(home), **disk_of(home)}
        except OSError:
            drive = {"path": str(home), "total_gb": None, "free_gb": None, "used_pct": None}
        try:
            load = round(os.getloadavg()[0], 2)
        except OSError:
            load = None
        ruled = cached("governor", governor, 5.0) if GOVERNOR.is_dir() else None
        warnings = []
        if drive["used_pct"] is not None and drive["used_pct"] > DISK_FULL:
            warnings.append(f"The disk holding the studio folder is {drive['used_pct']:.0f}% full ({drive['free_gb']:g} GB free).")
        if mem["floor_mb"] and mem["available_mb"] is not None and mem["available_mb"] < mem["floor_mb"]:
            warnings.append(f"Available memory ({mem['available_mb'] / 1000:.1f} GB) is below the memory guard's floor "
                            f"({mem['floor_mb'] / 1000:.1f} GB): a render now would be stopped.")
        if card and card["temp_c"] is not None and card["temp_c"] > GPU_HOT:
            warnings.append(f"The GPU is at {card['temp_c']:.0f} °C, over {GPU_HOT} °C.")
        if ruled and ruled["status"] not in (None, "ok"):
            warnings.append(f"The workload governor reports {ruled['status']}" + (f" ({', '.join(ruled['active'])})" if ruled["active"] else "") + ".")
        if ruled and ruled["sensor_errors"]:
            warnings.append("The workload governor cannot read a sensor: " + "; ".join(str(one) for one in ruled["sensor_errors"][:3]))
        return {"time": time.time(), "host": socket.gethostname(), "platform": "spark" if SPARK else "mac" if sys.platform == "darwin" else sys.platform,
                "gpu": card, "memory": mem, "cpu": {"load1": load, "cores": os.cpu_count()}, "disk": drive,
                "gpu_lock": lock_held, "renderer": {"comfy": cached("comfy", comfy)}, "jobs": queue(), "governor": ruled,
                "engines": held_by_engines(), "warnings": warnings}

    def sample() -> None:
        while True:
            try:
                now = snapshot()
                history.append({"t": round(now["time"], 1), "gpu": (now["gpu"] or {}).get("util_pct"), "mem": now["memory"]["used_pct"],
                                "temp": (now["gpu"] or {}).get("temp_c"), "power": (now["gpu"] or {}).get("power_w")})
            except Exception:  # noqa: BLE001 - a bad sample must not stop the sampler
                pass
            time.sleep(SAMPLE)

    def measure() -> None:
        try:
            seen: set = set()
            buckets, named = [], set()
            for key, label in STUDIO_FOLDERS:
                named.add(key)
                folder = home / key
                size, count = walk(folder, seen) if folder.is_dir() else (0, 0)
                buckets.append({"id": key, "label": label, "path": str(folder), "gb": round(size / 1e9, 2), "files": count, "group": "studio"})
            size = count = 0
            try:
                with os.scandir(home) as entries:
                    for entry in entries:
                        if entry.name in named:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            more, many = walk(Path(entry.path), seen)
                            size, count = size + more, count + many
                        elif entry.is_file(follow_symlinks=False):
                            size, count = size + entry.stat(follow_symlinks=False).st_size, count + 1
            except OSError:
                pass
            buckets.append({"id": "other", "label": "Everything else in the studio folder", "path": str(home), "gb": round(size / 1e9, 2),
                            "files": count, "group": "studio"})
            for key, label, path in model_folders():
                if path == home or home in path.parents:
                    continue
                size, count = walk(path, seen)
                buckets.append({"id": key, "label": label, "path": str(path), "gb": round(size / 1e9, 2), "files": count, "group": "models"})
            storage.update(buckets=buckets, computed_at=time.time(), error=None)
        except Exception as error:  # noqa: BLE001 - said on the page
            storage.update(error=str(error)[-300:], computed_at=time.time())
        finally:
            storage["computing"] = False

    def recompute(force: bool = False) -> None:
        with storage_lock:
            stale = storage["computed_at"] is None or time.time() - storage["computed_at"] > STORAGE_FRESH
            if storage["computing"] or not (force or stale):
                return
            storage["computing"] = True
        threading.Thread(target=measure, daemon=True).start()

    def storage_answer() -> dict:
        try:
            whole = disk_of(home)
        except OSError:
            whole = {"total_gb": None, "free_gb": None, "used_pct": None}
        return {"home": str(home), **whole, "buckets": list(storage["buckets"]), "computing": storage["computing"],
                "computed_at": storage["computed_at"], **({"error": storage["error"]} if storage["error"] else {})}

    @router.get("/api/telemetry")
    def telemetry() -> dict:
        return snapshot()

    @router.get("/api/telemetry/history")
    def telemetry_history() -> dict:
        return {"interval_s": SAMPLE, "samples": list(history)}

    @router.get("/api/events")
    async def events(request: Request):
        async def stream():
            last_jobs, beat = None, time.monotonic()
            yield ": connected\n\n"
            # ends on its own when the app shuts down: an open stream would otherwise hold a graceful restart forever
            while not STOPPING.is_set() and not await request.is_disconnected():
                now = await asyncio.to_thread(snapshot)
                yield "event: telemetry\ndata: " + json.dumps(now, default=str) + "\n\n"
                listed = compact()
                if listed != last_jobs:
                    last_jobs = listed
                    yield "event: jobs\ndata: " + json.dumps(listed, default=str) + "\n\n"
                if time.monotonic() - beat > 15:
                    beat = time.monotonic()
                    yield ": heartbeat\n\n"
                try:
                    await asyncio.sleep(TICK)
                except asyncio.CancelledError:
                    break

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/api/storage")
    def storage_sizes() -> dict:
        recompute()
        return storage_answer()

    @router.post("/api/storage/refresh")
    def storage_refresh(request: Request) -> dict:
        suite.local(request)
        recompute(force=True)
        return storage_answer()

    threading.Thread(target=sample, daemon=True, name="telemetry").start()
    return router
