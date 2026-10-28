"""Admission/cancellation races and detached-process ownership boundaries."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import local_gpus
from test_local_gpus import gpu, worker, launcher, runtime
from test_sparks_placement import hub, job


class ObservedLock:
    """A real RLock with a barrier for a competing transaction, not a time-based race."""
    def __init__(self):
        self.lock = threading.RLock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.contended.set()
            self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


def pause_start(hub, launcher, monkeypatch):
    hub.local = launcher.manager
    entered, release, checked = threading.Event(), threading.Event(), threading.Event()

    def inventory(*, fresh=False):
        if fresh:
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("startup barrier was not released")
        return {"supported": True, "devices": launcher.devices}

    def ready(worker):
        live = hub.state(worker["address"])
        live.update(state="ready", info=hub.info())
        checked.set()
        return live

    monkeypatch.setattr(local_gpus, "inventory", inventory)
    monkeypatch.setattr(hub, "check_local", ready)
    app = FastAPI()
    app.include_router(hub.router())
    return TestClient(app), entered, release, checked


def test_pending_start_cannot_remove_worker_before_lease_commit(hub, launcher, monkeypatch):
    client, entered, release, checked = pause_start(hub, launcher, monkeypatch)
    target = hub.workers()[1]
    assert client.post("/api/sparks/local", json={"gpu": target["address"], "action": "start"}).status_code == 200
    try:
        assert entered.wait(timeout=5)
        removed = client.delete("/api/sparks/workers/" + target["address"])
        assert removed.status_code == 400
        assert not launcher.launches
    finally:
        release.set()
    assert checked.wait(timeout=5)
    assert launcher.manager.owned(target)
    assert any(w["address"] == target["address"] for w in hub.workers())
    assert hub.close()
    assert not launcher.manager.owned(target)


def test_registration_and_settings_do_not_overwrite_each_other(hub, launcher, monkeypatch):
    client, entered, release, checked = pause_start(hub, launcher, monkeypatch)
    transaction = ObservedLock()
    launcher.manager.mutex = transaction
    target = launcher.devices[3]
    placement = {"worker": "head", "policy": "prefer"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        registering = pool.submit(client.post, "/api/sparks/local", json={"gpu": target["id"], "action": "start"})
        try:
            assert entered.wait(timeout=5)
            setting = pool.submit(client.post, "/api/sparks", json={"placement": placement})
            assert transaction.contended.wait(timeout=5)
        finally:
            release.set()
        assert registering.result(timeout=5).status_code == 200
        assert setting.result(timeout=5).status_code == 200
    assert checked.wait(timeout=5)
    config = hub.config()
    assert config["placement"] == placement
    assert any(w["address"] == target["id"] for w in config["workers"])
    assert launcher.manager.owned(worker(target))
    assert hub.close()


def test_lost_rows_are_reconciled_counted_and_drained(launcher):
    manager, devices = launcher.manager, launcher.devices
    targets = [worker(d) for d in devices[1:4]]
    for target in targets:
        manager.start_worker(target, [])  # ownership, not caller-supplied rows, accounts for earlier children
    recovered = manager.reconcile([])
    assert {w["address"] for w in recovered} == {w["address"] for w in targets}
    assert all(not w["enabled"] for w in recovered)
    assert manager.devices([], lambda w: w)["managed"] == 4
    with pytest.raises(ValueError):
        manager.start_worker(worker(devices[4]), [])
    assert manager.close([])
    assert not manager.owned_records() and not manager.children
    assert all(not manager.path(target["gpu_uuid"]).exists() for target in targets)


def test_retained_orphan_descendants_keep_lease_occupied_and_consuming_slot(launcher, monkeypatch):
    manager, devices = launcher.manager, launcher.devices
    target = worker(devices[1])
    manager.start_worker(target, [])
    record = manager.record(target["gpu_uuid"])
    launcher.states.pop(record["child_pid"])
    record.update(pid=62001, start="synthetic-dead-head")
    local_gpus.save(manager.path(target["gpu_uuid"]), record)
    orphans = {61001: "synthetic-orphan-start"}
    monkeypatch.setattr(local_gpus, "private_processes", lambda key: dict(orphans) if key == record["key"] else {})
    monkeypatch.setattr(local_gpus, "stop_child_processes", lambda key: not orphans)
    with pytest.raises(OSError):
        manager.stop_worker(target)
    assert manager.path(target["gpu_uuid"]).exists()
    assert manager.owned(target)
    assert target["gpu_uuid"] in manager.slot_ids([])
    assert not manager.close([])
    # A different Studio cannot reclaim a dead app's GPU while its tagged runner remains.
    other = local_gpus.Manager(manager.home.parent / "different-studio", manager.pack)
    assert other.conflict(target["gpu_uuid"])
    orphans.clear()
    assert manager.close([])
    assert not manager.path(target["gpu_uuid"]).exists()


def test_cancellation_while_blocked_on_ram_flock_cannot_grant_admission(runtime, monkeypatch):
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 80000))
    real_locked = local_gpus.locked
    entered = threading.Event()
    job = {"status": "running"}
    result = []

    @contextlib.contextmanager
    def observed(path):
        entered.set()
        with real_locked(path):
            yield

    monkeypatch.setattr(local_gpus, "locked", observed)

    def reserve():
        with local_gpus.ram_reservation(10000, job) as admitted:
            result.append(admitted)

    path = local_gpus.ram_dir() / "reservations.lock"
    with real_locked(path):
        thread = threading.Thread(target=reserve)
        thread.start()
        assert entered.wait(timeout=5)
        job["status"] = "cancelled"
    thread.join(timeout=5)
    assert not thread.is_alive() and result == [False]
    assert local_gpus.load(path.with_name("reservations.json")) == {}


def test_quiesce_prevents_claims_and_new_local_processes(hub, launcher):
    hub.local = launcher.manager
    clip = job(hub, count=3)
    hub.quiesce()
    assert hub.claim() is None and clip["status"] == "queued"
    with pytest.raises(ValueError):
        launcher.manager.start_worker(worker(launcher.devices[1]), [])
    assert not launcher.launches


@contextlib.contextmanager
def runner_family(*, orphan=False, child_marker=False, filter_marker=False):
    """Real CPU-only detached processes, bounded readiness and cleanup, no GPU loads."""
    if not sys.platform.startswith("linux"):
        pytest.skip("process identities and ancestry use Linux /proc")
    key = uuid.uuid4().hex
    marker = "STUDIO_LOCAL_GPU_KEY" if child_marker else "STUDIO_GPU_JOB_KEY"
    grandchild = "import time; time.sleep(300)"
    child = ("import subprocess,sys,json,os,time; "
             f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}],start_new_session=True); "
             "print(json.dumps({'grandchild':p.pid}),flush=True); time.sleep(300)")
    child_env = "{k:v for k,v in os.environ.items() if k != 'STUDIO_GPU_JOB_KEY'}" if filter_marker else "None"
    root = ("import subprocess,sys,json,os,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True,env={child_env}); "
            "print(json.dumps({'child':p.pid}),flush=True); " + ("" if orphan else "time.sleep(300)"))
    process = subprocess.Popen([sys.executable, "-c", root], env={**os.environ, marker: key},
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    start = local_gpus.process_identity(process.pid)
    owned = {}
    try:
        content = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while content.count(b"\n") < 2:
                assert selector.select(timeout=5)
                chunk = os.read(process.stdout.fileno(), 65536)
                assert chunk
                content += chunk
        identities = {}
        for line in content.splitlines():
            identities.update(json.loads(line))
        owned = {pid: identity for pid in identities.values() if (identity := local_gpus.process_identity(pid))}
        if orphan:
            process.wait(timeout=5)
        yield process, start, key, identities
    finally:
        if child_marker:
            local_gpus.stop_child_processes(key)
        else:
            local_gpus.stop_job_tree(process.pid, start, key=key)
        local_gpus.drain_processes(owned)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def test_job_cancellation_drains_detached_descendants_without_touching_other_jobs():
    with runner_family() as (root, start, key, family), runner_family() as (other, other_start, other_key, other_family):
        assert local_gpus.stop_job_tree(root.pid, start, key=key)
        root.wait(timeout=5)
        assert all(local_gpus.process_identity(pid) is None for pid in family.values())
        assert other.poll() is None
        assert all(local_gpus.process_identity(pid) is not None for pid in other_family.values())


def test_job_marker_finds_detached_descendants_after_root_exit():
    with runner_family(orphan=True) as (root, start, key, family):
        assert root.poll() == 0
        assert local_gpus.stop_job_tree(root.pid, start, key=key)
        assert all(local_gpus.process_identity(pid) is None for pid in family.values())


def test_forced_child_cleanup_drains_non_comfy_detached_jobs():
    with runner_family(orphan=True, child_marker=True) as (root, start, key, family):
        assert local_gpus.stop_child_processes(key)
        assert all(local_gpus.process_identity(pid) is None for pid in family.values())


def test_runner_pid_reuse_never_targets_replacement_process(monkeypatch):
    signalled = []
    monkeypatch.setattr(local_gpus, "process_identity", lambda pid: "replacement-start")
    monkeypatch.setattr(local_gpus.os, "kill", lambda pid, sig: signalled.append(pid))
    assert not local_gpus.stop_job_tree(os.getpid() + 100000, "original-start")
    assert not signalled


def test_live_runner_ancestry_covers_descendants_that_filter_the_job_marker():
    with runner_family(filter_marker=True) as (root, start, key, family):
        assert local_gpus.stop_job_tree(root.pid, start, key=key)
        assert all(local_gpus.process_identity(pid) is None for pid in family.values())


def test_quiescing_during_inventory_prevents_a_pending_worker_launch(hub, launcher, monkeypatch):
    client, entered, release, checked = pause_start(hub, launcher, monkeypatch)
    finished = threading.Event()
    original = launcher.manager.start_worker

    def start(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(launcher.manager, "start_worker", start)
    target = hub.workers()[1]
    assert client.post("/api/sparks/local", json={"gpu": target["address"], "action": "start"}).status_code == 200
    try:
        assert entered.wait(timeout=5)
        hub.quiesce()
    finally:
        release.set()
    assert finished.wait(timeout=5)
    assert not launcher.launches
    assert hub.close()
