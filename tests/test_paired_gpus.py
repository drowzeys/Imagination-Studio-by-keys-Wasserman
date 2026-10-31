"""STUDIO_GPUS: one Studio renders on paired cards (spark/gpu.sh, spark/gpus.py), so 4.3's local GPU workers stay off
while cards are paired; the shell pin and rank 1's card follow the same list."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import local_gpus  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("STUDIO_GPUS", "STUDIO_GPU", "STUDIO_GPU_SPLIT", "STUDIO_LOCAL_GPU_UUID"):
        monkeypatch.delenv(name, raising=False)


def test_paired_indexes(monkeypatch):
    assert local_gpus.paired_indexes() == set()
    monkeypatch.setenv("STUDIO_GPUS", "0,1")
    assert local_gpus.paired_indexes() == {0, 1}
    monkeypatch.setenv("STUDIO_GPU", "0")
    assert local_gpus.paired_indexes() == {0, 1}
    monkeypatch.setenv("STUDIO_GPU", "1")  # the head's card must be the first
    assert local_gpus.paired_indexes() == set()
    monkeypatch.setenv("STUDIO_GPU", "0")
    monkeypatch.setenv("STUDIO_GPU_SPLIT", "off")
    assert local_gpus.paired_indexes() == set()
    monkeypatch.delenv("STUDIO_GPU_SPLIT")
    monkeypatch.setenv("STUDIO_LOCAL_GPU_UUID", "GPU-00000000-0000-0000-0000-000000000001")
    assert local_gpus.paired_indexes() == set()


def test_no_local_worker_starts_while_cards_are_paired(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_GPUS", "0,1")
    monkeypatch.setenv("STUDIO_GPU", "0")
    cls = next(c for c in vars(local_gpus).values() if isinstance(c, type) and hasattr(c, "start_worker"))
    pool = object.__new__(cls)
    pool.child = False
    with pytest.raises(ValueError, match="STUDIO_GPUS"):
        pool.start_worker({"gpu_uuid": "GPU-bbbbbbbb-0000-0000-0000-000000000000", "index": 1}, [])


def pin(env):
    """spark/gpu.sh's studio_pin_gpu in a clean shell: what it exports."""
    script = f'. "{ROOT}/spark/gpu.sh"; studio_pin_gpu; echo "$?|${{STUDIO_GPU:-}}|${{CUDA_VISIBLE_DEVICES:-}}"'
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}
    out = subprocess.run(["bash", "-c", script], env={**keep, **env}, capture_output=True, text=True, timeout=30)
    return out.stdout.strip().splitlines()[-1]


def test_the_shell_pins_paired_cards_the_heads_first():
    assert pin({"STUDIO_GPU": "0"}) == "0|0|0"
    assert pin({"STUDIO_GPU": "0", "STUDIO_GPUS": "0,1"}) == "0|0|0,1"
    assert pin({"STUDIO_GPUS": "0,1"}) == "0|0|0,1"  # the head's card from the list
    assert pin({"STUDIO_GPU": "0", "STUDIO_GPUS": "0,1", "STUDIO_GPU_SPLIT": "off"}) == "0|0|0"
    assert pin({"STUDIO_GPU": "1", "STUDIO_GPUS": "0,1"}) == "0|1|1"  # a mismatch: one card, the head's
    assert pin({"STUDIO_GPU": "0", "STUDIO_GPUS": "zero,1"}) == "0|0|0"
