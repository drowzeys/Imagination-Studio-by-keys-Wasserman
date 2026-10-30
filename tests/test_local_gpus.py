"""Deterministic local GPU inventory, lease, lifecycle and host-RAM safety tests."""
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import local_gpus


def gpu(index, *, total=24000, display=False, compute=()):
    ident = f"GPU-00000000-0000-0000-0000-{index + 1:012d}"
    return {"id": "local:" + ident, "gpu_uuid": ident, "index": index, "name": f"Synthetic GPU {index}",
            "total_mb": total, "used_mb": 0, "free_mb": total, "util_pct": 0, "temp_c": 30, "power_w": 10,
            "display_active": display, "compute_pids": list(compute)}


def worker(device):
    return {"kind": "local", "address": device["id"], "gpu_uuid": device["gpu_uuid"], "name": device["name"], "enabled": True}


def xml_devices(count):
    return "<nvidia_smi_log>" + "".join(
        f"<gpu><uuid>{gpu(i)['gpu_uuid']}</uuid><product_name>Synthetic GPU {i}</product_name>"
        f"<fb_memory_usage><total>{(i + 1) * 12000} MiB</total><used>256 MiB</used><free>11744 MiB</free></fb_memory_usage>"
        f"<display_active>{'Enabled' if i == 0 else 'Disabled'}</display_active>"
        "<utilization><gpu_util>5 %</gpu_util></utilization><temperature><gpu_temp>34 C</gpu_temp></temperature>"
        "<gpu_power_readings><power_draw>25.5 W</power_draw></gpu_power_readings>"
        "<processes><process_info><pid>43210</pid><type>C</type></process_info></processes></gpu>"
        for i in range(count)) + "</nvidia_smi_log>"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.setenv("STUDIO_GPU", "0")
    for name in ("STUDIO_LOCAL_GPU_CHILD", "STUDIO_LOCAL_GPU_UUID", "STUDIO_LOCAL_GPU_KEY", "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"):
        monkeypatch.delenv(name, raising=False)
    prefix = tmp_path / "prefix"
    (prefix / "venv/bin").mkdir(parents=True)
    (prefix / "venv/bin/python").write_text("synthetic executable")
    monkeypatch.setenv("PREFIX", str(prefix))
    return tmp_path


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 7])
def test_full_inventory_all_devices_and_mixed_vram(count, monkeypatch, real_inventory):
    calls = []

    def query(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(returncode=0, stdout=xml_devices(count))

    monkeypatch.setattr(local_gpus.subprocess, "run", query)
    monkeypatch.setattr(local_gpus.sys, "platform", "linux")
    monkeypatch.setattr(local_gpus, "_CACHE", {"at": float("-inf"), "value": None})
    one = local_gpus.inventory()
    two = local_gpus.inventory()
    assert one == two and len(calls) == 1
    assert len(one["devices"]) == count
    assert one["supported"] is bool(count)
    assert [d["total_mb"] for d in one["devices"]] == [(i + 1) * 12000 for i in range(count)]
    if count:
        assert one["devices"][0]["display_active"]
        assert one["devices"][0]["compute_pids"] == [43210]
        assert one["devices"][0]["power_w"] == 25.5


@pytest.mark.parametrize("failure", [OSError("missing"), subprocess.TimeoutExpired("nvidia-smi", 5)])
def test_inventory_failure_is_bounded_and_unsupported(failure, monkeypatch, real_inventory):
    def query(*args, **kwargs):
        raise failure

    monkeypatch.setattr(local_gpus.sys, "platform", "linux")
    monkeypatch.setattr(local_gpus.subprocess, "run", query)
    assert local_gpus.inventory(fresh=True) == {"supported": False, "devices": []}


def test_mac_never_queries_nvidia(monkeypatch, real_inventory):
    monkeypatch.setattr(local_gpus.sys, "platform", "darwin")
    monkeypatch.setattr(local_gpus.subprocess, "run", lambda *a, **k: pytest.fail("Mac queried NVIDIA"))
    assert not local_gpus.inventory(fresh=True)["supported"]


@pytest.fixture
def launcher(runtime, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("local worker leases and launch identities require Linux")
    devices, states, launches, killed = [gpu(0), gpu(1), gpu(2), gpu(3), gpu(4)], {os.getpid(): "synthetic-head-start"}, [], []
    monkeypatch.setattr(local_gpus, "inventory", lambda **kwargs: {"supported": True, "devices": devices})
    monkeypatch.setattr(local_gpus, "process_identity", lambda pid: states.get(int(pid)))
    monkeypatch.setattr(local_gpus, "stop_child_comfy", lambda key=None: None)

    def kill(pid, sig):
        killed.append((pid, sig))
        states.pop(pid, None)

    monkeypatch.setattr(local_gpus.os, "kill", kill)

    class Process:
        def __init__(self, argv, **options):
            self.pid = os.getpid() + 100000 + len(launches)
            states[self.pid] = f"synthetic-child-{self.pid}"
            with socket.socket(fileno=os.dup(options["pass_fds"][0])) as bound:
                options["bound_address"] = bound.getsockname()
            launches.append((argv, options, self))

        def wait(self, timeout):
            assert timeout > 0
            states.pop(self.pid, None)
            return 0

        def terminate(self):
            kill(self.pid, signal.SIGTERM)

        def kill(self):
            kill(self.pid, signal.SIGKILL)

    monkeypatch.setattr(local_gpus.subprocess, "Popen", Process)
    manager = local_gpus.Manager(runtime / "studio", runtime / "pack")
    return SimpleNamespace(manager=manager, devices=devices, states=states, launches=launches, killed=killed)


def test_stable_uuid_survives_ordinal_reordering(launcher):
    m, devices = launcher.manager, launcher.devices
    assert m.head(devices) == devices[0]["gpu_uuid"]
    target = worker(devices[1])
    devices[1]["index"] = 3
    devices[3]["index"] = 1
    m.start_worker(target, [])
    assert launcher.launches[0][1]["env"]["STUDIO_GPU"] == "3"
    assert launcher.launches[0][1]["env"]["CUDA_VISIBLE_DEVICES"] == target["gpu_uuid"]
    devices[0]["index"] = 4
    assert m.head(devices) == devices[0]["gpu_uuid"]
    assert m.owned(target)


def test_four_card_limit_includes_head_and_allows_subset(launcher):
    m, devices = launcher.manager, launcher.devices
    workers = []
    for d in (devices[1], devices[2], devices[4]):
        w = worker(d)
        m.start_worker(w, workers)
        workers.append(w)
    assert len(launcher.launches) == 3
    with pytest.raises(ValueError):
        m.start_worker(worker(devices[3]), workers)
    listing = m.devices(workers, lambda w: w)
    assert listing["managed"] == 4 and len(listing["devices"]) == 5
    assert listing["devices"][3]["reason"]
    workers[0]["enabled"] = False
    assert m.devices(workers, lambda w: w)["managed"] == 4
    with pytest.raises(ValueError):
        m.start_worker(worker(devices[3]), workers)  # unticking never stops the owned process
    m.stop_worker(workers[0])
    m.start_worker(worker(devices[3]), workers)
    assert len(launcher.launches) == 4


def test_missing_uuid_is_listed_and_never_replaced_by_same_index(launcher):
    target = worker(launcher.devices[1])
    launcher.devices.pop(1)
    with pytest.raises(ValueError):
        launcher.manager.start_worker(target, [target])
    found = launcher.manager.devices([target], lambda w: w)
    missing = next(d for d in found["devices"] if d["id"] == target["address"])
    assert missing["index"] is None and missing["reason"]
    assert not launcher.launches


def test_display_gpu_requires_explicit_opt_in(launcher):
    launcher.devices[1]["display_active"] = True
    target = worker(launcher.devices[1])
    with pytest.raises(ValueError):
        launcher.manager.start_worker(target, [])
    launcher.manager.start_worker(target, [], allow_display=True)
    assert launcher.manager.owned(target)


@pytest.mark.parametrize("occupied", ["compute", "unattributed"])
def test_external_gpu_workload_is_not_stopped(launcher, occupied):
    if occupied == "compute":
        launcher.devices[1]["compute_pids"] = [12345]
    else:
        launcher.devices[1]["used_mb"] = 4000
    with pytest.raises(ValueError):
        launcher.manager.start_worker(worker(launcher.devices[1]), [])
    assert not launcher.killed and not launcher.launches


def test_duplicate_device_and_head_ownership(launcher, runtime):
    m, devices = launcher.manager, launcher.devices
    target = worker(devices[1])
    m.start_worker(target, [])
    other = local_gpus.Manager(runtime / "other-studio", runtime / "pack")
    with pytest.raises(ValueError):
        other.acquire_head(devices)
    # Select a different head, then prove the child UUID is still unavailable.
    other.head_uuid = devices[2]["gpu_uuid"]
    with pytest.raises(ValueError):
        other.start_worker(target, [])
    with pytest.raises(ValueError):
        other.stop_worker(target)
    assert m.owned(target) and not launcher.killed


def test_ports_home_locks_and_child_environment_are_isolated(launcher, monkeypatch):
    monkeypatch.setenv("SPARK_COMFY_URL", "http://unrelated.invalid:8190")
    monkeypatch.setenv("STUDIO_UPDATE_SESSION", "synthetic-session")
    targets = [worker(d) for d in launcher.devices[1:3]]
    for w in targets:
        launcher.manager.start_worker(w, targets)
    envs = [options["env"] for _, options, _ in launcher.launches]
    assert len({e[k] for e in envs for k in ("PORT", "SPARK_COMFY_PORT")}) == 4
    assert envs[0]["STUDIO_HOME"] != envs[1]["STUDIO_HOME"]
    assert envs[0]["STUDIO_GPU_LOCK"] != envs[1]["STUDIO_GPU_LOCK"]
    assert all(e["HOST"] == "127.0.0.1" and e["STUDIO_LOCAL_GPU_CHILD"] == "1" for e in envs)
    assert all("SPARK_COMFY_URL" not in e and "STUDIO_UPDATE_SESSION" not in e for e in envs)
    for argv, options, _ in launcher.launches:
        assert options["bound_address"] == ("127.0.0.1", int(options["env"]["PORT"]))
    record_path = launcher.manager.path(targets[0]["gpu_uuid"])
    assert record_path.stat().st_mode & 0o077 == 0
    assert local_gpus.runtime_dir().stat().st_mode & 0o077 == 0


def test_stop_only_signals_owned_child_and_pid_reuse_is_not_owned(launcher):
    targets = [worker(d) for d in launcher.devices[1:3]]
    for w in targets:
        launcher.manager.start_worker(w, targets)
    pid0, pid1 = [process.pid for _, _, process in launcher.launches]
    launcher.manager.stop_worker(targets[0])
    assert launcher.killed == [(pid0, signal.SIGTERM)]
    assert launcher.manager.owned(targets[1])
    launcher.states[pid1] = "reused-pid-start"
    assert not launcher.manager.owned(targets[1])
    launcher.manager.stop_worker(targets[1])
    assert launcher.killed == [(pid0, signal.SIGTERM)]


def test_orphan_owned_child_can_be_checked_by_restarted_head(launcher, runtime):
    target = worker(launcher.devices[1])
    launcher.manager.start_worker(target, [])
    record = launcher.manager.record(target["gpu_uuid"])
    record.update(pid=60000, start="dead-parent")
    local_gpus.save(launcher.manager.path(target["gpu_uuid"]), record)
    restarted = local_gpus.Manager(runtime / "studio", runtime / "pack")
    assert restarted.owned(target)
    base, key = restarted.credentials(target)
    assert base.startswith("http://127.0.0.1:") and key
    restarted.stop_worker(target)
    assert not restarted.owned(target)


def test_child_cannot_spawn_recursively(runtime, monkeypatch):
    monkeypatch.setenv("STUDIO_LOCAL_GPU_CHILD", "1")
    manager = local_gpus.Manager(runtime / "child", runtime / "pack")
    with pytest.raises(ValueError):
        manager.start_worker(worker(gpu(1)), [])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="private runtime ownership requires POSIX Linux")
def test_runtime_rejects_symlink(runtime):
    outside = runtime / "outside"
    outside.mkdir()
    link = runtime / "unsafe"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        local_gpus.private_dir(link)


@pytest.fixture
def ram(runtime, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Shared RAM admission requires Linux /proc process identities")
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 80000))
    return local_gpus.ram_dir() / "reservations.json"


def test_ram_contention_waits_and_releases(ram, monkeypatch):
    first, second = {"status": "running"}, {"status": "running"}
    with local_gpus.ram_reservation(40000, first) as admitted:
        assert admitted
        waited = []

        def release_room(seconds):
            waited.append(second["progress"]["stage"])
            monkeypatch.setattr(local_gpus, "memory", lambda: (150000, 140000))

        monkeypatch.setattr(local_gpus.time, "sleep", release_room)
        with local_gpus.ram_reservation(40000, second) as admitted_second:
            assert admitted_second and len(waited) == 1 and "progress" not in second
            assert len(local_gpus.load(ram)) == 2
    assert local_gpus.load(ram) == {}


def test_ram_stale_pid_start_is_cleaned(ram):
    local_gpus.save(ram, {"stale": {"pid": os.getpid(), "start": "wrong-start", "need_mb": 1000000}})
    with local_gpus.ram_reservation(50000, {"status": "running"}) as admitted:
        assert admitted and "stale" not in local_gpus.load(ram)
    assert local_gpus.load(ram) == {}


def test_ram_wait_is_cancellable_and_preserves_floor(ram, monkeypatch):
    job = {"status": "running"}
    with local_gpus.ram_reservation(1000, {"status": "running"}) as first:
        assert first
        monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 19000))
        monkeypatch.setattr(local_gpus.time, "sleep", lambda seconds: job.update(status="cancelled"))
        with local_gpus.ram_reservation(10000, job) as admitted:
            assert not admitted
    assert local_gpus.load(ram) == {}


def test_unmeasured_ram_reservation_is_exclusive(ram, monkeypatch):
    unknown, measured = {"status": "running"}, {"status": "running"}
    with local_gpus.ram_reservation(None, unknown) as admitted:
        assert admitted
        monkeypatch.setattr(local_gpus.time, "sleep", lambda seconds: measured.update(status="cancelled"))
        with local_gpus.ram_reservation(1, measured) as second:
            assert not second
        assert len(local_gpus.load(ram)) == 1
    with local_gpus.ram_reservation(1000, {"status": "running"}) as admitted:
        assert admitted
        unknown = {"status": "running"}
        monkeypatch.setattr(local_gpus.time, "sleep", lambda seconds: unknown.update(status="cancelled"))
        with local_gpus.ram_reservation(None, unknown) as second:
            assert not second
    assert local_gpus.load(ram) == {}


def test_ram_reservation_releases_after_error(ram):
    with pytest.raises(RuntimeError):
        with local_gpus.ram_reservation(1000, {"status": "running"}) as admitted:
            assert admitted
            raise RuntimeError("synthetic render failure")
    assert local_gpus.load(ram) == {}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="listener ownership requires Linux /proc")
def test_unrelated_comfy_listener_is_refused_without_signalling(runtime, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "synthetic-private-child-identity")
        monkeypatch.setenv("SPARK_COMFY_PORT", str(listener.getsockname()[1]))
        with pytest.raises(RuntimeError):
            local_gpus.check_child_comfy()
        # It was refused, not terminated or disconnected.
        assert listener.getsockname()[1] > 0


def test_ram_ledger_coordinates_distinct_processes(ram, monkeypatch):
    import multiprocessing

    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("Linux runtime uses flock and /proc process identities")
    context = multiprocessing.get_context("fork")
    ready, release = context.Event(), context.Event()

    def hold_reservation():
        with local_gpus.ram_reservation(40000, {"status": "running"}) as admitted:
            if admitted:
                ready.set()
                release.wait(timeout=10)

    child = context.Process(target=hold_reservation)
    child.start()
    try:
        assert ready.wait(timeout=5)
        job = {"status": "running"}
        monkeypatch.setattr(local_gpus.time, "sleep", lambda seconds: job.update(status="cancelled"))
        with local_gpus.ram_reservation(40000, job) as admitted:
            assert not admitted and job["progress"]
    finally:
        release.set()
        child.join(timeout=5)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
    assert child.exitcode == 0 and local_gpus.load(ram) == {}


def test_reserved_comfy_port_is_not_reused_by_another_child(launcher, monkeypatch):
    first, second = [worker(d) for d in launcher.devices[1:3]]
    launcher.manager.start_worker(first, [])
    reserved = launcher.manager.record(first["gpu_uuid"])["comfy_port"]
    original = socket.socket
    forced = [reserved]

    class BoundSocket:
        def __init__(self):
            self.sock = original()

        def bind(self, address):
            self.sock.bind((address[0], forced.pop()) if forced else address)

        def __getattr__(self, name):
            return getattr(self.sock, name)

    def allocate(*args, **kwargs):
        return original(*args, **kwargs) if args or kwargs else BoundSocket()

    monkeypatch.setattr(local_gpus.socket, "socket", allocate)
    launcher.manager.start_worker(second, [first])
    record = launcher.manager.record(second["gpu_uuid"])
    assert reserved not in (record["port"], record["comfy_port"])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="model process ownership requires Linux /proc")
def test_comfy_stop_uses_private_identity_not_matching_port(runtime, monkeypatch):
    proc = runtime / "synthetic-proc"
    owned_pid, external_pid = os.getpid() + 100001, os.getpid() + 100002
    for pid, identity in ((owned_pid, "synthetic-owner-key"), (external_pid, "different-owner-key")):
        entry = proc / str(pid)
        entry.mkdir(parents=True)
        (entry / "environ").write_bytes(("STUDIO_LOCAL_GPU_KEY=" + identity).encode() + b"\0")
        (entry / "cmdline").write_bytes(b"/home/you/python\0main.py\0--port\08190\0")
    original_path = Path

    def mapped_path(value):
        return proc if str(value) == "/proc" else original_path(value)

    states, killed = {owned_pid: "owned-start", external_pid: "external-start"}, []
    monkeypatch.setattr(local_gpus, "Path", mapped_path)
    monkeypatch.setattr(local_gpus, "process_identity", lambda pid: states.get(pid))

    def kill(pid, sig):
        killed.append((pid, sig))
        states.pop(pid, None)

    monkeypatch.setattr(local_gpus.os, "kill", kill)
    local_gpus.stop_child_comfy("synthetic-owner-key")
    assert killed == [(owned_pid, signal.SIGTERM)]
    assert states[external_pid] == "external-start"


def test_missing_head_uuid_is_not_replaced_by_another_ordinal(launcher):
    manager, devices = launcher.manager, launcher.devices
    manager.head(devices)
    devices.pop(0)
    devices[0]["index"] = 0
    with pytest.raises(ValueError):
        manager.acquire_head(devices)


def test_failed_private_lease_write_stops_only_new_owned_child(launcher, monkeypatch):
    target = worker(launcher.devices[1])
    original = local_gpus.save

    def fail_lease(path, value):
        if path == launcher.manager.path(target["gpu_uuid"]):
            raise OSError("synthetic private disk failure")
        return original(path, value)

    monkeypatch.setattr(local_gpus, "save", fail_lease)
    with pytest.raises(OSError):
        launcher.manager.start_worker(target, [])
    assert len(launcher.launches) == 1
    assert launcher.killed == [(launcher.launches[0][2].pid, signal.SIGTERM)]
    assert not launcher.manager.owned(target)


def test_child_cannot_serve_without_its_committed_process_identity(launcher, runtime, monkeypatch):
    target = worker(launcher.devices[1])
    launcher.manager.start_worker(target, [])
    record = launcher.manager.record(target["gpu_uuid"])
    monkeypatch.setenv("STUDIO_LOCAL_GPU_CHILD", "1")
    monkeypatch.setenv("STUDIO_LOCAL_GPU_UUID", target["gpu_uuid"])
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", record["key"])
    # Correct GPU and token do not authorize a different process or a reused PID.
    impostor = local_gpus.Manager(runtime / "child", runtime / "pack")
    with pytest.raises(OSError):
        impostor.register_child()
    assert launcher.manager.owned(target)
    monkeypatch.setattr(local_gpus.os, "getpid", lambda: record["child_pid"])
    child = local_gpus.Manager(runtime / "child", runtime / "pack")
    child.register_child()
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "synthetic-wrong-private-identity")
    with pytest.raises(OSError):
        child.register_child()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="head GPU leases require Linux")
@pytest.mark.parametrize("selection", ["uuid", "unset", "multiple-visible"])
def test_head_requires_supported_explicit_index_on_multi_gpu_host(runtime, monkeypatch, selection):
    devices = [gpu(0), gpu(1)]
    if selection == "uuid":
        monkeypatch.setenv("STUDIO_GPU", devices[0]["gpu_uuid"])
    else:
        monkeypatch.delenv("STUDIO_GPU", raising=False)
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        if selection == "multiple-visible":
            monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    manager = local_gpus.Manager(runtime / "studio", runtime / "pack")
    assert manager.head(devices) is None
    with pytest.raises(ValueError):
        manager.acquire_head(devices)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="managed device overview requires Linux")
@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 7])
def test_device_overview_lists_every_physical_card(runtime, monkeypatch, count):
    devices = [gpu(i) for i in range(count)]
    monkeypatch.setattr(local_gpus, "inventory", lambda **kw: {"supported": bool(count), "devices": devices})
    manager = local_gpus.Manager(runtime / "studio", runtime / "pack")
    overview = manager.devices([], lambda w: w)
    assert len(overview["devices"]) == count
    assert overview["managed"] == bool(count)
    assert sum(d["head"] for d in overview["devices"]) == bool(count)
    assert overview["limit"] == 4


@pytest.mark.parametrize("power, expected", [
    ("<gpu_power_readings><instant_power_draw>41.5 W</instant_power_draw><average_power_draw>40 W</average_power_draw></gpu_power_readings>", 41.5),
    ("<gpu_power_readings><instant_power_draw>N/A</instant_power_draw><average_power_draw>40 W</average_power_draw></gpu_power_readings>", 40.0),
    ("<gpu_power_readings><instant_power_draw>0 W</instant_power_draw><average_power_draw>40 W</average_power_draw></gpu_power_readings>", 0.0),
    ("<power_readings><power_draw>25.5 W</power_draw></power_readings>", 25.5),
    ("<gpu_power_readings><instant_power_draw>N/A</instant_power_draw></gpu_power_readings>", None),
])
def test_driver_power_layouts_preserve_available_readings_and_unknowns(power, expected):
    xml = xml_devices(1).replace("<gpu_power_readings><power_draw>25.5 W</power_draw></gpu_power_readings>", power)
    assert local_gpus.parse_inventory(xml)[0]["power_w"] == expected


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="head GPU leases require Linux")
@pytest.mark.parametrize("display_first", [True, False])
def test_the_only_compute_card_beside_a_display_is_the_head_with_no_studio_gpu(runtime, monkeypatch, display_first):
    """As spark/gpu.sh picks it. With a display card at either index and no STUDIO_GPU, the Studio stopped at its start."""

    monkeypatch.delenv("STUDIO_GPU", raising=False)
    display, compute = (gpu(0, total=8000, display=True), gpu(1)) if display_first else (gpu(1, total=8000, display=True), gpu(0))
    devices = [display, compute]
    manager = local_gpus.Manager(runtime / "studio", runtime / "pack")
    assert manager.head(devices) == compute["gpu_uuid"]
    manager.acquire_head(devices)
    assert manager.active


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="managed GPU startup requires Linux")
def test_two_compute_cards_and_no_studio_gpu_start_the_studio_without_local_workers(runtime, monkeypatch):
    import threading

    import sparks

    monkeypatch.delenv("STUDIO_GPU", raising=False)
    devices = [gpu(0), gpu(1)]
    monkeypatch.setattr(local_gpus, "inventory", lambda **kw: {"supported": True, "devices": devices})
    hub = sparks.Hub(runtime / "studio", runtime / "pack", True, {}, [], threading.RLock(), threading.Event(),
                     lambda: {}, lambda p: runtime / p, lambda j, w: None)
    monkeypatch.setattr(hub, "loop", lambda: None)
    monkeypatch.setattr(hub, "sweep", lambda: None)
    hub.start()  # raised ValueError out of the app's start
    assert not hub.local.active and "STUDIO_GPU" in hub.local.unavailable
    assert hub.local.devices([], lambda w: w)["unavailable"] == hub.local.unavailable


def test_head_lease_alone_does_not_enable_managed_execution(launcher, monkeypatch):
    manager = launcher.manager
    manager.acquire_head(launcher.devices)
    assert manager.active and not manager.managed
    assert os.environ["STUDIO_LOCAL_GPU_KEY"] == manager.key
    assert os.environ["CUDA_VISIBLE_DEVICES"] == launcher.devices[0]["gpu_uuid"]
    target = worker(launcher.devices[1])
    manager.start_worker(target, [])
    assert manager.managed
    target["enabled"] = False
    assert manager.managed
    manager.stop_worker(target)
    assert not manager.managed
    monkeypatch.setenv("STUDIO_LOCAL_GPU_CHILD", "1")
    assert local_gpus.Manager(manager.home, manager.pack).managed


@pytest.mark.parametrize("order", [None, "FASTEST_FIRST", "PCI_BUS_ID"])
def test_numeric_cuda_visibility_requires_matching_device_order(runtime, monkeypatch, order):
    monkeypatch.delenv("STUDIO_GPU", raising=False)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    if order is None:
        monkeypatch.delenv("CUDA_DEVICE_ORDER", raising=False)
    else:
        monkeypatch.setenv("CUDA_DEVICE_ORDER", order)
    manager = local_gpus.Manager(runtime / "studio", runtime / "pack")
    assert manager.head([gpu(0), gpu(1)]) == (gpu(1)["gpu_uuid"] if order == "PCI_BUS_ID" else None)


def test_inactive_configuration_and_shutdown_never_touch_runtime(runtime, monkeypatch):
    import threading
    import sparks

    monkeypatch.setattr(local_gpus, "runtime_dir", lambda: pytest.fail("inactive Studio touched GPU runtime"))
    hub = sparks.Hub(runtime / "studio", runtime / "pack", True, {}, [], threading.RLock(), threading.Event(),
                     lambda: {}, lambda p: runtime / p, lambda j, w: None)
    assert hub.config()["workers"] == []
    assert not hub.local.managed
    assert hub.close()


def test_active_reconciliation_cannot_raise_into_queue(launcher, monkeypatch):
    manager = launcher.manager
    manager.active = True
    rows = [worker(launcher.devices[1])]
    monkeypatch.setattr(local_gpus, "runtime_dir", lambda: (_ for _ in ()).throw(PermissionError("synthetic runtime")))
    assert manager.reconcile(rows) is rows


def test_reordered_live_worker_keeps_uuid_pin_but_requires_guard_restart(launcher):
    manager, device = launcher.manager, launcher.devices[1]
    target = worker(device)
    manager.start_worker(target, [])
    env = launcher.launches[0][1]["env"]
    device["index"] = 3
    assert env["CUDA_VISIBLE_DEVICES"] == target["gpu_uuid"]
    assert env["STUDIO_GPU"] == "1"
    assert "stop and start" in manager.guard_reason(target, device)
    manager.stop_worker(target)
    manager.start_worker(target, [])
    assert launcher.launches[-1][1]["env"]["STUDIO_GPU"] == "3"
    assert not manager.guard_reason(target, device)


def test_head_reorder_never_reassigns_its_numeric_guard(launcher):
    manager = launcher.manager
    manager.acquire_head(launcher.devices)
    launcher.devices[0]["index"] = 1
    launcher.devices[1]["index"] = 0
    with pytest.raises(ValueError, match="head GPU's index changed"):
        manager.validate_head(launcher.devices)
    with pytest.raises(ValueError, match="head GPU's index changed"):
        manager.start_worker(worker(launcher.devices[2]), [])
    assert not launcher.launches


def test_failed_head_process_drain_retains_device_lease(launcher):
    manager = launcher.manager
    manager.acquire_head(launcher.devices)
    manager.drain_failed = True
    assert not manager.close([])
    assert manager.path(manager.head_uuid).is_file()


def test_display_consent_does_not_authorize_unattributed_gpu_memory(launcher):
    launcher.devices[1].update(display_active=True, used_mb=4096)
    with pytest.raises(ValueError, match="unattributed GPU workload"):
        launcher.manager.start_worker(worker(launcher.devices[1]), [], allow_display=True)
    assert not launcher.launches and not launcher.killed


@pytest.mark.parametrize("supported", [False, True])
def test_startup_without_workers_keeps_legacy_path_and_never_touches_runtime(runtime, monkeypatch, supported):
    import threading
    import sparks

    devices = [gpu(0, total=24000 if supported else 0)]
    monkeypatch.setattr(local_gpus, "inventory", lambda **kw: {"supported": supported, "devices": devices})
    monkeypatch.setattr(local_gpus, "runtime_dir", lambda: pytest.fail("ordinary startup touched local GPU runtime"))
    hub = sparks.Hub(runtime / "studio", runtime / "pack", True, {}, [], threading.RLock(), threading.Event(),
                     lambda: {}, lambda p: runtime / p, lambda j, w: None)
    monkeypatch.setattr(hub, "loop", lambda: None)
    monkeypatch.setattr(hub, "sweep", lambda: None)
    hub.start()
    assert hub.started and not hub.local.active and not hub.local.managed
    assert hub.close()


@pytest.mark.parametrize("pid,expected,hidden", [("123", [123], False), ("N/A", [], True)])
def test_mixed_compute_process_types_and_hidden_process_identity(pid, expected, hidden):
    xml = xml_devices(1).replace("<pid>43210</pid><type>C</type>", f"<pid>{pid}</pid><type>M+C</type>")
    device = local_gpus.parse_inventory(xml)[0]
    assert device["compute_pids"] == expected
    assert device["hidden_compute"] is hidden


@pytest.mark.parametrize("index", [0, 1])
def test_hidden_compute_process_refuses_local_management_even_below_memory_fallback(launcher, index):
    launcher.devices[index].update(hidden_compute=True, used_mb=32)
    with pytest.raises(ValueError, match="hidden|unattributed"):
        launcher.manager.start_worker(worker(launcher.devices[1]), [])
    assert not launcher.launches and not launcher.killed
