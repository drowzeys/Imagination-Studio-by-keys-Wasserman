"""The Studio's memory manager: what the Studio itself holds (its idle modules), what other programs on this machine
hold, and what is given back before and during a render, so that a render never starts into memory it does not have.

The pieces it joins were already here, each guarding one engine: the clip, song, sound and swap estimates and the
floor (spark/memory_guard.py: check, music_check, sound_check, swap_check, floor_mb), the floor watch beside ComfyUI
(memory_guard.watch, spark/comfy.sh), Drama 3 and MiniMax Music 3 unloaded before any other job (voice.release,
music.release), NVIDIA Nemotron 3.5 Bolt unloaded when a job's peak does not fit beside it (bolt.admit), ComfyUI
stopped before a LoRA run (train_free) and the shared-RAM reservations of local GPU workers (local_gpus). What none of
them did, and this file does:

1. ``admit``: after those releases, a job of this machine's queue whose measured peak still does not fit gives back
   the Studio's remaining idle modules in a fixed order (MODULE ORDER below), then waits (STUDIO_MEMORY_WAIT_S, 120 s)
   for memory held by other programs to come back, and is refused with their names and sizes when it does not,
   instead of starting and taking the machine down. STUDIO_MEMORY_GUARD=warn (an N1X's default) says so and runs it.
2. ``Watch``: while the app runs, every STUDIO_MEMORY_POLL_S (2 s; 0.5 s while a job runs here) it reads the
   available memory and the kernel's memory stall time; under 1.5 times the floor, or with tasks waiting on memory
   over 10 % of the time (PSI "some"), it releases the next idle module (never one the running job uses), so the floor watch beside ComfyUI, which
   stops the render itself, does not have to act. What it did is logged (``<studio>/logs/memory.log``) and listed by
   GET /api/memory.
3. ``foreign``: the largest programs on this machine that are not the Studio's (an agent's model server, a ComfyUI
   started by hand): named in every refusal, in GET /api/memory, and never stopped by the app. The machine guard
   (scripts/memguard.py) may stop them, only when the person opted in.

MODULE ORDER: Fish Audio Drama 3 (voice), the song engines (music), the Studio's ComfyUI with its cached models,
NVIDIA Nemotron 3.5 Bolt (the brain, last: an agent may be talking to it). A module is idle when it is loaded, the
running job does not use it and (Bolt) no answer is in flight. The render itself is never stopped here.
"""
from __future__ import annotations

import collections
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

POLL_S = float(os.environ.get("STUDIO_MEMORY_POLL_S") or 2)
WAIT_S = float(os.environ.get("STUDIO_MEMORY_WAIT_S") or 120)
WARN_FACTOR = 1.5   # the watch releases an idle module under this many floors (the render's own guard acts at one)
COOLDOWN_S = 10.0   # between two releases of the watch: a release takes a few seconds to show in the reading
FOREIGN_MIN_MB = 1024  # a program smaller than this is not named
RENDER_POLL_S = 0.5    # the watch's poll while a job runs here
STALL_SOME_PCT = 10.0  # /proc/pressure/memory "some avg10": tasks waiting on memory this share of the time is the early sign
GPU_FREE = {"export", "soundtrack", "finish", "graphics", "hlg", "beats"}  # ffmpeg and the like: no model loaded

_modules: list = []


def enabled() -> bool:
    """STUDIO_MEMORY_MANAGER=off turns off the admission and the watch (GET /api/memory and an asked-for release still
    work): the test suite runs so, since its stand-in engines must not wait on the CI machine's real memory."""

    return os.environ.get("STUDIO_MEMORY_MANAGER", "on").strip().lower() not in ("off", "0", "no", "false")
events: collections.deque = collections.deque(maxlen=50)
_log: dict = {"path": None}
_mutex = threading.Lock()


class Module:
    """One thing the Studio loads and can give back: ``loaded()``, ``held_mb()`` (an estimate, 0 when unknown),
    ``busy()`` (answering now) and ``release(why)`` -> True when it was loaded."""

    def __init__(self, name: str, label: str, loaded, release, held_mb=lambda: 0, busy=lambda: False) -> None:
        self.name, self.label = name, label
        self._loaded, self._release, self._held, self._busy = loaded, release, held_mb, busy

    def loaded(self) -> bool:
        try:
            return bool(self._loaded())
        except Exception:  # noqa: BLE001 - a module that cannot say is not counted as loaded
            return False

    def held_mb(self) -> int:
        try:
            return max(0, int(self._held() or 0))
        except Exception:  # noqa: BLE001
            return 0

    def busy(self) -> bool:
        try:
            return bool(self._busy())
        except Exception:  # noqa: BLE001
            return True  # unknown: left alone

    def release(self, why: str) -> bool:
        return bool(self._release(why))


def register(module: Module) -> None:
    """server.py registers the modules once, in MODULE ORDER."""

    with _mutex:
        _modules[:] = [one for one in _modules if one.name != module.name] + [module]


def modules() -> list:
    return list(_modules)


def uses(kind: str | None) -> set:
    """The modules a job of this kind runs on (never released for it, nor while it runs)."""

    if not kind or kind in GPU_FREE:
        return set()
    if kind in ("voice", "music"):
        return {kind}
    return {"comfy"}  # clips, pictures, sound, swaps, LoRA runs: the render server (Mac: its ComfyUI actions)


# ---------- the machine ----------

def _guard():
    spark = str(Path(__file__).resolve().parent.parent / "spark")
    if spark not in sys.path:
        sys.path.insert(0, spark)
    import memory_guard  # noqa: PLC0415

    return memory_guard


def stall_some() -> float:
    """The kernel's share of the last 10 s in which some task waited on memory (Linux PSI), 0 where there is none."""

    try:
        with open("/proc/pressure/memory") as handle:
            for line in handle:
                if line.startswith("some"):
                    return float(line.split("avg10=")[1].split()[0])
    except (OSError, IndexError, ValueError):
        pass
    return 0.0


def reading() -> dict:
    """This machine's memory now, MB: {"available_mb", "total_mb", "floor_mb", "platform"} (available None when it
    cannot be read). Linux and native Windows: MemAvailable (GlobalMemoryStatusEx); a Mac: what vm_stat says the
    kernel gets back without compressing, 0 under critical pressure (telemetry.parse_vm_stat). The floor is the memory
    guard's (SPARK_MEMORY_FLOOR_MB, else a tenth of the machine, never under 8 GB) on every platform."""

    guard = _guard()
    total = available = None
    if sys.platform == "darwin":
        import telemetry  # noqa: PLC0415

        try:
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2 ** 20
            said = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout
            available = telemetry.parse_vm_stat(said, total)
            if available is not None and telemetry.mac_pressure_critical():
                available = 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        where = "mac"
    elif sys.platform == "win32":
        info = guard.windows_meminfo()
        total, available, where = info.get("MemTotal"), info.get("MemAvailable"), "windows"
    else:
        info = guard.meminfo()
        total, available, where = info.get("MemTotal"), info.get("MemAvailable"), "linux"
    return {"available_mb": available, "total_mb": total, "floor_mb": guard.floor_mb(total) if total else None, "platform": where}


def _proc_name(pid: int) -> tuple[str, str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            argv = [part.decode(errors="replace") for part in handle.read().split(b"\0") if part]
    except OSError:
        argv = []
    try:
        with open(f"/proc/{pid}/comm") as handle:
            comm = handle.read().strip()
    except OSError:
        comm = ""
    return comm or (Path(argv[0]).name if argv else str(pid)), " ".join(argv)


def _processes() -> list[dict]:
    """Every process this user can see: {"pid", "ppid", "mb" (resident, with its GPU memory where nvidia-smi reports
    it), "name", "command"}."""

    out: list[dict] = []
    if sys.platform.startswith("linux"):
        try:
            entries = [int(name) for name in os.listdir("/proc") if name.isdigit()]
        except OSError:
            entries = []
        try:
            gpu = _guard().processes_mb()
        except Exception:  # noqa: BLE001 - no nvidia-smi
            gpu = {}
        for pid in entries:
            try:
                with open(f"/proc/{pid}/status") as handle:
                    fields = dict(line.split(":", 1) for line in handle if ":" in line)
                rss = int(fields.get("VmRSS", "0 kB").split()[0]) // 1024
                ppid = int(fields.get("PPid", "0").strip())
            except (OSError, ValueError):
                continue
            name, command = _proc_name(pid)
            out.append({"pid": pid, "ppid": ppid, "mb": max(rss, gpu.get(pid, 0)), "name": name, "command": command})
    elif sys.platform == "darwin":
        try:
            said = subprocess.run(["ps", "-axo", "pid=,ppid=,rss=,command="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            said = ""
        for line in said.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
                out.append({"pid": int(parts[0]), "ppid": int(parts[1]), "mb": int(parts[2]) // 1024,
                            "name": Path(parts[3].split()[0]).name, "command": parts[3]})
    elif sys.platform == "win32":
        try:
            said = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            said = ""
        try:
            import windows_memory  # noqa: PLC0415

            gpu = {pid: int(mb) for pid, mb in windows_memory.windows_gpu_process_memory().items()}
        except Exception:  # noqa: BLE001
            gpu = {}
        for line in said.splitlines():
            cells = [cell.strip('"') for cell in line.split('","')]
            if len(cells) >= 5 and cells[1].isdigit():
                kb = "".join(ch for ch in cells[4] if ch.isdigit())
                pid = int(cells[1])
                out.append({"pid": pid, "ppid": 0, "mb": max(int(kb or 0) // 1024, gpu.get(pid, 0)), "name": cells[0], "command": cells[0]})
    return out


def _tree(rows: list[dict], root: int) -> set[int]:
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(row["ppid"], []).append(row["pid"])
    found, todo = set(), [root]
    while todo:
        one = todo.pop()
        if one not in found:
            found.add(one)
            todo.extend(children.get(one, []))
    return found


def own_markers() -> list[str]:
    """Paths in a command line that make a process the Studio's own (its install and its checkout)."""

    out = [str(Path(__file__).resolve().parent.parent), os.environ.get("PREFIX") or os.path.expanduser("~/.local/opt/tensorfold-studio")]
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        out.append(os.path.join(os.environ["LOCALAPPDATA"], "ImaginationStudio"))
    return out


def foreign(top: int = 5, rows: list[dict] | None = None, own: set | None = None) -> list[dict]:
    """The largest programs on this machine that are not the Studio's: not this app or anything it started, not the
    ComfyUI server of spark/comfy.sh or mac/comfy.sh, nothing run from the Studio's checkout or install. Each
    {"pid", "name", "mb", "command"} (command cut to 160 characters), largest first, FOREIGN_MIN_MB and up."""

    rows = _processes() if rows is None else rows
    mine = set(own or ()) | _tree(rows, os.getpid())
    try:
        server = _guard().server_pid() if sys.platform.startswith("linux") else None
    except Exception:  # noqa: BLE001
        server = None
    if server:
        mine |= _tree(rows, server)
    markers = [m for m in own_markers() if m and len(m) > 3]
    found = [row for row in rows if row["pid"] not in mine and row["mb"] >= FOREIGN_MIN_MB
             and not any(marker in row["command"] for marker in markers)]
    for row in found:
        row.setdefault("gpu", uses_gpu(row["pid"]))
    # a GPU program first (on a GB10 its CUDA memory may not show in its resident size), then by size
    found.sort(key=lambda row: (not row.get("gpu"), -row["mb"]))
    return [{"pid": row["pid"], "name": row["name"], "mb": row["mb"], "gpu": bool(row.get("gpu")), "command": row["command"][:160]}
            for row in found[:top]]


def uses_gpu(pid: int) -> bool:
    """Whether a Linux process has an NVIDIA device open (/dev/nvidia*): a model server, a ComfyUI, a training run.
    False where it cannot be read (another user's process, a Mac, Windows)."""

    if not sys.platform.startswith("linux"):
        return False
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                if os.readlink(f"/proc/{pid}/fd/{fd}").startswith("/dev/nvidia"):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def said_foreign(rows: list[dict]) -> str:
    return ", ".join(f"{row['name']} (pid {row['pid']}, {row['mb'] / 1024:.1f} GB{', on the GPU' if row.get('gpu') else ''})" for row in rows)


# ---------- what was done ----------

def attach_log(home: Path) -> None:
    _log["path"] = Path(home) / "logs" / "memory.log"


def note(what: str, **more) -> dict:
    """One thing the manager did, kept for GET /api/memory and appended to <studio>/logs/memory.log."""

    entry = {"at": round(time.time(), 1), "what": what, **more}
    events.append(entry)
    path = _log["path"]
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 1 << 20:  # one old copy is kept
                path.replace(path.with_suffix(".log.1"))
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + what + "".join(f" {k}={v}" for k, v in more.items()) + "\n")
        except OSError:
            pass
    return entry


# ---------- giving back ----------

def idle(module: Module, keep: set) -> bool:
    return module.name not in keep and module.loaded() and not module.busy()


def release_idle(why: str, keep: set = frozenset(), names: list | None = None, enough=None) -> list[dict]:
    """Give back the Studio's idle modules in MODULE ORDER (only ``names`` when given), stopping as soon as
    ``enough()`` says the memory is there. Modules in ``keep`` (the running job's) are never touched. Returns what was
    released: [{"module", "label", "before_mb", "after_mb"}]."""

    done = []
    for module in modules():
        if enough is not None and enough():
            break
        if names is not None and module.name not in names:
            continue
        if not idle(module, set(keep)):
            continue
        before = reading()["available_mb"]
        try:
            released = module.release(why)
        except Exception as error:  # noqa: BLE001 - one module that will not go does not stop the others
            note("release failed", module=module.name, why=why, error=str(error)[:200])
            continue
        if released:
            time.sleep(0.5)  # the memory comes back a little after the process is gone
            after = reading()["available_mb"]
            done.append({"module": module.name, "label": module.label, "before_mb": before, "after_mb": after})
            note("released", module=module.name, why=why, before_mb=before, after_mb=after)
    return done


def running_kinds(jobs) -> set:
    """The modules the jobs running on this machine now use."""

    out: set = set()
    for job in list(jobs):
        if job.get("status") == "running" and not job.get("remote") and not job.get("spark"):
            out |= uses(job.get("kind"))
    return out


def admit(job: dict, fit, wait_s: float | None = None, mode: str | None = None, stopped=lambda: False) -> str:
    """Before a job of this machine's queue loads anything (server.perform_job, after Drama 3, Music 3 and Bolt had
    their turn): ``fit()`` answers from the measured peaks {"fits", "need_mb", "available_mb", "floor_mb"}, or None
    when this kind of job has none (it then goes, as before). Not fitting: the idle modules go in MODULE ORDER; still
    not fitting: it waits up to ``wait_s`` for other programs to give memory back, its progress naming them; then it
    is refused (RuntimeError naming the largest of them), or, under STUDIO_MEMORY_GUARD=warn, run with a warning.
    Returns what the job's log says."""

    if not enabled():
        return ""
    said = fit()
    if not said or said.get("fits") is not False:
        return ""
    keep = uses(job.get("kind"))
    lines = []

    def enough() -> bool:
        now = fit()
        return not now or now.get("fits") is not False

    for one in release_idle(f"a {job.get('kind', 'job')} job needs the memory", keep, enough=enough):
        lines.append(f"[studio] memory: {one['label']} was unloaded to make room for this job "
                     f"({(one['before_mb'] or 0) / 1024:.0f} -> {(one['after_mb'] or 0) / 1024:.0f} GB available)\n")
    said = fit()
    if not said or said.get("fits") is not False:
        return "".join(lines)
    wait_s = WAIT_S if wait_s is None else wait_s
    ends = time.monotonic() + wait_s
    others = foreign()
    if others and wait_s > 0:
        note("waiting", job=job.get("id"), kind=job.get("kind"), others=said_foreign(others[:3]))
    while time.monotonic() < ends and not stopped() and job.get("status") not in ("cancelled", "failed", "done"):
        job["progress"] = {"stage": "Waiting for memory" + (": " + said_foreign(others[:2]) + " hold it" if others else ""), "at": 0, "of": 1}
        time.sleep(min(POLL_S, max(0.05, ends - time.monotonic())))
        said = fit()
        if not said or said.get("fits") is not False:
            job.pop("progress", None)
            lines.append("[studio] memory: the memory came back, the job goes on\n")
            return "".join(lines)
        others = foreign()
    if (job.get("progress") or {}).get("stage", "").startswith("Waiting for memory"):
        job.pop("progress", None)
    need, have, floor = said.get("need_mb") or 0, said.get("available_mb") or 0, said.get("floor_mb") or 0
    why = (f"This job needs about {need / 1024:.0f} GB and this machine has {have / 1024:.0f} GB for it with "
           f"{floor / 1024:.0f} GB kept free" + (f", after the Studio's idle modules were unloaded" if lines else "") + ". ")
    why += (f"The largest other programs on it: {said_foreign(others[:3])}. Stop one of them (or wait for it to finish) and "
            f"queue the job again; the Studio does not stop programs it did not start." if others else
            "Free some memory and queue the job again.")
    if (mode or _guard().guard_mode()) == "warn":
        note("warned", job=job.get("id"), kind=job.get("kind"), need_mb=need, available_mb=have)
        return "".join(lines) + f"[studio] memory: {why} It runs anyway (STUDIO_MEMORY_GUARD=warn) and fails if memory runs out.\n"
    note("refused", job=job.get("id"), kind=job.get("kind"), need_mb=need, available_mb=have, others=said_foreign(others[:3]))
    raise RuntimeError(why)


# ---------- while the app runs ----------

def pressed(available: int, floor: int) -> bool:
    """Under WARN_FACTOR floors, or under two floors while tasks wait on memory (PSI "some"; a cold start's model load
    stalls on the page cache with half the machine free, so a stall alone is not counted)."""

    return available < floor * WARN_FACTOR or available < 2 * floor and stall_some() >= STALL_SOME_PCT


class Watch:
    """The app's own watch: under WARN_FACTOR floors of available memory the next idle module goes (one per
    COOLDOWN_S), the running job's never; under one floor with nothing of the Studio's left to give, it says who holds
    the memory (the render's own floor watch, spark/memory_guard.py, then stops the render if it must)."""

    def __init__(self, jobs, poll_s: float = POLL_S) -> None:
        self.jobs, self.poll_s = jobs, poll_s
        self.stop = threading.Event()
        self.last = 0.0
        self.warned = 0.0
        self.thread: threading.Thread | None = None

    def tick(self) -> list[dict]:
        if not enabled():
            return []
        now = reading()
        available, floor = now["available_mb"], now["floor_mb"]
        if available is None or not floor or not pressed(available, floor):
            return []
        if time.monotonic() - self.last < COOLDOWN_S:
            return []
        keep = running_kinds(self.jobs())
        done = release_idle(f"memory is low ({available} MB available, the floor {floor} MB)", keep,
                            enough=lambda: not pressed(reading()["available_mb"] or 0, floor))
        self.last = time.monotonic()
        if not done and available < floor and time.monotonic() - self.warned > 60:
            self.warned = time.monotonic()
            note("under the floor", available_mb=available, floor_mb=floor, others=said_foreign(foreign(3)) or "none named")
        return done

    def interval(self) -> float:
        """RENDER_POLL_S while a job runs on this machine (a model load takes 7 to 30 GB a second on a Spark), else
        the poll it was made with."""

        return min(self.poll_s, RENDER_POLL_S) if running_kinds(self.jobs()) else self.poll_s

    def run(self) -> None:
        while not self.stop.wait(self.interval()):
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 - the watch never ends over one bad reading
                note("watch error", error=str(error)[:200])

    def start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, name="studio-memory-watch", daemon=True)
            self.thread.start()


def state(jobs=()) -> dict:
    """GET /api/memory: the machine's memory, the Studio's modules, the largest other programs and what was done."""

    now = reading()
    keep = running_kinds(jobs)
    listed = [{"name": one.name, "label": one.label, "loaded": one.loaded(), "held_mb": one.held_mb() if one.loaded() else 0,
               "idle": idle(one, keep)} for one in modules()]
    available, floor = now["available_mb"], now["floor_mb"]
    headroom = available - floor if available is not None and floor else None
    return {**now, "headroom_mb": headroom, "releasable_mb": sum(m["held_mb"] for m in listed if m["idle"]),
            "modules": listed, "others": foreign(5), "events": list(events)[-20:],
            "guard": _guard().guard_mode(), "wait_s": WAIT_S}
