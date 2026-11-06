"""The RTX Spark N1X (spark/memory_guard.py n1x, guard_mode, _relaxable): its GPU carve-out is the pool, WSL's small
RAM does not hold a job back, and a refusal is a flagged warning there while a DGX Spark keeps refusing."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "spark"))
import memory_guard  # noqa: E402

N1X_CARD = {"total_mb": 98304, "used_mb": 60183, "free_mb": 38121, "simulated": False}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(memory_guard, "_DEVICE", {"at": 0.0, "value": None})
    monkeypatch.setattr(memory_guard, "_N1X", {})
    for name in ("STUDIO_N1X", "STUDIO_MEMORY_GUARD", "STUDIO_VRAM_GB", "STUDIO_ROOM_MB", "STUDIO_GPU"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(memory_guard, "state_file", lambda: "/nonexistent/studio-comfy.mem")


def test_an_n1x_is_named_by_its_variable_or_its_gpu(monkeypatch):
    monkeypatch.setattr(memory_guard, "smi", lambda *q: [["NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)"]])
    assert memory_guard.n1x() and memory_guard.guard_mode() == "warn"
    monkeypatch.setenv("STUDIO_N1X", "0")
    assert not memory_guard.n1x() and memory_guard.guard_mode() == "strict"
    monkeypatch.setattr(memory_guard, "_N1X", {})
    monkeypatch.delenv("STUDIO_N1X")
    monkeypatch.setattr(memory_guard, "smi", lambda *q: [["NVIDIA GB10"]])
    assert not memory_guard.n1x() and memory_guard.guard_mode() == "strict"  # a DGX Spark keeps refusing
    monkeypatch.setenv("STUDIO_MEMORY_GUARD", "warn")
    assert memory_guard.guard_mode() == "warn"


def test_the_whole_card_is_the_studios_when_wsl_names_no_process(monkeypatch):
    monkeypatch.setenv("STUDIO_N1X", "1")
    monkeypatch.setattr(memory_guard, "device", lambda fresh=False: dict(N1X_CARD))
    monkeypatch.setattr(memory_guard, "processes_mb", lambda: {})  # WSL: no per-process memory
    monkeypatch.setattr(memory_guard, "server_pid", lambda: 4321)
    # its own ComfyUI holds 60 GB of loaded models; they are the job's, not another program's
    assert memory_guard.device_room_mb() == 98304 - memory_guard.VRAM_MARGIN_MB
    monkeypatch.setenv("STUDIO_N1X", "0")
    assert memory_guard.device_room_mb() == 38121 - memory_guard.VRAM_MARGIN_MB  # elsewhere: free memory, as before


def test_wsls_small_ram_does_not_hold_a_job_on_an_n1x(monkeypatch):
    monkeypatch.setattr(memory_guard, "ram_room_mb", lambda: 25_000)
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 30_000_000 // 1024, "MemAvailable": 25_000})
    monkeypatch.setenv("STUDIO_N1X", "1")
    assert memory_guard.card_pool(90_000) == (90_000 + memory_guard.VRAM_MARGIN_MB, memory_guard.VRAM_MARGIN_MB, True)
    monkeypatch.setenv("STUDIO_N1X", "0")
    assert memory_guard.card_pool(90_000)[2] is False  # a workstation card: held to the RAM, as before


def test_a_refusal_is_a_flagged_warning_in_warn_mode_and_a_refusal_in_strict(monkeypatch):
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 128_000_000 // 1024, "MemAvailable": 1})
    monkeypatch.setenv("STUDIO_N1X", "0")
    strict = memory_guard.check(1376, 768, 362, available_mb=20_000)
    assert strict["fits"] is False and "refused" in strict["reason"] and "warning" not in strict
    monkeypatch.setenv("STUDIO_MEMORY_GUARD", "warn")
    warned = memory_guard.check(1376, 768, 362, available_mb=20_000)
    assert warned["fits"] is True and warned["refused"] == strict["reason"]
    assert "tried anyway" in warned["warning"] and "so it is refused" not in warned["warning"]
    fine = memory_guard.check(864, 480, 22, available_mb=120_000)
    assert fine["fits"] is True and "warning" not in fine


def test_the_linux_setup_still_honours_studio_n1x():
    # STUDIO_N1X is a Linux-side setting (memory_guard.n1x() also knows the card by name); the Windows install is native
    assert 'STUDIO_N1X:-0}" = 1' in (ROOT / "oneshot-setup-cuda.sh").read_text()
