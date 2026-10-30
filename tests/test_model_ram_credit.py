"""Shared RAM admission uses owned matching residency, bounded waits and FIFO exclusives."""
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import local_gpus

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux GPU-session RAM accounting")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.setenv("STUDIO_GPU_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    monkeypatch.delenv("STUDIO_LOCAL_GPU_KEY", raising=False)
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 80000))
    monkeypatch.setattr(local_gpus, "_model_residents", lambda key=None: [])
    return local_gpus.ram_dir() / "reservations.json"


def test_default_leases_and_ledger_do_not_follow_session_runtime_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_GPU_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("STUDIO_GPU_RAM_DIR", raising=False)
    monkeypatch.setattr(local_gpus.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "desktop-session"))
    desktop = local_gpus.runtime_dir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "service-session"))
    assert local_gpus.runtime_dir() == desktop == tmp_path / ".cache/studio-gpus"
    assert local_gpus.ram_dir() == desktop / "ram"
    assert not (tmp_path / "desktop-session").exists()
    assert not (tmp_path / "service-session").exists()


def test_explicit_ledger_directory_does_not_touch_the_default_lease_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "explicit-ram"))
    monkeypatch.setattr(local_gpus, "runtime_dir", lambda: pytest.fail("explicit RAM directory touched lease state"))
    assert local_gpus.ram_dir() == tmp_path / "explicit-ram"


def wait_until(predicate):
    until = time.monotonic() + 3
    while not predicate() and time.monotonic() < until:
        time.sleep(.01)
    assert predicate(), "RAM admission did not reach the expected state"


def assert_empty(ledger):
    assert local_gpus.load(ledger) == {}
    assert local_gpus.load(ledger.with_name("waiters.json")) == {}


def test_warm_peak_does_not_book_its_already_allocated_pages_twice(ledger, monkeypatch):
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 20000))
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "first-session")
    residents = {"first-session": [{"route": "video", "mb": 60000}]}
    monkeypatch.setattr(local_gpus, "_model_residents", lambda key=None: residents.get(key, []))
    with local_gpus.ram_reservation(65000, {"status": "running"}, route="video") as first:
        assert first
        monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "second-session")
        with local_gpus.ram_reservation(5000, {"status": "running"}, route="video") as second:
            assert second  # 20 GB physical room - 5 GB outstanding - 5 GB new >= 10 GB floor
            assert len(local_gpus.load(ledger)) == 2
    assert_empty(ledger)


def test_newly_allocated_pages_reduce_only_their_own_outstanding_growth(ledger, monkeypatch):
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "first-session")
    residents = {}
    monkeypatch.setattr(local_gpus, "_model_residents", lambda key=None: residents.get(key, []))
    with local_gpus.ram_reservation(40000, {"status": "running"}, route="video") as first:
        assert first
        residents["first-session"] = [{"route": "video", "mb": 30000}]
        monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 50000))
        monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "second-session")
        with local_gpus.ram_reservation(20000, {"status": "running"}, route="video") as second:
            assert second
    assert_empty(ledger)


def test_concurrent_callers_cannot_spend_one_sessions_residency_twice(ledger, monkeypatch):
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "same-session")
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 20000))
    monkeypatch.setattr(local_gpus, "_model_residents", lambda key=None: [{"route": "video", "mb": 60000}])
    job = {"status": "running"}
    with local_gpus.ram_reservation(65000, {"status": "running"}, route="video") as first:
        assert first
        monkeypatch.setattr(local_gpus.time, "sleep", lambda _: job.update(status="cancelled"))
        with local_gpus.ram_reservation(10000, job, route="video") as second:
            assert not second
    assert_empty(ledger)


@pytest.mark.parametrize("route", [None, "music", "video+h3base"])
def test_another_resident_route_never_credits_the_next_job(ledger, monkeypatch, route):
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "same-session")
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 20000))
    monkeypatch.setattr(local_gpus, "_model_residents", lambda key=None: [{"route": "video", "mb": 60000,
                                                                       "pid": 123, "gpu": "2"}])
    with pytest.raises(RuntimeError, match="Not enough shared host RAM"):
        with local_gpus.ram_reservation(65000, {"status": "running"}, route=route):
            pytest.fail("a mismatched resident model was admitted")
    assert_empty(ledger)


def test_residency_never_credits_away_the_physical_ram_floor(ledger, monkeypatch):
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", "same-session")
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 9000))
    monkeypatch.setattr(local_gpus, "_model_residents", lambda key=None: [{"route": "video", "mb": 65000,
                                                                           "pid": 123, "gpu": "2"}])
    with pytest.raises(RuntimeError, match="10000 MiB must stay free.*GPU 2"):
        with local_gpus.ram_reservation(65000, {"status": "running"}, route="video"):
            pytest.fail("resident credit erased the floor")
    assert_empty(ledger)


def test_configured_guard_floor_is_honored(ledger, monkeypatch):
    monkeypatch.setenv("SPARK_MEMORY_FLOOR_MB", "24000")
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 30000))
    with pytest.raises(RuntimeError, match="24000 MiB must stay free"):
        with local_gpus.ram_reservation(10000, {"status": "running"}):
            pytest.fail("admission lowered the configured guard floor")
    assert_empty(ledger)


@pytest.mark.parametrize("need", [1000000, None])
def test_no_live_reservation_means_admit_or_fail_without_sleep(ledger, monkeypatch, need):
    if need is None:
        monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 9000))
    monkeypatch.setattr(local_gpus.time, "sleep", lambda _: pytest.fail("impossible admission waited"))
    with pytest.raises(RuntimeError, match="Not enough shared host RAM"):
        with local_gpus.ram_reservation(need, {"status": "running"}):
            pytest.fail("an impossible job was admitted")
    assert_empty(ledger)


def test_wait_deadline_cannot_leave_an_exclusive_barrier(ledger, monkeypatch):
    with local_gpus.ram_reservation(1000, {"status": "running"}) as admitted:
        assert admitted
        monkeypatch.setattr(local_gpus, "RAM_WAIT_SECONDS", 0)
        with pytest.raises(RuntimeError, match="Timed out waiting for shared host RAM"):
            with local_gpus.ram_reservation(None, {"status": "running"}):
                pytest.fail("exclusive work bypassed an active reservation")
        assert local_gpus.load(ledger.with_name("waiters.json")) == {}
    assert_empty(ledger)


def test_idle_residency_after_last_holder_finishes_fails_instead_of_waiting_forever(ledger, monkeypatch):
    job, errors = {"status": "running"}, []

    def reserve():
        try:
            with local_gpus.ram_reservation(40000, job):
                pytest.fail("the idle holder did not leave enough physical RAM")
        except RuntimeError as exc:
            errors.append(str(exc))

    with local_gpus.ram_reservation(40000, {"status": "running"}) as first:
        assert first
        monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 30000))
        thread = threading.Thread(target=reserve, daemon=True)
        thread.start()
        wait_until(lambda: bool(job.get("progress")))
    thread.join(timeout=3)
    assert not thread.is_alive() and errors and "Not enough shared host RAM" in errors[0]
    assert_empty(ledger)


def test_exclusive_waiter_is_fifo_and_blocks_later_measured_work(ledger):
    exclusive, later = {"status": "running"}, {"status": "running"}
    exclusive_in, measured_in, release = threading.Event(), threading.Event(), threading.Event()
    order, errors = [], []

    def reserve(need, job, entered):
        try:
            with local_gpus.ram_reservation(need, job) as admitted:
                assert admitted and "progress" not in job
                order.append(need)
                entered.set()
                if need is None:
                    assert release.wait(5)
        except Exception as exc:
            errors.append(exc)

    with local_gpus.ram_reservation(40000, {"status": "running"}) as first:
        assert first
        one = threading.Thread(target=reserve, args=(None, exclusive, exclusive_in), daemon=True)
        one.start()
        wait_until(lambda: bool(exclusive.get("progress")))
        two = threading.Thread(target=reserve, args=(1000, later, measured_in), daemon=True)
        two.start()
        wait_until(lambda: bool(later.get("progress")))
        assert not measured_in.is_set(), "a new measured job starved an exclusive waiter"
        for path in (ledger, ledger.with_name("waiters.json")):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    try:
        assert exclusive_in.wait(3)
        assert not measured_in.is_set()
    finally:
        release.set()
        one.join(timeout=3)
        two.join(timeout=3)
    assert not one.is_alive() and not two.is_alive() and not errors
    assert order == [None, 1000]
    assert_empty(ledger)


def test_exclusive_waiters_keep_arrival_order(ledger):
    order, errors, threads = [], [], []

    def reserve(index, job):
        try:
            with local_gpus.ram_reservation(None, job) as admitted:
                assert admitted
                order.append(index)
        except Exception as exc:
            errors.append(exc)

    with local_gpus.ram_reservation(1000, {"status": "running"}) as first:
        assert first
        for index in range(3):
            job = {"status": "running"}
            thread = threading.Thread(target=reserve, args=(index, job), daemon=True)
            threads.append(thread)
            thread.start()
            wait_until(lambda: bool(job.get("progress")))
    for thread in threads:
        thread.join(timeout=3)
    assert not any(thread.is_alive() for thread in threads) and not errors
    assert order == [0, 1, 2]
    assert_empty(ledger)


def test_cancelled_and_stale_exclusive_waiters_do_not_block_new_jobs(ledger):
    stale = {"pid": os.getpid(), "start": "old-pid-start", "need_mb": None}
    local_gpus.save(ledger.with_name("waiters.json"), {"stale": stale})
    job = {"status": "running"}
    result = []

    def reserve():
        with local_gpus.ram_reservation(None, job) as admitted:
            result.append(admitted)

    with local_gpus.ram_reservation(1000, {"status": "running"}) as first:
        assert first
        thread = threading.Thread(target=reserve, daemon=True)
        thread.start()
        wait_until(lambda: bool(job.get("progress")))
        job["status"] = "cancelled"
        thread.join(timeout=3)
        assert not thread.is_alive() and result == [False]
        with local_gpus.ram_reservation(1000, {"status": "running"}) as next_job:
            assert next_job
    assert_empty(ledger)


def test_impossible_peak_fails_even_while_another_reservation_is_live(ledger, monkeypatch):
    with local_gpus.ram_reservation(1000, {"status": "running"}) as first:
        assert first
        monkeypatch.setattr(local_gpus.time, "sleep", lambda _: pytest.fail("an impossible peak waited"))
        with pytest.raises(RuntimeError, match="Not enough shared host RAM"):
            with local_gpus.ram_reservation(1000000, {"status": "running"}):
                pytest.fail("a peak larger than physical RAM was admitted")
    assert_empty(ledger)


def test_missing_process_identity_fails_without_waiting(ledger, monkeypatch):
    monkeypatch.setattr(local_gpus, "process_identity", lambda pid: None)
    with pytest.raises(RuntimeError, match="live Linux process identity"):
        with local_gpus.ram_reservation(1000, {"status": "running"}):
            pytest.fail("admitted a process without a start identity")


def test_only_matching_owned_comfy_anonymous_pages_are_credited(tmp_path, monkeypatch):
    key = "synthetic-private-model-session"
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", key)
    backing = tmp_path / "file-cache.bin"
    backing.write_bytes(b"x" * (64 * 1024 * 1024))
    script = """import mmap, sys, time
anon = bytearray(int(sys.argv[-1]) * 1024 * 1024)
with open(sys.argv[-2], 'rb') as stream:
    cached = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    touched = sum(cached[::4096])
    print('ready', flush=True)
    time.sleep(15)
"""
    children = []
    try:
        cases = ((key, True, "video", 24, "main.py"),
                 (key, False, "video", 48, "main.py"),
                 ("another-private-session", True, "video", 48, "main.py"),
                 (key, True, "music", 48, "main.py"),
                 (key, True, "video", 48, "unrelated.py"))
        for index, (child_key, marked, route, size, name) in enumerate(cases):
            prefix = tmp_path / f"server-{index}"
            comfy = prefix / "ComfyUI"
            comfy.mkdir(parents=True)
            (comfy / name).write_text(script)
            (prefix / "comfy.kind").write_text(route)
            env = {**os.environ, "STUDIO_LOCAL_GPU_KEY": child_key, "PREFIX": str(prefix), "SPARK_COMFY_PORT": "8190"}
            env.pop("STUDIO_GPU_MODEL_RAM", None)
            if marked:
                env["STUDIO_GPU_MODEL_RAM"] = "1"
            child = subprocess.Popen([sys.executable, name, "--listen", "127.0.0.1", "--port", "8190", str(backing), str(size)],
                                     cwd=comfy, env=env, stdout=subprocess.PIPE, text=True)
            children.append(child)
            assert select.select([child.stdout], [], [], 5)[0], "model fixture did not allocate its RAM"
            assert child.stdout.readline().strip() == "ready"
        credited = local_gpus.model_ram_mb("video")
        assert 24 <= credited < 45, f"credited {credited} MiB; cache, another route, process or GPU leaked into credit"
        assert local_gpus.model_ram_mb() == 0
        starts = local_gpus.private_processes(key)
        with monkeypatch.context() as reused:
            reused.setattr(local_gpus, "private_processes", lambda _: {pid: "stale-start" for pid in starts})
            assert local_gpus.model_ram_mb("video") == 0
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()


def test_matching_owned_native_server_allows_repeated_warm_jobs(tmp_path, monkeypatch):
    key, route = "synthetic-private-native-session", "native:voice:int8"
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", key)
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 10001))
    script = """import sys, time
anon = bytearray(int(sys.argv[1]) * 1024 * 1024)
print('ready', flush=True)
time.sleep(15)
"""
    children = []
    try:
        cases = ((key, True, route, 24), (key, True, None, 48),
                 (key, True, "native:music:int8", 48),
                 (key, False, route, 48), ("another-private-session", True, route, 48))
        for child_key, marked, child_route, size in cases:
            env = {**os.environ, "STUDIO_LOCAL_GPU_KEY": child_key}
            env.pop("STUDIO_GPU_MODEL_RAM", None)
            env.pop("STUDIO_GPU_MODEL_ROUTE", None)
            if marked:
                env["STUDIO_GPU_MODEL_RAM"] = "1"
            if child_route:
                env["STUDIO_GPU_MODEL_ROUTE"] = child_route
            child = subprocess.Popen([sys.executable, "-c", script, str(size)], env=env,
                                     stdout=subprocess.PIPE, text=True)
            children.append(child)
            assert select.select([child.stdout], [], [], 5)[0], "native fixture did not allocate its RAM"
            assert child.stdout.readline().strip() == "ready"
        credited = local_gpus.model_ram_mb(route)
        assert 24 <= credited < 45, f"credited {credited} MiB; a marker, route or GPU mismatch leaked into credit"
        for _ in range(2):
            with local_gpus.ram_reservation(24, {"status": "running"}, route=route) as admitted:
                assert admitted, "the same resident native model should not be booked again"
        with pytest.raises(RuntimeError, match="Not enough shared host RAM"):
            with local_gpus.ram_reservation(24, {"status": "running"}, route="native:voice:bf16"):
                pytest.fail("native credit crossed weights routes")
        assert_empty(local_gpus.ram_dir() / "reservations.json")
        starts = local_gpus.private_processes(key)
        with monkeypatch.context() as reused:
            reused.setattr(local_gpus, "private_processes", lambda _: {pid: "stale-start" for pid in starts})
            assert local_gpus.model_ram_mb(route) == 0
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()
