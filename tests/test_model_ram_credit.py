"""Warm GPU sessions credit only physical RAM already held by their owned model servers."""
import os
from pathlib import Path
import select
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import local_gpus

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux GPU-session RAM accounting")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.setenv("STUDIO_GPU_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 20000))
    return local_gpus.ram_dir() / "reservations.json"


def test_warm_admission_preserves_the_full_peak_for_another_gpu(ledger, monkeypatch):
    monkeypatch.setattr(local_gpus, "model_ram_mb", lambda: 60000)
    first, second = {"status": "running"}, {"status": "running"}
    entered, trying = threading.Event(), threading.Event()

    def other_gpu():
        trying.set()
        with local_gpus.ram_reservation(5000, second) as admitted:
            if admitted:
                entered.set()

    with local_gpus.ram_reservation(65000, first) as admitted:
        assert admitted  # only 5 GB growth is needed; the 10 GB physical floor still fits
        monkeypatch.setattr(local_gpus, "model_ram_mb", lambda: 0)
        thread = threading.Thread(target=other_gpu, daemon=True)
        thread.start()
        assert trying.wait(2)
        assert not entered.wait(.3), "another GPU spent the credited model's full peak reservation"
    thread.join(timeout=3)
    assert entered.is_set() and not thread.is_alive()
    assert local_gpus.load(ledger) == {}


def test_residency_never_credits_away_the_physical_ram_floor(ledger, monkeypatch):
    job = {"status": "running"}
    monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 9000))
    monkeypatch.setattr(local_gpus, "model_ram_mb", lambda: 65000)
    monkeypatch.setattr(local_gpus.time, "sleep", lambda _: job.update(status="cancelled"))
    with local_gpus.ram_reservation(65000, job) as admitted:
        assert not admitted
    assert local_gpus.load(ledger) == {}


def test_only_this_sessions_model_anonymous_pages_are_credited(tmp_path, monkeypatch):
    key = "test-private-model-session"
    monkeypatch.setenv("STUDIO_LOCAL_GPU_KEY", key)
    backing = tmp_path / "file-cache.bin"
    with backing.open("wb") as stream:
        block = b"x" * (1024 * 1024)
        for _ in range(64):
            stream.write(block)
    code = """import mmap, sys, time
anon = bytearray(int(sys.argv[2]) * 1024 * 1024)
with open(sys.argv[1], 'rb') as stream:
    cached = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    touched = sum(cached[::4096])
    print('ready', flush=True)
    time.sleep(15)
"""
    children = []
    try:
        for child_key, role, size in ((key, True, 24), (key, False, 48), ("another-private-session", True, 48)):
            env = {**os.environ, "STUDIO_LOCAL_GPU_KEY": child_key}
            env.pop("STUDIO_GPU_MODEL_RAM", None)
            if role:
                env["STUDIO_GPU_MODEL_RAM"] = "1"
            child = subprocess.Popen([sys.executable, "-c", code, str(backing), str(size)], env=env,
                                     stdout=subprocess.PIPE, text=True)
            children.append(child)
            assert select.select([child.stdout], [], [], 5)[0], "model fixture did not allocate its RAM"
            assert child.stdout.readline().strip() == "ready"
        credited = local_gpus.model_ram_mb()
        assert 24 <= credited < 45, f"credited {credited} MiB; file cache, non-model work, or another GPU leaked into the credit"
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)
