"""A card with memory of its own (an RTX Pro 6000 Blackwell, 96 GB; docs/rtx-pro-6000.md): the Studio's room for a
job is that card's memory, held to the machine's RAM, STUDIO_ROOM_MB (a 32 GB card) keeps the RAM budget, the card is read from nvidia-smi (or pretended with
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
    monkeypatch.delenv("STUDIO_ROOM_MB", raising=False)
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB", raising=False)
    monkeypatch.setattr(memory_guard, "state_file", lambda: "/nonexistent/studio-comfy.mem")


LINUX = sys.platform.startswith("linux")
spark_only = pytest.mark.skipif(not LINUX, reason="the CUDA build's memory checks run on Linux only (server.SPARK)")


def answers(monkeypatch, gpu=None, apps=None, name=None):
    """nvidia-smi as a card (memory.total, used, free in MB) or a GB10 ([N/A]), its compute apps, and its name."""

    def smi(*query):
        if name and query[0] == "--query-gpu=name":
            return [[name]]
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


def test_two_cards_are_one_pool_and_a_single_card_engine_sees_one(monkeypatch):
    # spark/gpus.py: two 48 GB cards, the transformer on one and the text encoder and decoders on the other
    monkeypatch.delenv("STUDIO_GPU_SPLIT", raising=False)

    def smi(*query):
        if query[0].startswith("--query-gpu"):
            return [["48935", "22000", "26935"], ["48935", "400", "48535"]]
        return []
    monkeypatch.setattr(memory_guard, "smi", smi)
    card = memory_guard.device()
    assert card["total_mb"] == 97870 and card["free_mb"] == 75470 and card["cards"] == 2
    assert memory_guard.single_card_free_mb() == 48535
    monkeypatch.setattr(memory_guard, "_DEVICE", {"at": 0.0, "value": None})
    monkeypatch.setenv("STUDIO_GPU_SPLIT", "off")  # the first card only, as before
    assert memory_guard.device() == {"total_mb": 48935, "used_mb": 22000, "free_mb": 26935, "simulated": False}


def test_paired_cards_are_asked_for_together_the_heads_first(monkeypatch):
    # STUDIO_GPUS (spark/gpu.sh): one Studio renders on cards 0 and 1, so the guard reads both
    monkeypatch.delenv("STUDIO_GPU_SPLIT", raising=False)
    monkeypatch.delenv("STUDIO_LOCAL_GPU_UUID", raising=False)
    monkeypatch.setenv("STUDIO_GPU", "0")
    monkeypatch.setenv("STUDIO_GPUS", "0,1")
    assert memory_guard.paired_gpus() == ["0", "1"]
    assert memory_guard.nvidia_argv() == ["nvidia-smi", "-i", "0,1"]
    # not paired: the head's card alone, as 4.3 pins it
    for env, value in (("STUDIO_GPU", "1"),          # the head's card must come first
                       ("STUDIO_GPUS", "0"),          # one card is no pair
                       ("STUDIO_GPUS", "0,x"),        # not indexes
                       ("STUDIO_GPU_SPLIT", "off")):  # pairing switched off
        monkeypatch.setenv("STUDIO_GPU", "0")
        monkeypatch.setenv("STUDIO_GPUS", "0,1")
        monkeypatch.setenv(env, value)
        assert memory_guard.paired_gpus() == [], (env, value)
        monkeypatch.delenv("STUDIO_GPU_SPLIT", raising=False)
    # a managed local worker is pinned to one card by UUID and never pairs, whatever it inherited
    monkeypatch.setenv("STUDIO_GPU", "0")
    monkeypatch.setenv("STUDIO_GPUS", "0,1")
    monkeypatch.setenv("STUDIO_LOCAL_GPU_UUID", "GPU-00000000-0000-0000-0000-000000000001")
    assert memory_guard.paired_gpus() == [] and memory_guard.nvidia_argv() == ["nvidia-smi", "-i", "0"]


def test_the_servers_child_processes_count_as_the_jobs(monkeypatch):
    # a two-GPU pass: ComfyUI (pid 10) holds 25 GB on the first card, its rank 1 (pid 11) 22 GB on the second; a
    # stranger (pid 99) holds 5 GB. Only the stranger's memory is not the job's.
    monkeypatch.setattr(memory_guard, "device", lambda fresh=False: {"total_mb": 96 * GB, "used_mb": 52 * GB,
                                                                     "free_mb": 44 * GB, "simulated": False})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: 10)
    monkeypatch.setattr(memory_guard, "family", lambda pid: {10, 11})
    monkeypatch.setattr(memory_guard, "processes_mb", lambda: {10: 25 * GB, 11: 22 * GB, 99: 5 * GB})
    assert memory_guard.device_room_mb() == (44 + 25 + 22) * GB - memory_guard.VRAM_MARGIN_MB


def test_family_finds_a_process_and_its_children():
    import subprocess
    child = subprocess.Popen(["sleep", "5"])
    try:
        assert {os.getpid(), child.pid} <= memory_guard.family(os.getpid())
    finally:
        child.kill()


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
    """A card's room_mb is device_room_mb (the checks hold it to the RAM: card_pool). A Spark (no card) stays on its RAM."""

    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 200 * GB, "MemTotal": 256 * GB})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    monkeypatch.setattr(memory_guard, "device_room_mb", lambda: 60 * GB)
    assert memory_guard.room_mb() == 60 * GB
    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemAvailable": 50 * GB, "MemTotal": 64 * GB})
    assert memory_guard.room_mb() == 60 * GB  # the card's figure; the checks hold it to the RAM
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


@spark_only
def test_voice_and_bolt_see_the_smaller_of_the_card_and_the_ram(monkeypatch):
    """Drama 3 and Bolt decide whether to stop ComfyUI from mem_available_gb: on a card, the smaller of its free
    memory and MemAvailable (the weights are read through RAM). has_card_memory says when the card is that one."""

    monkeypatch.setattr(voice, "_linux_ram_gb", lambda: 80.0)
    monkeypatch.setattr(voice, "gpu_free_gb", lambda: 0.25)
    assert voice.mem_available_gb() == 0.25
    monkeypatch.setattr(voice, "gpu_free_gb", lambda: 500.0)
    assert voice.mem_available_gb() == 80.0  # a smaller RAM is the limit
    monkeypatch.setattr(voice, "gpu_free_gb", lambda: None)
    assert voice.mem_available_gb() == 80.0  # a Spark: the machine's RAM

    answers(monkeypatch, gpu=["97887", "500", "97387"])
    memory_guard.device()  # the reading mem_available_gb just took
    assert not voice.has_card_memory()  # 95 GB free on the card, 80 in RAM: the RAM is the number
    monkeypatch.setattr(voice, "_linux_ram_gb", lambda: 120.0)
    assert voice.has_card_memory()
    answers(monkeypatch, gpu=["[N/A]", "[N/A]", "[N/A]"])
    memory_guard.device(fresh=True)
    assert not voice.has_card_memory()  # a GB10


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


@spark_only
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


def rtx_pro_6000_with_40_gb_free(monkeypatch):
    """An RTX Pro 6000 (96 GB, idle) in a machine with 64 GB of RAM, 40 GB of it available."""

    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 65_000, "MemAvailable": 40_000, "MemFree": 40_000})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    answers(monkeypatch, gpu=["97887", "500", "97387"])


def as_main(width, height, frames=124):
    """main's arithmetic before the card had its own pool: min(ram, card + floor) against floor_mb."""

    info = memory_guard.meminfo()
    room = memory_guard.room_budget_mb()
    card = memory_guard.device_room_mb()
    ram = info["MemAvailable"]
    pool = min(ram, room) if room else min(ram, card + memory_guard.floor_mb(info["MemTotal"]))
    return memory_guard.check(width, height, frames, available_mb=pool, total_mb=info["MemTotal"])


@spark_only
def test_an_rtx_pro_6000_with_40_gb_of_ram_free_refuses_what_main_refused(monkeypatch):
    """The card has 95 GB, the machine 40 GB available: the RAM is what runs out, so a 1344x768 clip is refused as
    it was, and the sentence names the machine, not the GPU."""

    rtx_pro_6000_with_40_gb_free(monkeypatch)
    big = memory_guard.check(1344, 768, 124)
    assert not big["fits"] and big["fits"] == as_main(1344, 768)["fits"]
    assert "this machine has" in big["reason"] and "this GPU" not in big["reason"]
    assert big["floor_mb"] == memory_guard.floor_mb(65_000) and big["available_mb"] == 40_000
    for width, height in ((864, 480), (1280, 720), (1344, 768)):
        assert memory_guard.check(width, height, 124)["fits"] == as_main(width, height)["fits"], (width, height)
    # through the app, as a job is queued: no engine loaded
    import music
    import server
    monkeypatch.setattr(server, "SPARK_ENGINE_LIB", Path(__file__))  # TensorFold's engine is there
    monkeypatch.setattr(voice, "room_mb", lambda: None)
    monkeypatch.setattr(music, "room_mb", lambda: None)
    monkeypatch.setattr(server.bolt, "room_mb", lambda: None)
    assert server.memory_of({}, {"WIDTH": 1344, "HEIGHT": 768}, 124, False)["fits"] is False
    # beside Bolt the RAM still binds
    beside = memory_guard.check(1344, 768, 124, available_mb=60 * GB, card=True)
    assert not beside["fits"] and "this machine has" in beside["reason"]
    # songs and sound effects are held to the RAM the same way
    song = memory_guard.music_check(300, available_mb=90 * GB, card=True)
    assert song["available_mb"] == 40_000 and song["floor_mb"] == memory_guard.floor_mb(65_000)


@spark_only
def test_with_plenty_of_ram_the_card_is_the_pool(monkeypatch):
    """128 GB of RAM, 120 available: the card runs out first, and its arithmetic (the 2 GB margin) and words stay."""

    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 128 * GB, "MemAvailable": 120 * GB})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    answers(monkeypatch, gpu=["97887", "500", "97387"])
    said = memory_guard.check(1344, 768, 124)
    assert said["floor_mb"] == memory_guard.VRAM_MARGIN_MB and said["available_mb"] == 97_387
    assert said["fits"] == as_main(1344, 768)["fits"]
    half = memory_guard.check(1376, 768, 243, available_mb=40 * GB, card=True)
    assert not half["fits"] and "this GPU has" in half["reason"]


@spark_only
def test_a_32_gb_5090_with_studio_room_mb_fits_video_as_main_does(monkeypatch):
    """#147: STUDIO_ROOM_MB is a RAM budget for a card smaller than the models. It is the pool, with the Spark's
    floor, exactly as on main; the card's own 30 GB is not asked."""

    import music
    import server
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 93_000, "MemAvailable": 80_000})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    answers(monkeypatch, gpu=["32607", "600", "32007"])
    monkeypatch.setattr(server, "SPARK_ENGINE_LIB", Path(__file__))  # TensorFold's engine is there
    monkeypatch.setattr(voice, "room_mb", lambda: None)
    monkeypatch.setattr(music, "room_mb", lambda: None)
    monkeypatch.setattr(server.bolt, "room_mb", lambda: None)
    without = memory_guard.check(1344, 768, 124)
    assert not without["fits"] and "this GPU" in without["reason"]  # the card alone is too small
    monkeypatch.setenv("STUDIO_ROOM_MB", "84000")  # docs/rtx-5090.md; the RAM has 80 GB, so 80 GB is the room
    assert not memory_guard.card_room_applies() and memory_guard.room_mb() == 80_000
    for width, height in ((864, 480), (1344, 768)):
        said, main = memory_guard.check(width, height, 124), as_main(width, height)
        assert said["fits"] == main["fits"] and said["available_mb"] == 80_000 and said["floor_mb"] == main["floor_mb"]
        assert server.memory_of({}, {"WIDTH": width, "HEIGHT": height}, 124, False)["fits"] == main["fits"]
    assert memory_guard.check(864, 480, 124)["fits"] and memory_guard.check(1344, 768, 124)["fits"]
    assert server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 124, False)["available_mb"] == 80_000


@spark_only
def test_a_gb10_is_unchanged(monkeypatch):
    """[N/A] from nvidia-smi: no card pool, RAM and floor_mb as before, Voice reads MemAvailable, and the machine is a
    Spark, also with STUDIO_VRAM_GB unset or STUDIO_ROOM_MB set."""

    import server
    import sparks
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": 124_000, "MemAvailable": 110_000})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    answers(monkeypatch, gpu=["[N/A]", "[N/A]", "[N/A]"])
    monkeypatch.setattr(server, "GPU_NAME", ["NVIDIA GB10"])
    monkeypatch.setattr(voice, "_linux_ram_gb", lambda: 107.4)
    for room in (None, "84000"):
        if room:
            monkeypatch.setenv("STUDIO_ROOM_MB", room)
        assert not memory_guard.card_room_applies() and memory_guard.room_mb() == 110_000
        for width, height in ((864, 480), (1344, 768)):
            said = memory_guard.check(width, height, 124)
            spark = memory_guard.check(width, height, 124, available_mb=110_000, total_mb=124_000)
            assert {k: said[k] for k in ("fits", "available_mb", "floor_mb", "need_mb")} == \
                   {k: spark[k] for k in ("fits", "available_mb", "floor_mb", "need_mb")}
        assert voice.mem_available_gb() == 107.4 and not voice.has_card_memory()
        assert server.machine()[1].startswith("DGX SPARK") and sparks.machine_word(True) == "Spark"


@spark_only
def test_a_spark_pretending_a_card_keeps_its_word_with_its_banner(monkeypatch):
    """STUDIO_VRAM_GB on a GB10 (the RTX Pro 6000 simulation): the banner stays DGX SPARK, so the word stays Spark."""

    import server
    import sparks
    monkeypatch.setenv("STUDIO_VRAM_GB", "96")
    answers(monkeypatch, apps={})
    monkeypatch.setattr(server, "GPU_NAME", ["NVIDIA GB10"])
    assert memory_guard.device() is not None
    assert server.machine()[1].startswith("DGX SPARK") and sparks.machine_word(True) == "Spark"
    monkeypatch.setattr(server, "GPU_NAME", ["NVIDIA GeForce RTX 5090"])
    assert sparks.machine_word(True) == "GPU" and sparks.machine_word(False) == "Mac"


def test_no_module_imports_the_server_again():
    """The app runs server.py as __main__: importing server from another module would run the app a second time.
    The card face is looked up among the loaded modules (sparks.card_face) instead."""

    for name in ("sparks.py", "agents.py", "loras.py", "voice.py", "music.py", "bolt.py", "sound.py"):
        source = (ROOT / "app" / name).read_text()
        assert "import server\n" not in source and "import server " not in source, name


def test_the_page_cache_is_left_alone_on_a_card():
    source = (ROOT / "spark" / "memory_guard.py").read_text()
    release = source[source.index("    def release() -> None:"):]
    assert release.index('if card and not card["simulated"]:') < release.index("release_cache(")


# three machines as nvidia-smi and /proc/meminfo show them: a DGX Spark, an RTX PRO 6000 on a 128 GB machine, and a
# 32 GB RTX 5090 on a 91 GB one (docs/rtx-5090.md)
MACHINES = {"gb10": (["[N/A]", "[N/A]", "[N/A]"], "NVIDIA GB10", {"MemTotal": 124_500, "MemAvailable": 118_000}),
            "rtx-pro-6000": (["97887", "600", "97287"], "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
                             {"MemTotal": 128_000, "MemAvailable": 120_000}),
            "rtx-5090": (["32607", "600", "32007"], "NVIDIA GeForce RTX 5090", {"MemTotal": 93_000, "MemAvailable": 88_000})}


def machine(monkeypatch, which, room=None):
    gpu, name, info = MACHINES[which]
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: dict(info))
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    answers(monkeypatch, gpu=gpu, name=name)
    if room:
        monkeypatch.setenv("STUDIO_ROOM_MB", str(room))


@pytest.mark.parametrize("which,room,fits", [
    ("gb10", None, {480: True, 768: True}),
    ("rtx-pro-6000", None, {480: True, 768: True}),
    ("rtx-5090", None, {480: False, 768: False}),
    ("rtx-5090", 84000, {480: True, 768: True}),
])
def test_character_swap_s_memory_on_each_machine(monkeypatch, which, room, fits):
    machine(monkeypatch, which, room)
    for quality in (480, 768):
        said = memory_guard.swap_check(quality)
        assert said["fits"] is fits[quality], (which, room, quality, said)
        on_card, offloaded = which != "gb10" and not room, which != "gb10" and bool(room)
        table = (memory_guard.SWAP_CARD_ESTIMATE if on_card else memory_guard.SWAP_OFFLOAD_ESTIMATE if offloaded
                 else memory_guard.SWAP_ESTIMATE)
        assert said["need_mb"] == table[quality] and said["route"].endswith("-card" if on_card else "-offload" if offloaded else str(quality))
        if not said["fits"]:  # the card by name, and what to do
            assert "this GPU (NVIDIA GeForce RTX 5090)" in said["reason"] and "STUDIO_ROOM_MB" in said["reason"]
    # the bf16 Ref2VA (not measured) is held to its larger file
    assert memory_guard.swap_check(480, weights="bf16")["need_mb"] == memory_guard.swap_check(480)["need_mb"] + memory_guard.SWAP_BF16_EXTRA_MB


def test_a_swap_too_large_says_the_smaller_quality_that_fits(monkeypatch):
    machine(monkeypatch, "gb10")
    need480, need768 = memory_guard.SWAP_ESTIMATE[480], memory_guard.SWAP_ESTIMATE[768]
    room = need480 + memory_guard.ERROR_MB + memory_guard.floor_mb(124_500) + (need768 - need480) // 2
    said = memory_guard.swap_check(768, available_mb=room)
    assert not said["fits"] and said["nearest"] == {"quality": 480} and "Fast (480p) fits" in said["reason"]
    assert "this machine has" in said["reason"]


@spark_only
@pytest.mark.parametrize("which", ["gb10", "rtx-pro-6000", "rtx-5090"])
def test_a_swap_with_bolt_resident_is_held_to_the_swap_s_own_peak(monkeypatch, which):
    import server

    machine(monkeypatch, which)
    monkeypatch.setattr(server.bolt.engine(), "loaded", lambda: False)
    said = server.bolt_need({"kind": "swap", "params": {"quality": 480}}, 28.0)
    assert said is not None and "the job's estimated peak" in said[1]
    expected = (memory_guard.SWAP_ESTIMATE if which == "gb10" else memory_guard.SWAP_CARD_ESTIMATE)[480]
    assert f"about {expected / 1000:.0f} GB" in said[1]


@spark_only
def test_yue2_songs_on_a_32_gb_5090(monkeypatch):
    """Measured under a 32 GB limit: a 30 s song peaked at 16.3 GB on the card, a 300 s one fitted only with
    STUDIO_ROOM_MB. The card's own room takes songs up to about two minutes."""

    machine(monkeypatch, "rtx-5090")
    have = memory_guard.card_room_for_job([])
    short = memory_guard.music_check(120, available_mb=have, route="music-yue2-int8", card=True)
    long = memory_guard.music_check(300, available_mb=have, route="music-yue2-int8", card=True)
    assert short["fits"] and not long["fits"] and "this GPU" in long["reason"]
    monkeypatch.setenv("STUDIO_ROOM_MB", "84000")
    assert memory_guard.music_check(300)["fits"]


def test_each_machine_is_told_its_own_installer(monkeypatch):
    import music_yue2
    import sparks

    for card, entry in ((False, "oneshot-setup-spark.sh --yue2"), (True, "oneshot-setup-rtx-6000.sh --yue2")):
        monkeypatch.setattr(sparks, "card_face", lambda card=card: card)
        assert music_yue2.install_command() == entry
