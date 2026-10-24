"""A card with memory of its own (an RTX Pro 6000 Blackwell, 96 GB; docs/rtx-pro-6000.md): the Studio's room for a
job is that card's memory, the card is read from nvidia-smi (or pretended with
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
    monkeypatch.delenv("STUDIO_GPU", raising=False)


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


def test_studio_gpu_is_the_card_nvidia_smi_is_asked_for(monkeypatch):
    """The display card can be nvidia-smi's first row. STUDIO_GPU is the index the guard reads."""

    monkeypatch.setenv("STUDIO_GPU", "1")
    seen = []

    def run(command, *args, **kwargs):
        seen.append(command)
        return type("Done", (), {"returncode": 0, "stdout": "97887, 1000, 96887\n"})()

    monkeypatch.setattr(memory_guard.subprocess, "run", run)
    assert memory_guard.device()["total_mb"] == 97887
    assert seen[0][:3] == ["nvidia-smi", "-i", "1"]


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


def test_room_is_the_cards_on_a_card_and_the_rams_on_a_spark(monkeypatch):
    """A card's room is device_room_mb alone. A smaller RAM is not the limit. A Spark (no card) stays on its RAM."""

    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 200 * GB, "MemTotal": 256 * GB})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: 60 * GB)
    assert memory_guard.room_mb() == 60 * GB
    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 50 * GB, "MemTotal": 64 * GB})
    assert memory_guard.room_mb() == 60 * GB  # a smaller RAM is not the limit
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
    assert not said["fits"] and "this GPU" in said["reason"]
    assert said["floor_mb"] == memory_guard.VRAM_MARGIN_MB and "this machine has" not in said["reason"]


def test_voice_and_bolt_see_the_cards_free_memory(monkeypatch):
    """Drama 3 and Bolt decide whether to stop ComfyUI from mem_available_gb: on a card, its free memory alone,
    not the smaller of that and system RAM."""

    monkeypatch.setattr(voice, "gpu_free_gb", lambda: 0.25)
    assert voice.mem_available_gb() == 0.25
    monkeypatch.setattr(voice, "gpu_free_gb", lambda: 500.0)
    assert voice.mem_available_gb() == 500.0  # a smaller RAM is not the limit
    monkeypatch.setattr(voice, "gpu_free_gb", lambda: None)
    monkeypatch.setattr(voice, "_linux_ram_gb", lambda: 80.0)
    if sys.platform.startswith("linux"):
        assert voice.mem_available_gb() == 80.0  # no card reading: the machine's RAM, not the 500 GB just above


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


def test_a_loaded_engine_counts_once_on_a_card(monkeypatch):
    """Queue time is before the unload. The room is the larger of the live card and each engine's raw free, and the
    2 GB margin is taken once. A raw before under the live free does not shrink the room. A passed room is kept.
    A song counts Drama 3 and Music, not Bolt."""

    margin = memory_guard.VRAM_MARGIN_MB
    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemTotal": 256 * GB, "MemAvailable": 200 * GB})
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: None)
    assert memory_guard.card_room_for_job([90 * GB]) is None
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: 8 * GB)
    # 8 GB + 100 MB is above the after-margin room and below the live raw free. Dropping the margin before the
    # max would subtract it from this before and shrink the room.
    assert memory_guard.card_room_for_job([8 * GB + 100]) == 8 * GB
    assert memory_guard.card_room_for_job([None, 0]) == 8 * GB
    assert memory_guard.card_room_for_job([50 * GB, 90 * GB, 70 * GB]) == 90 * GB - margin
    wide = memory_guard.check(1376, 768, 243, available_mb=90 * GB - margin, total_mb=256 * GB, card=True)
    assert wide["fits"] and wide["available_mb"] == 90 * GB and wide["floor_mb"] == margin
    spark = memory_guard.check(1376, 768, 243, available_mb=40 * GB, total_mb=128 * GB)
    assert not spark["fits"] and "this machine has" in spark["reason"] and "this GPU" not in spark["reason"]

    import server
    import music
    assert server.SPARK and music.SPARK
    monkeypatch.setattr(voice, "room_mb", lambda: 50 * GB)
    monkeypatch.setattr(music, "room_mb", lambda: 70 * GB)
    monkeypatch.setattr(server.bolt, "room_mb", lambda: 90 * GB)
    found = server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 22, False)
    assert found["available_mb"] == 90 * GB  # Bolt is the largest. The 200 GB of RAM is not the pool.
    beside = server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 22, False, available_mb=20 * GB)
    assert beside["available_mb"] == 20 * GB + margin
    monkeypatch.setattr(voice, "room_mb", lambda: None)
    monkeypatch.setattr(music, "room_mb", lambda: None)
    monkeypatch.setattr(server.bolt, "room_mb", lambda: None)
    idle = server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 22, False)
    assert idle["available_mb"] == 8 * GB + margin

    class Quiet:
        def check(self):
            return ""
        guard_route = "music-cuda-int8"

    monkeypatch.setattr(music, "_engine", Quiet())
    asked = {}

    def grab(seconds, available_mb=None, total_mb=None, route="music", card=False):
        asked.update(available_mb=available_mb, card=card, route=route)
        return {"fits": True}

    monkeypatch.setattr(memory_guard, "music_check", grab)
    monkeypatch.setattr(voice, "room_mb", lambda: 40 * GB)
    monkeypatch.setattr(music, "room_mb", lambda: 80 * GB)
    monkeypatch.setattr(server.bolt, "room_mb", lambda: 95 * GB)
    music.checked({"caption": "pop", "seconds": 30, "seed": 1})
    assert asked == {"available_mb": 80 * GB - margin, "card": True, "route": "music-cuda-int8"}
    monkeypatch.setattr(voice, "room_mb", lambda: 85 * GB)
    monkeypatch.setattr(music, "room_mb", lambda: 30 * GB)
    music.checked({"caption": "pop", "seconds": 30, "seed": 1})
    assert asked["available_mb"] == 85 * GB - margin and asked["card"] is True


def test_drama_3_names_the_gpu_when_the_card_is_short(monkeypatch):
    monkeypatch.setattr(voice, "has_card_memory", lambda: True)
    monkeypatch.setattr(voice, "mem_available_gb", lambda: 10.0)
    monkeypatch.setattr(voice, "comfy_up", lambda port=None: False)
    monkeypatch.setattr(voice, "settle", lambda: None)
    one = voice.Served.__new__(voice.Served)
    one.NEED_GB, one.title = 50.0, "Drama 3"
    with pytest.raises(RuntimeError, match="this GPU has 10 GB"):
        one.make_room({})
    monkeypatch.setattr(voice, "has_card_memory", lambda: False)
    with pytest.raises(RuntimeError, match="this machine has 10 GB"):
        one.make_room({})


def test_the_page_cache_is_left_alone_on_a_card():
    source = (ROOT / "spark" / "memory_guard.py").read_text()
    release = source[source.index("    def release() -> None:"):]
    assert release.index('if card and not card["simulated"]:') < release.index("release_cache(")
