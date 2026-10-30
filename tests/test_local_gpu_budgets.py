"""Card host-RAM budgets retain conservative fallbacks outside the measured route."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import local_gpus

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="local GPU admission uses Linux process identity")


@pytest.fixture
def card_host(monkeypatch):
    monkeypatch.setattr(server, "SPARK", True)
    monkeypatch.setattr(server.memory_guard, "meminfo", lambda: {"MemTotal": 128000, "MemAvailable": 116000})
    monkeypatch.setattr(server.memory_guard, "device", lambda **_: {"total_mb": 97887, "free_mb": 97000, "used_mb": 0})
    monkeypatch.setattr(server.memory_guard, "device_room_mb", lambda: 94952)
    monkeypatch.setattr(server, "SPARK_ENGINE_LIB", Path(__file__))
    monkeypatch.setattr(server.voice, "room_mb", lambda: None)
    monkeypatch.setattr(server.music, "room_mb", lambda: None)
    monkeypatch.setattr(server.bolt, "room_mb", lambda: None)
    monkeypatch.setattr(local_gpus, "memory", lambda: (128000, 116000))
    for key in ("STUDIO_ROOM_MB", "SPARK_ENGINE", "SPARK_BASE", "TEXT_ENCODER", "SPARK_MEMORY_FLOOR_MB"):
        monkeypatch.delenv(key, raising=False)


def clip(**params):
    return {"kind": "video", "params": {"preset": "small", "quality": "fh4", "frames": 22, **params}, "status": "running"}


def test_two_cold_card_clips_overlap_without_spending_the_host_floor(card_host):
    first, second = clip(), clip()
    with local_gpus.ram_reservation(server.local_ram_need(first), first) as admitted:
        assert admitted
        with local_gpus.ram_reservation(server.local_ram_need(second), second) as also_admitted:
            assert also_admitted
            reservations = local_gpus.load(local_gpus.ram_dir() / "reservations.json")
            assert sum(row["need_mb"] for row in reservations.values()) <= 116000 - server.memory_guard.floor_mb(128000)


@pytest.mark.parametrize("change", ["small_card", "offload", "longer_clip", "other_encoder", "comfy_engine"])
def test_unmeasured_card_routes_keep_the_unified_upper_bound(card_host, monkeypatch, change):
    job = clip()
    if change == "small_card":
        monkeypatch.setattr(server.memory_guard, "device", lambda **_: {"total_mb": 32768, "free_mb": 32000, "used_mb": 0})
    elif change == "offload":
        monkeypatch.setenv("STUDIO_ROOM_MB", "84000")
    elif change == "longer_clip":
        job["params"]["frames"] = 260
    elif change == "other_encoder":
        monkeypatch.setenv("TEXT_ENCODER", "another-encoder.safetensors")
    else:
        monkeypatch.setenv("SPARK_ENGINE", "comfy")
    assert server.local_ram_need(job) > server.LOCAL_VIDEO_HOST_MB + server.memory_guard.ERROR_MB


def test_character_swap_has_a_budget_instead_of_excluding_every_other_gpu(card_host, monkeypatch):
    monkeypatch.setattr(server.swap_build, "weights", lambda: "int8")
    swap = {"kind": "swap", "params": {"quality": 480}, "status": "running"}
    need = server.local_ram_need(swap)
    assert need is not None
    with local_gpus.ram_reservation(need, swap) as admitted:
        assert admitted
        video = clip()
        with local_gpus.ram_reservation(server.local_ram_need(video), video) as also_admitted:
            assert also_admitted
