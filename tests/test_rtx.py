"""A card with memory of its own (an RTX Pro 6000 Blackwell, 96 GB; docs/rtx-pro-6000.md): the Studio's room for a
job is the smaller of the machine's RAM and the card's memory, the card is read from nvidia-smi (or pretended with
STUDIO_VRAM_GB on a Spark), a DGX Spark reads as it always did, and Bolt points an x86_64 machine at llama.cpp.
Run: STUDIO_HOME=$(mktemp -d) python3 -m pytest tests/test_rtx.py"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import bolt  # noqa: E402
import memory_guard  # noqa: E402
import voice  # noqa: E402

GB = 1024


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(memory_guard, "_DEVICE", {"at": 0.0, "value": None})
    monkeypatch.delenv("STUDIO_VRAM_GB", raising=False)


def answers(monkeypatch, gpu=None, apps=None):
    """nvidia-smi as a card (memory.total, used, free in MB) or a GB10 ([N/A]), and its compute apps."""

    def smi(*query):
        if query[0].startswith("--query-gpu"):
            return [gpu] if gpu else []
        return [[str(pid), str(mb)] for pid, mb in (apps or {}).items()]
    monkeypatch.setattr(memory_guard, "smi", smi)


def test_a_card_is_read_from_nvidia_smi(monkeypatch):
    answers(monkeypatch, gpu=["97887", "30000", "67887"])
    assert memory_guard.device() == {"total_mb": 97887, "used_mb": 30000, "free_mb": 67887, "simulated": False}
    assert memory_guard.discrete()


def test_a_dgx_spark_has_no_card_memory_of_its_own(monkeypatch):
    answers(monkeypatch, gpu=["[N/A]", "[N/A]", "[N/A]"])
    assert memory_guard.device() is None and not memory_guard.discrete() and memory_guard.device_room_mb() is None


def test_the_simulation_pretends_a_card_from_what_the_processes_hold(monkeypatch):
    monkeypatch.setenv("STUDIO_VRAM_GB", "96")
    answers(monkeypatch, apps={101: 30 * GB, 202: 10 * GB})
    card = memory_guard.device()
    assert card == {"total_mb": 96 * GB, "used_mb": 40 * GB, "free_mb": 56 * GB, "simulated": True}


def test_the_comfyui_servers_own_memory_counts_as_the_jobs(monkeypatch):
    """As on a Spark: a render runs in the studio's ComfyUI, so what that server holds is the job's room."""

    monkeypatch.setenv("STUDIO_VRAM_GB", "96")
    answers(monkeypatch, apps={101: 30 * GB, 202: 10 * GB})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: 101)
    assert memory_guard.device_room_mb() == 86 * GB - memory_guard.VRAM_MARGIN_MB


def test_room_is_the_smaller_of_ram_and_the_card(monkeypatch):
    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 200 * GB, "MemTotal": 256 * GB})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: 60 * GB)
    floor = memory_guard.floor_mb(256 * GB)
    assert memory_guard.room_mb() == 60 * GB + floor  # the floor is the RAM's: the card keeps VRAM_MARGIN_MB instead
    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 50 * GB, "MemTotal": 64 * GB})
    assert memory_guard.room_mb() == 50 * GB  # a small RAM is the limit
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: None)  # a Spark: as before
    assert memory_guard.room_mb() == 50 * GB


def test_the_largest_measured_clip_fits_a_96_gb_card(monkeypatch):
    """1376x768, 243 frames on the int8 engine (69.0 to 70.9 GB measured as the Spark's whole fall) fits an idle
    96 GB card on a 128 GB machine; a card already half full refuses it."""

    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 120 * GB, "MemTotal": 128 * GB})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: 96 * GB - memory_guard.VRAM_MARGIN_MB)
    assert memory_guard.check(1376, 768, 243)["fits"]
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: 48 * GB)
    said = memory_guard.check(1376, 768, 243)
    assert not said["fits"] and said["reason"]


def test_voice_and_bolt_see_the_cards_free_memory(monkeypatch):
    """Drama 3 and Bolt decide whether to stop ComfyUI from mem_available_gb: on a card, its free memory."""

    monkeypatch.setattr(voice, "gpu_free_gb", lambda: 0.25)
    assert voice.mem_available_gb() == 0.25  # the machine's RAM is more; the card is the limit
    monkeypatch.setattr(voice, "gpu_free_gb", lambda: None)
    assert voice.mem_available_gb() > 0.25 or sys.platform != "linux"  # a Spark: the machine's own


def test_an_x86_64_workstation_is_pointed_at_llama_cpp(monkeypatch):
    monkeypatch.delenv("BOLT_BACKEND", raising=False)
    monkeypatch.setattr(bolt, "SPARK", True)
    monkeypatch.setattr(bolt.Cuda, "present", lambda self: "no Bolt engine in PREFIX.")
    monkeypatch.setattr(bolt.platform, "machine", lambda: "x86_64")
    chosen, why = bolt.choose()
    assert chosen is bolt.External and "BOLT_BACKEND=llamacpp" in why
    monkeypatch.setattr(bolt.platform, "machine", lambda: "aarch64")
    assert "llamacpp" not in bolt.choose()[1]
    assert "CUDA" in bolt.Llamacpp.label or sys.platform == "darwin"


def test_the_page_cache_is_left_alone_on_a_card():
    source = (ROOT / "spark" / "memory_guard.py").read_text()
    release = source[source.index("    def release() -> None:"):]
    assert release.index('if card and not card["simulated"]:') < release.index("release_cache(")
