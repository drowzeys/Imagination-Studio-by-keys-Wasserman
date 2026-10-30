"""Local, independent NVIDIA workers; device identity is a UUID, never an ordinal.

Runtime leases and RAM reservations are private, per-user and shared by every Studio
on the host. No SSH, model copies, downloads or tensor parallelism are involved.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import fcntl
except ImportError:  # Platforms without POSIX file locks cannot manage local workers.
    fcntl = None

LIMIT = 4
CACHE_SECONDS = 3
RAM_WAIT_SECONDS = 120
UUID = re.compile(r"GPU-[A-Za-z0-9-]{8,100}\Z")
_CACHE = {"at": float("-inf"), "value": {"supported": False, "devices": []}}
_CACHE_LOCK = threading.Lock()


def process_identity(pid: int) -> str | None:
    """Linux start ticks, including boot identity; PID reuse never grants ownership."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = raw[raw.rindex(")") + 2:].split()
        if fields[0] == "Z":
            return None
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + fields[19]
    except (OSError, ValueError, IndexError):
        return None


def alive(record: dict, prefix: str = "") -> bool:
    pid, start = record.get(prefix + "pid"), record.get(prefix + "start")
    return bool(pid and start and process_identity(pid) == start)


def private_dir(path: Path) -> Path:
    """Refuse symlinks and other users' runtime directories, rather than following them."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("GPU runtime directory must be a real directory owned by this user")
    path.chmod(0o700)
    return path


def runtime_dir() -> Path:
    # A desktop, cron and systemd session must see the same per-user device leases.
    default = Path.home() / ".cache/studio-gpus"
    return private_dir(Path(os.environ.get("STUDIO_GPU_RUNTIME_DIR", default)))


def ram_dir() -> Path:
    asked = os.environ.get("STUDIO_GPU_RAM_DIR")
    return private_dir(Path(asked) if asked else runtime_dir() / "ram")


@contextlib.contextmanager
def locked(path: Path):
    if fcntl is None:
        raise OSError("Local GPU workers require Linux file locks")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
            raise OSError("GPU runtime file is not owned by this user")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def load(path: Path) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError("GPU runtime state is not private")
            found = json.load(stream)
            return found if isinstance(found, dict) else {}
    except FileNotFoundError:
        return {}
    except (ValueError, TypeError):
        raise OSError("GPU runtime state is invalid") from None


def save(path: Path, value: dict) -> None:
    part = path.with_name(path.name + "." + secrets.token_hex(8))
    fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def number(value: str | None) -> float | None:
    try:
        return float((value or "").split()[0])
    except (ValueError, IndexError):
        return None


def parse_inventory(text: str) -> list[dict]:
    devices = []
    for index, gpu in enumerate(ET.fromstring(text).findall("gpu")):
        ident = gpu.findtext("uuid", "")
        if not UUID.fullmatch(ident):
            continue
        total = number(gpu.findtext("fb_memory_usage/total"))
        # Unified-memory GB10s remain Sparks, not local discrete-card workers.
        power = next((value for path in ("gpu_power_readings/instant_power_draw", "gpu_power_readings/power_draw",
                                         "power_readings/power_draw", "gpu_power_readings/average_power_draw")
                      if (value := number(gpu.findtext(path))) is not None), None)
        compute = [p.findtext("pid") or "" for p in gpu.findall("processes/process_info")
                   if "C" in (p.findtext("type") or "")]
        devices.append({"id": "local:" + ident, "gpu_uuid": ident, "index": index,
                        "name": gpu.findtext("product_name", "NVIDIA GPU"),
                        "total_mb": total, "used_mb": number(gpu.findtext("fb_memory_usage/used")),
                        "free_mb": number(gpu.findtext("fb_memory_usage/free")),
                        "util_pct": number(gpu.findtext("utilization/gpu_util")),
                        "temp_c": number(gpu.findtext("temperature/gpu_temp")),
                        "power_w": power,
                        "display_active": gpu.findtext("display_active", "Disabled").lower() == "enabled",
                        "compute_pids": [int(pid) for pid in compute if pid.isdigit() and int(pid) > 0],
                        "hidden_compute": any(not pid.isdigit() or int(pid) <= 0 for pid in compute)})
    return devices


def inventory(*, fresh: bool = False) -> dict:
    """One cached full query (nvidia-smi ignores CUDA_VISIBLE_DEVICES), with a deadline."""
    with _CACHE_LOCK:
        if not fresh and time.monotonic() - _CACHE["at"] < CACHE_SECONDS:
            return _CACHE["value"]
        found = {"supported": False, "devices": []}
        if sys.platform.startswith("linux"):
            try:
                done = subprocess.run(["nvidia-smi", "-q", "-x"], capture_output=True, text=True, timeout=5, check=False)
                if done.returncode == 0:
                    devices = parse_inventory(done.stdout)
                    found = {"supported": any(d["total_mb"] for d in devices), "devices": devices}
            except (OSError, subprocess.TimeoutExpired, ET.ParseError):
                pass
        _CACHE.update(at=time.monotonic(), value=found)
        return found


def memory() -> tuple[int, int]:
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                values[key] = int(value.split()[0]) // 1024
    except (OSError, ValueError):
        pass
    return values.get("MemTotal", 0), values.get("MemAvailable", 0)


def _model_residents(key: str | None = None) -> list[dict]:
    """Owned model servers with a native route marker or a validated Comfy route."""
    residents = []
    processes = private_processes(key) if key else marked_processes("STUDIO_GPU_MODEL_RAM", "1")
    for pid, start in processes.items():
        try:
            proc = Path("/proc") / str(pid)
            environment = dict(item.split(b"=", 1) for item in (proc / "environ").read_bytes().split(b"\0") if b"=" in item)
            if environment.get(b"STUDIO_GPU_MODEL_RAM") != b"1" or not environment.get(b"STUDIO_LOCAL_GPU_KEY"):
                continue
            if key and environment.get(b"STUDIO_LOCAL_GPU_KEY") != key.encode():
                continue
            native = environment.get(b"STUDIO_GPU_MODEL_ROUTE", b"").decode("utf-8")
            if native.startswith("native:"):
                route = native
            else:
                port = environment.get(b"SPARK_COMFY_PORT", b"8190").strip() or b"8190"
                wanted = b"\0".join((b"main.py", b"--listen", b"127.0.0.1", b"--port", port, b""))
                if wanted not in (proc / "cmdline").read_bytes() + b"\0":
                    continue
                prefix = Path(os.fsdecode(environment.get(b"PREFIX") or os.fsencode(Path.home() / ".local/opt/tensorfold-studio")))
                if (proc / "cwd").resolve() != (prefix / "ComfyUI").resolve():
                    continue
                stem = "comfy" if port == b"8190" else "comfy-" + port.decode("ascii")
                route = (prefix / (stem + ".kind")).read_text().strip()
            anon = next((int(line.split()[1]) for line in (proc / "smaps_rollup").read_text().splitlines()
                         if line.startswith("Pss_Anon:")), 0)
            if process_identity(pid) == start:
                residents.append({"pid": pid, "start": start, "route": route, "mb": max(0, anon) // 1024,
                                  "gpu": os.fsdecode(environment.get(b"STUDIO_GPU", b"?"))})
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    return residents


def model_ram_mb(route: str | None = None, *, key: str | None = None) -> int:
    """Only this session's matching resident route offsets its remaining growth."""
    key = key or os.environ.get("STUDIO_LOCAL_GPU_KEY")
    if not key or not route:
        return 0
    return sum(resident["mb"] for resident in _model_residents(key) if resident["route"] == route)


def _ram_growth(reservations: dict) -> int:
    """Credit each resident session/route once, including concurrent callers."""
    credit, outstanding = {}, 0
    for reservation in reservations.values():
        need = reservation.get("need_mb")
        if need is None:
            continue
        key, route = reservation.get("key"), reservation.get("route")
        identity = (key, route)
        if identity not in credit:
            credit[identity] = model_ram_mb(route, key=key) if key and route else 0
        used = min(need, credit[identity])
        credit[identity] -= used
        outstanding += need - used
    return outstanding


def _ram_refusal(need: int | None, available: int, floor: int, *, timed_out: bool = False) -> str:
    said = ("Timed out waiting for shared host RAM" if timed_out else "Not enough shared host RAM")
    said += f": {available} MiB is available and {floor} MiB must stay free"
    if need is not None:
        said += f"; this job still needs up to {need} MiB"
    holders = _model_residents()
    if holders:
        said += ". Resident model servers: " + ", ".join(
            f"GPU {r['gpu']} (PID {r['pid']}, {r['mb']} MiB anonymous RAM)" for r in holders)
        said += ". Stop an idle worker to release its models, then retry"
    else:
        said += ". Free memory or stop an idle model server, then retry"
    return said + "."


@contextlib.contextmanager
def ram_reservation(need_mb: int | None, job: dict, *, route: str | None = None):
    """Book host peaks, not memory twice; unmeasured jobs form a FIFO-exclusive barrier.

    MemAvailable already excludes each server's allocated pages. A reservation books
    only the outstanding growth to its full peak, recomputed from matching owned
    anonymous pages. File cache, another route and unrelated processes get no credit.
    No live holder that could release memory means fail now, not an endless wait.
    """
    spark = str(Path(__file__).resolve().parent.parent / "spark")
    if spark not in sys.path:
        sys.path.insert(0, spark)
    from memory_guard import floor_mb

    need = max(0, int(need_mb)) if need_mb is not None else None
    directory = ram_dir()
    path, waiting = directory / "reservations.json", directory / "waiters.json"
    token = uuid.uuid4().hex
    owner = {"pid": os.getpid(), "start": process_identity(os.getpid()), "need_mb": need,
             "route": route, "key": os.environ.get("STUDIO_LOCAL_GPU_KEY")}
    if not owner["start"]:
        raise RuntimeError("Shared host RAM admission requires a live Linux process identity.")
    admitted, registered = False, False
    began = time.monotonic()
    terminal = ("cancelled", "failed", "done")
    try:
        while job.get("status") not in terminal:
            failure = None
            with locked(directory / "reservations.lock"):
                reservations = {key: value for key, value in load(path).items() if alive(value)}
                waiters = {key: value for key, value in load(waiting).items() if alive(value)}
                if job.get("status") not in terminal:
                    waiters.setdefault(token, owner)
                    registered = True
                    earlier = list(waiters)[:list(waiters).index(token)]
                    barrier = any(waiters[key].get("need_mb") is None for key in earlier)
                    turn = not barrier and (need is not None or not earlier)
                    total, before = memory()
                    outstanding = _ram_growth({**reservations, token: owner})
                    resident = model_ram_mb(route) if need is not None else 0
                    growth = max(0, need - resident) if need is not None else None
                    # Allocations while /proc is read must not spend the older, larger
                    # MemAvailable together with their newer residency credit.
                    _, after = memory()
                    available = min(before, after)
                    floor = max(8192, floor_mb(total))
                    exclusive = any(r.get("need_mb") is None for r in reservations.values())
                    fits = not reservations if need is None else not exclusive and available - outstanding >= floor
                    if not total or (need is not None and need > total - floor):
                        failure = _ram_refusal(growth, available, floor)
                    elif turn and available >= floor and fits:
                        reservations[token] = owner
                        waiters.pop(token)
                        admitted = True
                    elif time.monotonic() - began >= RAM_WAIT_SECONDS:
                        failure = _ram_refusal(growth, available, floor, timed_out=True)
                    elif turn and not reservations:
                        failure = _ram_refusal(growth, available, floor)
                save(path, reservations)
                save(waiting, waiters)
            if failure:
                raise RuntimeError(failure)
            if admitted:
                if (job.get("progress") or {}).get("stage") == "Waiting for shared host RAM":
                    job.pop("progress", None)
                break
            if job.get("status") not in terminal:
                job["progress"] = {"stage": "Waiting for shared host RAM", "at": 0, "of": 1}
                time.sleep(0.2)
        yield admitted
    finally:
        if admitted or registered:
            with locked(directory / "reservations.lock"):
                for state in (path, waiting):
                    entries = {key: value for key, value in load(state).items() if key != token and alive(value)}
                    save(state, entries)


def check_child_comfy() -> None:
    """Reject a listener on a child's reserved Comfy port unless its identity is ours."""
    key = os.environ.get("STUDIO_LOCAL_GPU_KEY")
    if not key:
        return
    port = int(os.environ["SPARK_COMFY_PORT"])
    inodes = set()
    for family in ("tcp", "tcp6"):
        try:
            lines = Path("/proc/net/" + family).read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 9 and fields[3] == "0A" and int(fields[1].split(":")[1], 16) == port:
                inodes.add(fields[9])
    if not inodes:
        return
    marker, owned = ("STUDIO_LOCAL_GPU_KEY=" + key).encode(), set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if marker not in (entry / "environ").read_bytes().split(b"\0"):
                continue
            for fd in (entry / "fd").iterdir():
                try:
                    link = os.readlink(fd)
                except OSError:
                    continue
                if link.startswith("socket:["):
                    owned.add(link[8:-1])
        except (OSError, ValueError):
            continue
    if not inodes.issubset(owned):
        raise RuntimeError("The local worker's Comfy port is occupied by an unrelated process; it is left untouched.")


def stop_child_comfy(key: str | None = None) -> None:
    """Stop only Comfy processes that inherited this private child's identity.

    This replaces port-only pkill for local children: an unrelated process that later
    occupies a released port must never become ours merely because its port matches.
    """
    key = key or os.environ.get("STUDIO_LOCAL_GPU_KEY")
    if not key:
        return
    marker = ("STUDIO_LOCAL_GPU_KEY=" + key).encode()
    signalled = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            pid = int(entry.name)
            start = process_identity(pid)
            environment = (entry / "environ").read_bytes().split(b"\0")
            words = (entry / "cmdline").read_bytes().split(b"\0")
            comfy = any(Path(w.decode("utf-8", "replace")).name in ("main.py", "managed_comfy.py", "comfy.sh") for w in words if w)
            if start and marker in environment and comfy and process_identity(pid) == start:
                os.kill(pid, signal.SIGTERM)
                signalled.append((pid, start))
        except (OSError, ValueError):
            continue
    until = time.monotonic() + 5
    while any(process_identity(pid) == start for pid, start in signalled) and time.monotonic() < until:
        time.sleep(0.1)
    for pid, start in signalled:
        try:
            if process_identity(pid) == start:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def process_table() -> dict[int, dict]:
    """One Linux parent/start snapshot; sessions are deliberately not ownership."""
    found = {}
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        entries = Path("/proc").iterdir()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text()
                fields = raw[raw.rindex(")") + 2:].split()
                if fields[0] != "Z":
                    pid = int(entry.name)
                    found[pid] = {"pid": pid, "ppid": int(fields[1]), "start": boot + ":" + fields[19]}
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return found


def signal_owned(pid: int, start: str, sig: int) -> bool:
    """A stale PID is never a signal target."""
    if process_identity(pid) != start:
        return True
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        return False


def drain_processes(processes: dict[int, str], *, frozen: bool = False) -> bool:
    """Bounded TERM/KILL of exact process identities, including detached sessions."""
    for pid, start in reversed(list(processes.items())):
        signal_owned(pid, start, signal.SIGTERM)
        if frozen:
            signal_owned(pid, start, signal.SIGCONT)
    until = time.monotonic() + 5
    while any(process_identity(pid) == start for pid, start in processes.items()) and time.monotonic() < until:
        time.sleep(0.05)
    for pid, start in processes.items():
        signal_owned(pid, start, signal.SIGKILL)
    until = time.monotonic() + 2
    while any(process_identity(pid) == start for pid, start in processes.items()) and time.monotonic() < until:
        time.sleep(0.05)
    return not any(process_identity(pid) == start for pid, start in processes.items())


def stop_job_tree(pid: int, start: str | None, *, key: str | None = None) -> bool:
    """Stop an exact runner family, including detached or reparented descendants.

    The private per-job marker finds orphans after the runner exited. A validated
    Linux ancestry snapshot also includes children that filtered their environment.
    Sessions and process groups are never treated as ownership.
    """
    if pid == os.getpid():
        return False
    family = marked_processes("STUDIO_GPU_JOB_KEY", key) if key else {}
    if start and process_identity(pid) == start:
        family[pid] = start
    elif not key:
        return False
    if not family:
        return True
    for child, identity in family.items():
        signal_owned(child, identity, signal.SIGSTOP)
    complete = False
    for _ in range(8):
        table, added = process_table(), False
        while True:
            discovered = {}
            for child, record in table.items():
                parent = record["ppid"]
                if child not in family and parent in family:
                    parent_record = table.get(parent)
                    if parent_record and parent_record["start"] == family[parent]:
                        discovered[child] = record["start"]
            if not discovered:
                break
            for child, identity in discovered.items():
                signal_owned(child, identity, signal.SIGSTOP)
            family.update(discovered)
            added = True
        if not added:
            complete = True
            break
    drained = drain_processes(family, frozen=True)
    return drained and complete and not (key and marked_processes("STUDIO_GPU_JOB_KEY", key))


def marked_processes(name: str, key: str) -> dict[int, str]:
    """Live exact launch identities only; neither marker values nor argv leave this module."""
    if not key or not sys.platform.startswith("linux"):
        return {}
    marker, found = (name + "=" + key).encode(), {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            pid = int(entry.name)
            start = process_identity(pid)
            if start and marker in (entry / "environ").read_bytes().split(b"\0") and process_identity(pid) == start:
                found[pid] = start
        except (OSError, ValueError):
            continue
    return found


def private_processes(key: str) -> dict[int, str]:
    return marked_processes("STUDIO_LOCAL_GPU_KEY", key)


def stop_child_processes(key: str) -> bool:
    """Forced child fallback includes non-Comfy jobs and reparented descendants."""
    for _ in range(3):
        processes = private_processes(key)
        if not processes:
            return True
        for pid, start in processes.items():
            signal_owned(pid, start, signal.SIGSTOP)
        if not drain_processes(processes, frozen=True):
            return False
    return not private_processes(key)


class Manager:
    """A head Studio's local device ownership and child process lifecycle."""

    def __init__(self, home: Path, pack: Path):
        self.home, self.pack = home, pack
        self.pid, self.start = os.getpid(), process_identity(os.getpid())
        self.mutex = threading.RLock()
        self.children: dict[str, subprocess.Popen] = {}
        self.child_leases: dict[str, dict] = {}
        self.closing = False
        self.drain_failed = False
        self.head_uuid: str | None = None
        self.head_index: int | None = None
        self.active = False
        self.unavailable = ""  # why this Studio manages no local GPU (no head card it can claim), said in the room
        self.key: str | None = None
        self.child = bool(os.environ.get("STUDIO_LOCAL_GPU_CHILD"))
        self._owner: str | None = None

    @property
    def managed(self) -> bool:
        """A head lease alone must not change the ordinary single-GPU execution path."""
        if self.child:
            return True
        if not self.active:
            return False
        if any(alive(record, "child_") for record in self.child_leases.values()):
            return True
        try:
            return any(alive(record, "child_") for record in self.owned_records().values())
        except OSError:
            return False

    @property
    def owner(self) -> str:
        if self._owner is None:
            folder = private_dir(self.home / "sparks/local")
            with locked(folder / "owner.lock"):
                path = folder / "owner.json"
                found = load(path)
                if not found.get("id"):
                    found = {"id": uuid.uuid4().hex}
                    save(path, found)
                self._owner = found["id"]
        return self._owner

    def path(self, gpu_uuid: str) -> Path:
        if not UUID.fullmatch(gpu_uuid):
            raise ValueError("invalid NVIDIA GPU UUID")
        return runtime_dir() / (gpu_uuid + ".json")

    def record(self, gpu_uuid: str) -> dict:
        return load(self.path(gpu_uuid)) or self.child_leases.get(gpu_uuid, {})

    def authorized(self, record: dict) -> bool:
        return bool(record.get("owner") == self.owner and not record.get("head")
                    and (not alive(record) or record.get("pid") == self.pid and record.get("start") == self.start))

    @staticmethod
    def occupied(record: dict) -> bool:
        return bool(alive(record, "child_") or record.get("key") and private_processes(record["key"]))

    def owned_records(self) -> dict[str, dict]:
        if not self.active or self.child:
            return {}
        records = dict(self.child_leases)
        for path in runtime_dir().glob("GPU-*.json"):
            if UUID.fullmatch(path.stem):
                records[path.stem] = load(path)
        return {ident: record for ident, record in records.items() if self.authorized(record) and self.occupied(record)}

    def reconcile(self, workers: list[dict]) -> list[dict]:
        """Live committed ownership cannot disappear with a lost configuration row."""
        if self.child or not self.active:
            return workers
        try:
            return self._reconcile(workers)
        except (OSError, ValueError):
            # Configuration is also read under the queue lock. A missing/unwritable
            # runtime directory cannot take down the Studio's ordinary queue.
            return workers

    def _reconcile(self, workers: list[dict]) -> list[dict]:
        if not inventory()["supported"]:
            return workers
        known = {w["address"] for w in workers}
        missing = {ident: record for ident, record in self.owned_records().items() if "local:" + ident not in known}
        if not missing:
            return workers
        devices = {d["gpu_uuid"]: d for d in inventory()["devices"]}
        recovered = list(workers)
        for ident, record in missing.items():
            device = devices.get(ident) or {"index": record.get("index", "?"), "name": record.get("name") or "NVIDIA GPU"}
            recovered.append({"kind": "local", "address": "local:" + ident, "gpu_uuid": ident,
                              "name": f"GPU {device['index']} · {device['name']}", "enabled": False})
        return recovered

    def slot_ids(self, workers: list[dict]) -> set[str]:
        return ({w["gpu_uuid"] for w in workers if self.consumes_slot(w)} | set(self.owned_records()) | set(self.children)) - {self.head_uuid}

    def head(self, devices: list[dict]) -> str | None:
        # Freeze the physical UUID once resolved; ordinal reordering cannot move the head.
        if self.head_uuid:
            return self.head_uuid
        raw = os.environ.get("STUDIO_GPU", "").strip()
        if raw and not raw.isdigit():
            return None  # GPU scripts and memory_guard require a numeric physical index
        explicit = os.environ.get("STUDIO_LOCAL_GPU_UUID", "") if self.child else raw
        if not explicit:
            explicit = os.environ.get("CUDA_VISIBLE_DEVICES", "")
            if explicit and not (UUID.fullmatch(explicit) or explicit.isdigit()
                                 and os.environ.get("CUDA_DEVICE_ORDER") == "PCI_BUS_ID"):
                return None  # CUDA's default ordinal order need not match nvidia-smi
        if not explicit and len(devices) > 1:
            # as spark/gpu.sh picks: the only card with memory of its own that is not driving a display. Two such
            # cards are never chosen between silently: STUDIO_GPU names the head (``unavailable`` says so)
            compute = [d for d in devices if d["total_mb"] and not d["display_active"]]
            if len(compute) != 1:
                return None
            explicit = str(compute[0]["index"])
        selected = None
        if UUID.fullmatch(explicit):
            selected = next((d for d in devices if d["gpu_uuid"] == explicit), None)
        elif explicit.isdigit():
            selected = next((d for d in devices if d["index"] == int(explicit)), None)
        elif not explicit:
            selected = next((d for d in devices if d["index"] == 0), None)
        if selected:
            self.head_uuid = selected["gpu_uuid"]
            self.head_index = selected["index"]
        return self.head_uuid

    def conflict(self, ident: str) -> str:
        record = self.record(ident)
        if not (alive(record) or self.occupied(record)):
            return ""
        if record.get("owner") != self.owner:
            return "This GPU is owned by another Studio; stop it in that Studio first."
        if alive(record) and (record.get("pid") != self.pid or record.get("start") != self.start):
            return "Another running Studio session already owns this GPU."
        return ""

    def validate_head(self, devices: list[dict]) -> dict:
        ident = self.head(devices)
        device = next((d for d in devices if d["gpu_uuid"] == ident and d["total_mb"]), None)
        if not device:
            raise ValueError("This Studio's own GPU is not known: with two or more compute cards, set STUDIO_GPU to the "
                             "nvidia-smi index it renders on and start the Studio again.")
        if self.head_index is not None and device["index"] != self.head_index:
            raise ValueError("The head GPU's index changed; restart this Studio so its memory guard follows the same card.")
        if device.get("hidden_compute"):
            raise ValueError("The head GPU has a compute process whose identity is hidden; local workers cannot safely start.")
        return device

    def acquire_head(self, devices: list[dict]) -> None:
        device = self.validate_head(devices)
        ident = device["gpu_uuid"]
        with locked(runtime_dir() / "devices.lock"):
            why = self.conflict(ident)
            if why:
                raise ValueError(why)
            record = self.record(ident)
            if not record.get("head") and self.occupied(record):
                raise ValueError("The head GPU is already occupied by a local worker.")
            self.key = record.get("key") if record.get("owner") == self.owner and record.get("head") else None
            self.key = self.key or secrets.token_urlsafe(32)
            save(self.path(ident), {"owner": self.owner, "pid": self.pid, "start": self.start, "head": True, "key": self.key})
        self.active = True
        # Every child and the head must use the same cross-process RAM ledger.
        os.environ.setdefault("STUDIO_GPU_RAM_DIR", str(ram_dir()))
        # Lazy activation happens after Hub.start, before any managed head runner.
        os.environ.update(STUDIO_LOCAL_GPU_KEY=self.key, STUDIO_LOCAL_GPU_UUID=ident,
                          STUDIO_GPU=str(device["index"]), CUDA_VISIBLE_DEVICES=ident,
                          CUDA_DEVICE_ORDER="PCI_BUS_ID")
        self.unavailable = ""

    def owned(self, worker: dict) -> bool:
        if self.child:
            return False
        if self.active:
            record = self.record(worker["gpu_uuid"])
        else:
            # Explicitly checking a saved worker can recover an orphan without creating/chmodding runtime dirs.
            ident = worker["gpu_uuid"]
            if not UUID.fullmatch(ident):
                return False
            directory = Path(os.environ.get("STUDIO_GPU_RUNTIME_DIR", Path.home() / ".cache/studio-gpus"))
            record = load(directory / (ident + ".json"))
            if not record:
                return False
            self._owner = self._owner or load(self.home / "sparks/local/owner.json").get("id")
            if not self._owner:
                return False
        return self.authorized(record) and self.occupied(record)

    def guard_reason(self, worker: dict, device: dict) -> str:
        """UUID CUDA pinning survives a reorder; a running numeric guard does not."""
        if not self.active:
            return ""
        if device.get("hidden_compute"):
            return "A compute process on this GPU has a hidden identity; its ownership cannot be checked."
        try:
            record = self.record(worker["gpu_uuid"])
        except OSError:
            return "Local GPU ownership could not be read; check the runtime directory before using this worker."
        if self.authorized(record) and self.occupied(record) and record.get("index") != device["index"]:
            return "This GPU's index changed; stop and start its worker so the memory guard follows the same card."
        return ""

    def consumes_slot(self, worker: dict) -> bool:
        """Unticking scheduling does not make a still-running process disappear."""
        return worker.get("kind") == "local" and bool(worker.get("enabled", True) or self.owned(worker))

    def credentials(self, worker: dict) -> tuple[str, str]:
        record = self.record(worker["gpu_uuid"])
        if not self.authorized(record) or not alive(record, "child_"):
            raise OSError("No live local worker owned by this Studio")
        return f"http://127.0.0.1:{int(record['port'])}", record["key"]

    def register_child(self) -> None:
        """A child may serve only after its parent committed this exact process lease."""
        ident, key = os.environ.get("STUDIO_LOCAL_GPU_UUID", ""), os.environ.get("STUDIO_LOCAL_GPU_KEY", "")
        with locked(runtime_dir() / "devices.lock"):
            record = self.record(ident)
            if (not key or not secrets.compare_digest(str(record.get("key", "")), key)
                    or record.get("gpu_uuid") != ident or record.get("head")
                    or record.get("child_pid") != self.pid or record.get("child_start") != self.start):
                raise OSError("Local GPU child has no matching committed process lease")

    def devices(self, workers: list[dict], shown) -> dict:
        found = inventory()
        head = self.head(found["devices"])
        if found["supported"] and not head:
            self.unavailable = ("This Studio's own GPU is not known: with two or more compute cards, set STUDIO_GPU to the "
                                "nvidia-smi index it renders on and start the Studio again.")
        workers = self.reconcile(workers)
        locals_ = {w["address"]: w for w in workers if w.get("kind") == "local"}
        managed = (1 if head else 0) + len(self.slot_ids(workers))
        devices = []
        seen = set()
        for raw in found["devices"]:
            device = {key: value for key, value in raw.items() if key not in ("compute_pids", "gpu_uuid")}
            worker = locals_.get(device["id"])
            is_head = raw["gpu_uuid"] == head
            reason = (self.conflict(raw["gpu_uuid"]) if self.active else "") if not self.child else "Local child workers cannot manage other GPUs."
            if not reason and not raw["total_mb"]:
                reason = "This device has no dedicated VRAM; it keeps the existing Spark path."
            if not reason and not is_head and not (worker and self.consumes_slot(worker)) and managed >= LIMIT:
                reason = "At most four local GPUs can be managed, including the head; stop and disable another first."
            if not reason and not is_head and (raw.get("hidden_compute") or raw["compute_pids"] and not (worker and self.owned(worker))):
                reason = "An external compute workload is using this GPU; it is left running."
            device.update(head=is_head, worker=shown(worker) if worker else None, reason=reason)
            devices.append(device)
            seen.add(device["id"])
        for address, worker in locals_.items():
            if address not in seen:
                devices.append({"id": address, "index": None, "name": worker.get("name") or "Missing GPU", "total_mb": None,
                                "used_mb": None, "free_mb": None, "util_pct": None, "temp_c": None, "power_w": None,
                                "display_active": False, "head": worker["gpu_uuid"] == head, "worker": shown(worker),
                                "reason": "This GPU UUID is not present; reconnect the card before starting it."})
        return {"supported": found["supported"] and not self.child, "limit": LIMIT, "head_id": "local:" + head if head else None,
                "managed": managed, "devices": devices, "unavailable": self.unavailable}

    def start_worker(self, worker: dict, workers: list[dict], *, allow_display: bool = False) -> None:
        if self.child:
            raise ValueError("Local child workers cannot start other workers")
        with self.mutex:
            if self.closing:
                raise ValueError("This Studio is closing; no local worker can start")
            devices = inventory(fresh=True)["devices"]
            if self.closing:
                raise ValueError("This Studio is closing; no local worker can start")
            self.acquire_head(devices)
            ident = worker["gpu_uuid"]
            device = next((d for d in devices if d["gpu_uuid"] == ident), None)
            if device is None:
                raise ValueError("This GPU UUID is no longer present")
            if ident == self.head_uuid:
                raise ValueError("The head GPU already runs this Studio")
            if not device["total_mb"]:
                raise ValueError("Local workers require dedicated GPU VRAM")
            if device["display_active"] and not (allow_display or worker.get("allow_display")):
                raise ValueError("This GPU drives a display; explicitly allow display-GPU rendering before starting it")
            if 1 + len(self.slot_ids(workers) - {ident}) >= LIMIT:
                raise ValueError("At most four local GPUs can be managed, including the head")
            python = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")) / "venv/bin/python"
            if not python.is_file():
                raise ValueError("The shared PREFIX/venv Python is missing; install this Studio before starting local workers")
            with locked(runtime_dir() / "devices.lock"):
                why = self.conflict(ident)
                if why:
                    raise ValueError(why)
                if self.owned(worker) and alive(self.record(ident), "child_"):
                    return
                stale = self.record(ident)
                if stale.get("owner") == self.owner and stale.get("key") and not alive(stale, "child_"):
                    if not stop_child_processes(stale["key"]):
                        raise OSError("Owned GPU processes have not exited; their lease is retained")
                    device = next((d for d in inventory(fresh=True)["devices"] if d["gpu_uuid"] == ident), None)
                    if device is None:
                        raise ValueError("This GPU UUID is no longer present")
                if device["compute_pids"] or device.get("hidden_compute") or (device["used_mb"] or 0) > 1024:
                    raise ValueError("An external or unattributed GPU workload is using this card; it is left running")
                folder = private_dir(self.home / "sparks/local" / ident)
                # Binding both sockets together prevents duplicate selection within this launch;
                # the device transaction also serializes launches by other Studio heads.
                sockets = []
                try:
                    for _ in range(2):
                        sock = socket.socket()
                        sock.bind(("127.0.0.1", 0))
                        sockets.append(sock)
                    # Live leases reserve Comfy ports even while Comfy is unloaded.
                    reserved = {int(r.get(key, 0)) for path in runtime_dir().glob("GPU-*.json")
                                if (r := load(path)) and (alive(r) or alive(r, "child_")) for key in ("port", "comfy_port")}
                    for i in range(2):
                        while sockets[i].getsockname()[1] in reserved:
                            sockets[i].close()
                            sockets[i] = socket.socket()
                            sockets[i].bind(("127.0.0.1", 0))
                    port, comfy_port = (sock.getsockname()[1] for sock in sockets)
                    key = secrets.token_urlsafe(32)
                    env = dict(os.environ)
                    for name in ("SPARK_COMFY_URL", "STUDIO_UPDATE_SESSION", "STUDIO_UPDATE_BRANCH"):
                        env.pop(name, None)
                    env.update(STUDIO_HOME=str(folder), STUDIO_PIDFILE=str(folder / "app.pid"), HOST="127.0.0.1", PORT=str(port),
                               SPARK_COMFY_PORT=str(comfy_port), STUDIO_GPU=str(device["index"]), CUDA_VISIBLE_DEVICES=ident,
                               CUDA_DEVICE_ORDER="PCI_BUS_ID", STUDIO_GPU_LOCK=str(folder / "gpu.lock.d"),
                               STUDIO_LOCAL_GPU_CHILD="1", STUDIO_LOCAL_GPU_UUID=ident, STUDIO_LOCAL_GPU_KEY=key,
                               STUDIO_GPU_RAM_DIR=str(ram_dir()))
                    # The app receives an already-bound socket; no release/rebind race.
                    env["STUDIO_LOCAL_GPU_FD"] = str(sockets[0].fileno())
                    with open(folder / "app.log", "ab") as log:
                        os.chmod(folder / "app.log", 0o600)
                        if self.closing:
                            raise ValueError("This Studio is closing; no local worker can start")
                        process = subprocess.Popen([str(python), str(self.pack / "app/server.py")], cwd=self.pack, env=env,
                                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                                                   pass_fds=(sockets[0].fileno(),))
                    start = process_identity(process.pid)
                    if not start:
                        process.wait(timeout=5)
                        raise OSError("The local worker exited before acquiring its process identity")
                    record = {"owner": self.owner, "pid": self.pid, "start": self.start, "child_pid": process.pid,
                              "child_start": start, "port": port, "comfy_port": comfy_port, "key": key,
                              "home": str(folder), "gpu_uuid": ident, "index": device["index"], "name": device["name"]}
                    self.children[ident] = process
                    self.child_leases[ident] = record
                    try:
                        save(self.path(ident), record)
                    except OSError:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                        if stop_child_processes(key) and not self.occupied(record):
                            self.children.pop(ident, None)
                            self.child_leases.pop(ident, None)
                        raise
                finally:
                    for sock in sockets:
                        sock.close()

    def stop_worker(self, worker: dict) -> None:
        ident = worker["gpu_uuid"]
        with self.mutex, locked(runtime_dir() / "devices.lock"):
            record = self.record(ident)
            if self.conflict(ident):
                raise ValueError("This GPU is owned by another Studio; its processes are left running")
            if not self.authorized(record):
                return
            pid, start = record.get("child_pid"), record.get("child_start")
            if pid and start and process_identity(pid) == start:
                signal_owned(pid, start, signal.SIGTERM)
                until = time.monotonic() + 20
                while process_identity(pid) == start and time.monotonic() < until:
                    time.sleep(0.1)
            # Includes detached training/render runners, engine processes and guards;
            # only this private launch identity is ours, never a port or GPU alone.
            drained = stop_child_processes(record.get("key", ""))
            if pid and start and process_identity(pid) == start:
                drained = drain_processes({pid: start}) and drained
            if not drained or self.occupied(record):
                raise OSError("Owned GPU processes have not exited; their lease is retained")
            process = self.children.pop(ident, None)
            if process:
                process.wait(timeout=5)
            self.child_leases.pop(ident, None)
            current = load(self.path(ident))
            if current.get("owner") == self.owner and current.get("key") == record.get("key"):
                self.path(ident).unlink(missing_ok=True)

    def close(self, workers: list[dict]) -> bool:
        with self.mutex:
            self.closing = True
            if self.child or not (self.active or self.children or self.child_leases):
                return True
            identities = ({w["gpu_uuid"] for w in workers if w.get("kind") == "local"}
                          | set(self.owned_records()) | set(self.children) | set(self.child_leases))
            drained = True
            for ident in identities:
                try:
                    self.stop_worker({"kind": "local", "gpu_uuid": ident, "address": "local:" + ident})
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    drained = False
            if self.owned_records() or self.children or self.child_leases:
                drained = False
            if self.drain_failed:
                drained = False
            if drained and self.active and self.head_uuid:
                with locked(runtime_dir() / "devices.lock"):
                    record = self.record(self.head_uuid)
                    if record.get("owner") == self.owner and record.get("pid") == self.pid and record.get("start") == self.start:
                        self.path(self.head_uuid).unlink(missing_ok=True)
                self.active = False
            return drained
