"""A machine-wide workload governor on a DGX Spark, when one runs beside the studio (opt-in).

Some Sparks run more than this studio: other model servers, other people's jobs. Such a machine may have a workload
governor, a small service that gives out memory reservations through an admission socket, so that two heavy jobs
never start on the same memory. ``SPARK_GOVERNOR`` names its folder; unset (the default), the studio behaves as
before and nothing here runs.

The governor folder is expected to hold ``admission_client.py`` (``rpc`` and the ``--run`` launcher that reserves,
binds the child's process session and releases at its end), ``process_binding.py`` (``process`` and ``session``),
``governor.py`` (``collect_snapshot``, ``sensor_errors``, ``GIB``) and ``state/reservations.json``.

With it, the studio's renderer (``managed_comfy.py``) and Drama 3 (``managed_voice.py``) start only inside a
reservation of their own, a start gate (``start_gate.py``) checks ownership, memory, pressure and temperatures before
every job is submitted, and a telemetry thread stops a server whose machine goes past the limits. All of it is in
addition to the memory guard (``memory_guard.py watch``), which still stands beside every ComfyUI server.

Environment: ``SPARK_GOVERNOR`` (the folder), ``SPARK_GOVERNOR_SOCKET`` (its admission socket, default
``state/admission.sock`` in it), ``SPARK_GOVERNOR_OWNER`` (the renderer's owner name, default
``tensorfold-studio-comfy-<port>``), ``SPARK_GOVERNOR_VOICE_OWNER`` (``tensorfold-studio-drama3``),
``SPARK_GOVERNOR_RESERVE_GIB`` (85, what a render reserves), ``SPARK_GOVERNOR_VOICE_GIB`` (55),
``SPARK_GOVERNOR_MIN_FREE_GIB`` (12, never lower), ``SPARK_GOVERNOR_FREE_URL`` (another ComfyUI on this machine whose
idle model cache is freed with its ``/free`` before a reservation is asked for; refused while its queue is busy),
``SPARK_THERMAL_ZONES`` (how many thermal zones the machine has; by default the count first seen must stay).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio")).expanduser()

# the start gate's limits beside the governor's own: a cool start, and memory that is not under pressure
GPU_START_C = 80.0  # a job is submitted only below this GPU temperature
GPU_STOP_C = 90.0  # a server is stopped at or above it, or on any thermal slowdown flag
ZONE_MARGIN_C = 5.0  # a platform zone's ceiling: its hot or critical trip point less this
ZONE_CEILING_C = 95.0  # when a zone has no such trip point
PSI_SOME, PSI_FULL = 10.0, 2.0  # memory pressure (avg10 %) at which a start waits and a run starts counting
PRESSURE_STOP_S = 60.0  # sustained pressure for this long stops a running server
GATE_TRIES, GATE_WAIT_S = 60, 3.0  # the start gate waits up to three minutes for the machine to cool or calm


def folder() -> Path | None:
    value = os.environ.get("SPARK_GOVERNOR", "").strip()
    return Path(value).expanduser() if value else None


def enabled() -> bool:
    return folder() is not None


def port() -> str:
    return os.environ.get("SPARK_COMFY_PORT", "8190")


def owner() -> str:
    return os.environ.get("SPARK_GOVERNOR_OWNER") or f"tensorfold-studio-comfy-{port()}"


def voice_owner() -> str:
    return os.environ.get("SPARK_GOVERNOR_VOICE_OWNER") or "tensorfold-studio-drama3"


def number(name: str, default: float, least: float | None = None) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = default
    return max(value, least) if least is not None else value


def min_free_gib() -> float:
    # a floor is never lowered below the studio's own default
    return number("SPARK_GOVERNOR_MIN_FREE_GIB", 12.0, least=12.0)


def pidfile(kind: str = "comfy") -> Path:
    """Where a managed server writes its process identity: one file a renderer port, so two studios on one machine
    (and one PREFIX) never stop or vouch for each other's server."""

    return PREFIX / (f"managed-comfy-{port()}.json" if kind == "comfy" else "drama3/managed-server.json")


def modules():
    """The governor's own modules (admission_client, process_binding, governor), imported from its folder."""

    where = folder()
    if where is None:
        raise RuntimeError("SPARK_GOVERNOR is not set")
    if not (where / "admission_client.py").is_file():
        raise RuntimeError(f"no workload governor at {where} (admission_client.py is missing)")
    if str(where) not in sys.path:
        sys.path.insert(0, str(where))
    import admission_client  # noqa: PLC0415
    import governor  # noqa: PLC0415
    import process_binding  # noqa: PLC0415

    return admission_client, process_binding, governor


def socket_path() -> str:
    return os.environ.get("SPARK_GOVERNOR_SOCKET") or str(folder() / "state" / "admission.sock")


def rpc(request: dict) -> dict:
    admission_client, _, _ = modules()
    return admission_client.rpc(request, socket_path())


def client_command(who: str, workload: str, gib: float, command: list[str]) -> list[str]:
    """``admission_client.py --run``: a reservation for ``who``, the command started inside it, released at its end."""

    admission_client, _, _ = modules()
    return [sys.executable, admission_client.__file__, "--owner", who, "--workload", workload, "--phase", "start",
            "--socket", socket_path(), "--estimated-incremental-gib", f"{gib:g}", "--min-free-gib", f"{min_free_gib():g}",
            *(["--residency", "new"] if workload == "h3" else []), "--run", *command]


def reservations() -> list[dict]:
    """The governor's reservations with their tokens (its status call leaves the tokens out)."""

    return json.loads((folder() / "state" / "reservations.json").read_text()).get("reservations", [])


def write_identity(kind: str, pid: int) -> dict:
    _, process_binding, _ = modules()
    identity = process_binding.process(pid)
    path = pidfile(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(identity))
    return identity


def live_identity(kind: str) -> dict | None:
    """The managed server's process, when the one its file names is still that process and ours."""

    _, process_binding, _ = modules()
    path = pidfile(kind)
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    current = process_binding.process(saved.get("pid"))
    if not current or current["start"] != saved.get("start") or current["uid"] != os.getuid():
        return None
    return current


def owned(kind: str = "comfy") -> bool:
    """Whether the server of ``kind`` is alive and inside a live reservation bound to its own process session.
    Inside an admitted command (``SPARK_ADMISSION_TOKEN``) the reservation is that token's."""

    _, process_binding, _ = modules()
    rows = reservations()
    token = os.environ.get("SPARK_ADMISSION_TOKEN")
    if token:
        row = next((r for r in rows if r.get("token") == token and r.get("owner") == os.environ.get("SPARK_WORKLOAD_OWNER")), None)
        return bool(row and row.get("binding") and process_binding.session(row["binding"]))
    current = live_identity(kind)
    if current is None:
        return False
    who = owner() if kind == "comfy" else voice_owner()
    return any(r.get("owner") == who and r.get("binding") and current["pid"] in process_binding.session(r["binding"])
               for r in rows)


def reconcile_dead(who: str) -> None:
    """Release only ``who``'s bound reservations whose process session has ended. Live, unbound and other owners'
    reservations are left alone (the governor checks the identity again under its own lock)."""

    _, process_binding, _ = modules()
    for row in reservations():
        if row.get("owner") != who or not row.get("binding") or process_binding.session(row["binding"]):
            continue
        result = rpc({"op": "release", "owner": who, "token": row["token"]})
        if not result.get("allowed"):
            raise RuntimeError("could not release the reservation of a server that has ended: "
                               + str(result.get("reason", result.get("error"))))
        print(f"[studio] released the reservation of an ended {who} server", flush=True)


def free_other_comfy() -> None:
    """``SPARK_GOVERNOR_FREE_URL``: another ComfyUI on this machine gives back its idle model cache (``/free``, never
    ``/interrupt``). A busy one is never touched: the start is refused instead."""

    url = os.environ.get("SPARK_GOVERNOR_FREE_URL", "").rstrip("/")
    if not url:
        return

    def call(path: str, data: dict | None = None) -> dict:
        request = urllib.request.Request(url + path, data=None if data is None else json.dumps(data).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as answer:
            raw = answer.read()
        return json.loads(raw) if raw else {}

    try:
        queue = call("/queue")
    except OSError:
        return  # not running: nothing to free
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("the other ComfyUI on this machine is busy; wait for its queue to finish")
    call("/free", {"unload_models": True, "free_memory": True})
    time.sleep(5)  # its release is asynchronous; the reservation itself is what counts


# ---------- the machine's state ----------

def zones() -> list[dict]:
    out = []
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        ceiling = ZONE_CEILING_C
        for kind in zone.glob("trip_point_*_type"):
            if kind.read_text().strip() in ("critical", "hot"):
                trip = float(kind.with_name(kind.name.replace("_type", "_temp")).read_text()) / 1000
                ceiling = min(ceiling, trip - ZONE_MARGIN_C)
        out.append({"zone": zone.name, "temp_c": float((zone / "temp").read_text()) / 1000, "ceiling_c": ceiling})
    return out


def expected_zones(seen: int) -> int:
    try:
        return int(os.environ["SPARK_THERMAL_ZONES"])
    except (KeyError, ValueError):
        return seen


def reasons(snapshot: dict, found: list[dict], want_zones: int, pressure_s: float, gib: float) -> list[str]:
    """Why the machine is past its limits now ([] when it is not): missing telemetry, memory under the floor, a hot
    or throttled GPU, a hot or missing platform zone, sustained memory pressure."""

    out = [str(e) for e in snapshot.get("sensor_errors", [])]
    if not snapshot.get("memory") or not snapshot.get("gpus") or not snapshot.get("psi"):
        return out + ["required telemetry missing"]
    if snapshot["memory"]["available_bytes"] < min_free_gib() * gib:
        out.append(f"free memory below {min_free_gib():g} GiB")
    if any(g["temperature_c"] >= GPU_STOP_C or any((g.get("thermal_flags") or {}).values()) for g in snapshot["gpus"]):
        out.append("GPU temperature ceiling or thermal slowdown")
    if not found or len(found) != want_zones or any(z["temp_c"] >= z["ceiling_c"] for z in found):
        out.append("platform temperature ceiling or unavailable zone")
    if pressure_s >= PRESSURE_STOP_S:
        out.append("sustained memory pressure")
    return out


def pressured(snapshot: dict) -> bool:
    psi = snapshot.get("psi") or {}
    return psi.get("some_avg10", 0) >= PSI_SOME or psi.get("full_avg10", 0) >= PSI_FULL


def start_reasons(snapshot: dict, found: list[dict], want_zones: int, gib: float) -> list[str]:
    """``reasons`` with the stricter limits of a start: a cool GPU and no memory pressure now."""

    out = reasons(snapshot, found, want_zones, 0, gib)
    if any(g["temperature_c"] >= GPU_START_C for g in snapshot.get("gpus") or []):
        out.append(f"cool start: the GPU must be below {GPU_START_C:g} C")
    if pressured(snapshot):
        out.append("cool start: memory pressure is high")
    return out


def log(name: str, record: dict) -> None:
    """One line of what a gate saw, in ``$PREFIX/managed-logs`` (the reasons and the main figures, not raw dumps)."""

    where = PREFIX / "managed-logs"
    try:
        where.mkdir(parents=True, exist_ok=True)
        with (where / name).open("a") as out:
            out.write(json.dumps({"time": time.time(), **record}) + "\n")
    except OSError:
        pass


def gate(kind: str = "comfy", tries: int = GATE_TRIES, wait_s: float = GATE_WAIT_S) -> None:
    """Before a job is submitted to a managed server: it must hold its reservation, and the machine must be cool and
    calm; waits up to ``tries * wait_s`` for that, then raises RuntimeError with the reasons."""

    _, _, governor = modules()
    want = None
    for _ in range(tries):
        if not owned(kind):
            raise RuntimeError("the studio's server has no live reservation of its own")
        snapshot = governor.collect_snapshot()
        errors = governor.sensor_errors(snapshot)
        if errors:
            raise RuntimeError(f"telemetry unavailable: {errors}")
        found = zones()
        want = expected_zones(len(found)) if want is None else want
        why = start_reasons(snapshot, found, want, governor.GIB)
        mem = snapshot.get("memory") or {}
        log(f"start-gates-{kind}.jsonl", {"port": port(), "reasons": why,
                                          "available_gib": round(mem.get("available_bytes", 0) / governor.GIB, 1),
                                          "gpu_c": [g.get("temperature_c") for g in snapshot.get("gpus") or []],
                                          "psi": snapshot.get("psi")})
        if not why:
            return
        print("[studio] waiting before submission: " + "; ".join(why), file=sys.stderr, flush=True)
        time.sleep(wait_s)
    raise RuntimeError(f"the start checks did not pass within {tries * wait_s / 60:g} minutes")


class Telemetry(threading.Thread):
    """Every three seconds while a managed server runs: the machine against ``reasons``. The first failure is kept in
    ``failure`` and the thread ends; the owner stops its server."""

    def __init__(self, name: str) -> None:
        super().__init__(daemon=True)
        self.name_ = name
        self.failure: list[str] = []
        self.done = threading.Event()

    def run(self) -> None:
        try:
            _, _, governor = modules()
        except Exception as error:  # noqa: BLE001
            self.failure.append(f"telemetry failed: {error}")
            return
        pressure_since, want = None, None
        while not self.done.is_set():
            began = time.monotonic()
            try:
                snapshot = governor.collect_snapshot()
                found = zones()
                want = expected_zones(len(found)) if want is None else want
                pressure_since = (pressure_since or time.monotonic()) if pressured(snapshot) else None
                why = reasons(snapshot, found, want, 0 if pressure_since is None else time.monotonic() - pressure_since, governor.GIB)
                if time.monotonic() - began > 25:
                    why.append("telemetry collection gap")
                if why:
                    log(f"stops-{self.name_}.jsonl", {"port": port(), "reasons": why})
                    self.failure.extend(why)
                    return
            except Exception as error:  # noqa: BLE001 - no telemetry is a stop, not a pass
                self.failure.append(f"telemetry failed: {error}")
                return
            self.done.wait(3)
