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
except ImportError:  # Macs keep the existing remote-Spark behavior.
    fcntl = None

LIMIT = 4
CACHE_SECONDS = 3
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
    base = os.environ.get("XDG_RUNTIME_DIR")
    default = Path(base) / "studio-gpus" if base else Path.home() / ".cache/studio-gpus"
    return private_dir(Path(os.environ.get("STUDIO_GPU_RUNTIME_DIR", default)))


def ram_dir() -> Path:
    return private_dir(Path(os.environ.get("STUDIO_GPU_RAM_DIR", runtime_dir() / "ram")))


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
        devices.append({"id": "local:" + ident, "gpu_uuid": ident, "index": index,
                        "name": gpu.findtext("product_name", "NVIDIA GPU"),
                        "total_mb": total, "used_mb": number(gpu.findtext("fb_memory_usage/used")),
                        "free_mb": number(gpu.findtext("fb_memory_usage/free")),
                        "util_pct": number(gpu.findtext("utilization/gpu_util")),
                        "temp_c": number(gpu.findtext("temperature/gpu_temp")),
                        "power_w": power,
                        "display_active": gpu.findtext("display_active", "Disabled").lower() == "enabled",
                        "compute_pids": [int(p.findtext("pid")) for p in gpu.findall("processes/process_info")
                                         if p.findtext("type") in ("C", "C+G") and (p.findtext("pid") or "").isdigit()]})
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


def model_ram_mb() -> int:
    """Already allocated anonymous model RAM of this exact GPU session, never file cache or another GPU."""
    key = os.environ.get("STUDIO_LOCAL_GPU_KEY")
    if not key:
        return 0
    held_kb = 0
    for pid, start in private_processes(key).items():
        try:
            proc = Path("/proc") / str(pid)
            if b"STUDIO_GPU_MODEL_RAM=1" not in (proc / "environ").read_bytes().split(b"\0"):
                continue
            anon = next((int(line.split()[1]) for line in (proc / "smaps_rollup").read_text().splitlines()
                         if line.startswith("Pss_Anon:")), 0)
            if process_identity(pid) == start:
                held_kb += max(0, anon)
        except (OSError, ValueError, IndexError):
            continue
    return held_kb // 1024


@contextlib.contextmanager
def ram_reservation(need_mb: int | None, job: dict):
    """Wait cancellably for shared host RAM; keep max(8 GiB, 10% RAM) untouched.

    Reserve growth to the existing measured peak, crediting only already allocated
    anonymous RAM in this session's owned model servers. File cache is already part
    of MemAvailable and cannot be credited. Other active reservations remain conservative.
    An unmeasured job (None) is exclusive rather than pretending to need zero RAM.
    """
    need = max(0, int(need_mb)) if need_mb is not None else None
    directory = ram_dir()
    path, token = directory / "reservations.json", uuid.uuid4().hex
    owner = {"pid": os.getpid(), "start": process_identity(os.getpid()), "need_mb": need}
    admitted = False
    try:
        while job.get("status") not in ("cancelled", "failed", "done"):
            with locked(directory / "reservations.lock"):
                if job.get("status") in ("cancelled", "failed", "done"):
                    break
                reservations = {key: value for key, value in load(path).items() if alive(value)}
                total, available = memory()
                floor = max(8192, (total + 9) // 10)
                resident = model_ram_mb() if need is not None else 0
                growth = max(0, need - resident) if need is not None else None
                exclusive = any(r.get("need_mb") is None for r in reservations.values())
                fits = not reservations if need is None else not exclusive and available - sum(r["need_mb"] for r in reservations.values()) - growth >= floor
                if owner["start"] and total and available >= floor and fits:
                    owner.update(need_mb=need, resident_mb=resident)
                    reservations[token] = owner
                    admitted = True
                save(path, reservations)
            if admitted:
                break
            job["progress"] = {"stage": "Waiting for shared host RAM", "at": 0, "of": 1}
            time.sleep(0.2)
        yield admitted
    finally:
        if admitted:
            with locked(directory / "reservations.lock"):
                reservations = load(path)
                reservations.pop(token, None)
                save(path, reservations)


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
    if not key:
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
        self.head_uuid: str | None = None
        self.active = False
        self.key: str | None = None
        self.child = bool(os.environ.get("STUDIO_LOCAL_GPU_CHILD"))
        self._owner: str | None = None

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
        records = dict(self.child_leases)
        for path in runtime_dir().glob("GPU-*.json"):
            if UUID.fullmatch(path.stem):
                records[path.stem] = load(path)
        return {ident: record for ident, record in records.items() if self.authorized(record) and self.occupied(record)}

    def reconcile(self, workers: list[dict]) -> list[dict]:
        """Live committed ownership cannot disappear with a lost configuration row."""
        if self.child:
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
            if explicit and not explicit.isdigit():
                return None
        if not explicit and len(devices) > 1:
            return None  # never silently select a head from several physical cards
        selected = None
        if UUID.fullmatch(explicit):
            selected = next((d for d in devices if d["gpu_uuid"] == explicit), None)
        elif explicit.isdigit():
            selected = next((d for d in devices if d["index"] == int(explicit)), None)
        elif not explicit:
            selected = next((d for d in devices if d["index"] == 0), None)
        if selected:
            self.head_uuid = selected["gpu_uuid"]
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

    def acquire_head(self, devices: list[dict]) -> None:
        ident = self.head(devices)
        if not ident or not any(d["gpu_uuid"] == ident and d["total_mb"] for d in devices):
            raise ValueError("The head GPU is missing or has no dedicated VRAM; select STUDIO_GPU before starting the Studio.")
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

    def owned(self, worker: dict) -> bool:
        record = self.record(worker["gpu_uuid"])
        return self.authorized(record) and self.occupied(record)

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
        workers = self.reconcile(workers)
        locals_ = {w["address"]: w for w in workers if w.get("kind") == "local"}
        managed = (1 if head else 0) + len(self.slot_ids(workers))
        devices = []
        seen = set()
        for raw in found["devices"]:
            device = {key: value for key, value in raw.items() if key not in ("compute_pids", "gpu_uuid")}
            worker = locals_.get(device["id"])
            is_head = raw["gpu_uuid"] == head
            reason = self.conflict(raw["gpu_uuid"]) if not self.child else "Local child workers cannot manage other GPUs."
            if not reason and not raw["total_mb"]:
                reason = "This device has no dedicated VRAM; it keeps the existing Spark path."
            if not reason and not is_head and not (worker and self.consumes_slot(worker)) and managed >= LIMIT:
                reason = "At most four local GPUs can be managed, including the head; stop and disable another first."
            if not reason and not is_head and raw["compute_pids"] and not (worker and self.owned(worker)):
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
                "managed": managed, "devices": devices}

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
                if device["compute_pids"] or (not device["display_active"] and (device["used_mb"] or 0) > 1024):
                    raise ValueError("An external GPU workload is using this card; it is left running")
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
                               SPARK_COMFY_PORT=str(comfy_port), STUDIO_GPU=str(device["index"]), CUDA_VISIBLE_DEVICES=str(device["index"]),
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
            if drained and self.active and self.head_uuid:
                with locked(runtime_dir() / "devices.lock"):
                    record = self.record(self.head_uuid)
                    if record.get("owner") == self.owner and record.get("pid") == self.pid and record.get("start") == self.start:
                        self.path(self.head_uuid).unlink(missing_ok=True)
                self.active = False
            return drained
