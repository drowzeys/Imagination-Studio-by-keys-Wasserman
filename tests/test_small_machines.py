"""Small machines: a GeForce under 24 GB (docs/small-cards.md: oneshot-setup-rtx-6000.sh --small), a Mac under 96 GB
(oneshot-setup.sh --small) and a Mac with no models (--client, a MacBook Neo). python3 -m pytest tests/test_small_machines.py.
Nothing installs: the installers stop at their dry-run lines, and the guard is asked with a stand-in nvidia-smi."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "spark"))
import memory_guard  # noqa: E402

from test_install_checklist import BASH, disk_need, env, fake_mac, mac_install  # noqa: E402

GB = 1024
FASTH3_INT8 = "FastH3-Comfy/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"
FASTH3_BF16 = "FastH3-Comfy/diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors"
QWEN_INT8 = "Qwen-Image-2.1-Comfy/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors"
QWEN_BF16 = "Qwen-Image-2.1-Comfy/diffusion_models/qwen_image_2.1_turbo_bf16.safetensors"
H3_ENCODER = "FastH3-Comfy/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
REF2VA_INT8 = "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
H3_ENCODER_NVFP4 = "FastH3-Comfy/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"


def ram_gb() -> int:
    try:
        total = int(next(l for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")).split()[1])
    except (OSError, StopIteration):
        return 0
    return total // (1024 * 1024)


linux_with_24_gb = pytest.mark.skipif(not sys.platform.startswith("linux") or ram_gb() < 24,
                                      reason="--small's preflight wants 24 GB of RAM on a Linux machine")


# ---------- the CUDA installer on a small card ----------

@pytest.mark.parametrize("card", ["rtx-5060-ti", "rtx-4060"])
def test_a_small_card_is_refused_without_small_and_told_the_flag(tmp_path, card):
    code, said = disk_need(tmp_path, card)
    assert code == 1 and "--small" in said and "docs/small-cards.md" in said
    assert "STUDIO_ALLOW_SMALL_VRAM=1" in said  # the 5090's own escape stays


@linux_with_24_gb
@pytest.mark.parametrize("card,vram", [("rtx-5060-ti", 15), ("rtx-4060", 7)])
def test_small_installs_video_on_the_8_bit_file(tmp_path, card, vram):
    """--small fetches FastH3's 8-bit transformer (22 GB) and the same text encoder and decoders (31 GB): 53 GB in place
    of the bf16 install's 75, and Qwen-Image-2.1-Turbo's 8-bit checkpoint (7.3 GB): 19 in place of 26. Each part is
    counted by its transformer file, as the bf16 ones are: a machine that holds the 8-bit files already needs the
    base alone; --image-only takes the 8-bit picture model too."""

    base = 5 + 19 + 1 + 1  # Qwen-Image (8-bit), motion graphics and Finish
    assert disk_need(tmp_path, card, "--small") == (0, f"need={base + 26 + 27} ref2va=0 swap=0 vram={vram}")
    assert disk_need(tmp_path, card, "--small", "--image-only") == (0, f"need={base} ref2va=0 swap=0 vram={vram}")
    # an H3 text encoder already here (another build of it, an NVFP4 one) stands in for the 27 GB int8 download
    assert disk_need(tmp_path, card, "--small", have=[H3_ENCODER_NVFP4]) == (0, f"need={base + 26} ref2va=0 swap=0 vram={vram}")
    assert disk_need(tmp_path, card, "--small", have=[FASTH3_INT8]) == (0, f"need={base} ref2va=0 swap=0 vram={vram}")  # the files stay
    assert disk_need(tmp_path, card, "--small", have=[QWEN_INT8]) == (0, f"need={base - 19} ref2va=0 swap=0 vram={vram}")
    setup = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert 'h3_encoder_here() { ls "$MODELS"/FastH3-Comfy/text_encoders/qwen3vl_32b_minimax_h3_*.safetensors' in setup


@linux_with_24_gb
def test_small_ref2va_and_swap_share_the_8_bit_ref2va(tmp_path):
    """--small --ref2va fetches the 8-bit Ref2VA (21 GB) in place of the bf16 file (40 GB) and its Turbo adapter;
    --swap runs on the same file, so the two together count it once. SAM 3.1 and the LoRAs are the swap's own."""

    core = 5 + 19 + 1 + 1 + 26 + 27
    assert disk_need(tmp_path, "rtx-5060-ti", "--small", "--ref2va") == (0, f"need={core + 22} ref2va=1 swap=0 vram=15")
    assert disk_need(tmp_path, "rtx-5060-ti", "--small", "--swap") == (0, f"need={core + 22 + 4} ref2va=0 swap=1 vram=15")
    assert disk_need(tmp_path, "rtx-5060-ti", "--small", "--ref2va", "--swap") == (0, f"need={core + 22 + 4} ref2va=1 swap=1 vram=15")
    assert disk_need(tmp_path, "rtx-5060-ti", "--small", "--ref2va", "--swap", have=[REF2VA_INT8]) == (0, f"need={core + 4} ref2va=1 swap=1 vram=15")
    setup = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert 'if [ "$REF2VA" = 1 ] && [ "$IMAGE_ONLY" = 0 ] && [ "$SMALL" = 1 ]; then' in setup
    generate = (ROOT / "spark" / "spark_generate.py").read_text()
    assert "rendering the model's 20 passes instead" in generate  # Turbo asked, the bf16 file absent: the 8-bit file, 20 passes


def test_small_is_for_a_card_with_memory_of_its_own(tmp_path):
    code, said = disk_need(tmp_path, "gb10", "--small")
    assert code == 1 and "a DGX Spark takes the plain install" in said


def test_the_installer_leaves_the_engine_and_the_lora_bundle_out_on_a_small_card():
    setup = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert 'if [ "$IMAGE_ONLY" = 0 ] && { [ "$SMALL" = 1 ] || { [ -n "$VRAM_GB" ] && [ "$VRAM_GB" -lt 24 ]; }; }; then' in setup
    assert "Essential LoRA bundle: left out on a small card" in setup
    assert 'TEST_FRAMES=124; [ "$SMALL" = 0 ] || TEST_FRAMES=56' in setup
    # oneshot-setup.sh hands --small to the CUDA installer on Linux, and --client is the Mac's alone
    mac = (ROOT / "oneshot-setup.sh").read_text()
    assert "--image-only|--small|--no-render" in mac and "--turbo|--app|--client)" in mac


# ---------- the memory guard on a small card ----------

def card(monkeypatch, tmp_path, total_mb, name, ram=(31_000, 26_000), small=True):
    """A card of ``total_mb`` beside ``ram``, on an install with (``small``) or without the mark --small leaves."""

    def smi(*query):
        if query[0] == "--query-gpu=name":
            return [[name]]
        if query[0].startswith("--query-gpu"):
            return [[str(total_mb), "600", str(total_mb - 600)]]
        return []
    monkeypatch.setattr(memory_guard, "smi", smi)
    monkeypatch.setattr(memory_guard, "meminfo", lambda text=None: {"MemTotal": ram[0], "MemAvailable": ram[1]})
    monkeypatch.setattr(memory_guard, "server_pid", lambda: None)
    for name_ in ("STUDIO_MEMORY_GUARD", "STUDIO_SMALL_CARD", "STUDIO_ROOM_MB", "STUDIO_N1X", "STUDIO_VRAM_GB", "STUDIO_GPUS"):
        monkeypatch.delenv(name_, raising=False)
    monkeypatch.setenv("PREFIX", str(tmp_path / "prefix"))
    (tmp_path / "prefix").mkdir(exist_ok=True)
    if small:
        (tmp_path / "prefix" / "small-card").write_text("")
    memory_guard._N1X.clear()
    memory_guard.device(fresh=True)


@pytest.mark.parametrize("total_mb,name", [(16311, "NVIDIA GeForce RTX 5060 Ti"), (8188, "NVIDIA GeForce RTX 4060")])
def test_a_small_card_install_warns_instead_of_refusing(monkeypatch, tmp_path, total_mb, name):
    """The Spark's estimate (54 GB with the int8 models held whole) cannot fit a 16 GB card beside 31 GB of RAM, and is
    not its peak either: ComfyUI streams the weights. On an install made with --small (its mark at $PREFIX/small-card)
    the guard says so and lets the clip try."""

    card(monkeypatch, tmp_path, total_mb, name)
    assert memory_guard.small_card() and memory_guard.guard_mode() == "warn"
    said = memory_guard.check(864, 480, 124, engine=False)
    assert said["fits"] and said["refused"] and "a small card's default" in said["warning"]
    assert "fails if the memory runs out" in said["warning"]


def test_a_small_card_install_keeps_a_tenth_of_the_machine_not_8_gb(monkeypatch, tmp_path):
    """The watcher's floor: 8 GB is a Spark's; a 31 GB machine streaming a 22 GB transformer keeps 3 GB (a tenth),
    never under 1.5 GB, and SPARK_MEMORY_FLOOR_MB still names any floor."""

    card(monkeypatch, tmp_path, 16311, "NVIDIA GeForce RTX 5060 Ti")
    assert memory_guard.floor_mb(31_000) == 3_100 and memory_guard.floor_mb(12_000) == 1_500
    monkeypatch.setenv("SPARK_MEMORY_FLOOR_MB", "5000")
    assert memory_guard.floor_mb(31_000) == 5_000
    monkeypatch.delenv("SPARK_MEMORY_FLOOR_MB")
    monkeypatch.setenv("STUDIO_SMALL_CARD", "0")
    assert memory_guard.floor_mb(31_000) == 8_000  # not a small-card install: the Spark's floor, as before


def test_a_small_card_can_be_told_to_refuse_again(monkeypatch, tmp_path):
    card(monkeypatch, tmp_path, 16311, "NVIDIA GeForce RTX 5060 Ti")
    monkeypatch.setenv("STUDIO_MEMORY_GUARD", "strict")
    said = memory_guard.check(864, 480, 124, engine=False)
    assert not said["fits"] and "refused" in said["reason"]
    monkeypatch.delenv("STUDIO_MEMORY_GUARD")
    monkeypatch.setenv("STUDIO_SMALL_CARD", "0")
    assert not memory_guard.small_card() and memory_guard.guard_mode() == "strict"


def test_the_mark_is_the_install_s_choice_not_the_card_s_size(monkeypatch, tmp_path):
    """Without --small a 16 GB card keeps the strict guard, as a 32 GB RTX 5090 does (docs/rtx-5090.md); the mark or
    STUDIO_SMALL_CARD=1 turns the warning on, and an install without --small takes the mark away again."""

    card(monkeypatch, tmp_path, 16311, "NVIDIA GeForce RTX 5060 Ti", small=False)
    assert not memory_guard.small_card() and memory_guard.guard_mode() == "strict"
    assert not memory_guard.check(864, 480, 124, engine=False)["fits"]
    monkeypatch.setenv("STUDIO_SMALL_CARD", "1")
    assert memory_guard.small_card() and memory_guard.guard_mode() == "warn"
    setup = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert 'if [ "$SMALL" = 1 ]; then : > "$PREFIX/small-card"; else rm -f "$PREFIX/small-card"; fi' in setup


# ---------- which FastH3 file a clip renders on ----------

def test_a_small_card_install_renders_on_the_8_bit_file_by_default(tmp_path, monkeypatch):
    if sys.platform == "darwin" or sys.platform == "win32":
        pytest.skip("the 8-bit default is the CUDA build's")
    monkeypatch.delenv("SPARK_WEIGHTS", raising=False)
    import spark_generate
    monkeypatch.setattr(spark_generate, "MODELS", tmp_path / "models")  # read when asked, as SPARK_MODELS sets it
    assert spark_generate.default_weights() == "bf16"  # nothing here: the bf16 name, as before
    (tmp_path / "models" / FASTH3_INT8).parent.mkdir(parents=True)
    (tmp_path / "models" / FASTH3_INT8).write_bytes(b"x")
    assert spark_generate.default_weights() == "int8"
    (tmp_path / "models" / FASTH3_BF16).write_bytes(b"x")
    assert spark_generate.default_weights() == "bf16"
    # the picture model the same way: the 8-bit Turbo checkpoint where it is the only one, SPARK_IMAGE_WEIGHTS over both
    monkeypatch.delenv("SPARK_IMAGE_WEIGHTS", raising=False)
    assert spark_generate.qwen_file() == spark_generate.QWEN
    (tmp_path / "models" / QWEN_INT8).parent.mkdir(parents=True)
    (tmp_path / "models" / QWEN_INT8).write_bytes(b"x")
    assert spark_generate.qwen_file() == spark_generate.QWEN_INT8
    (tmp_path / "models" / QWEN_BF16).write_bytes(b"x")
    assert spark_generate.qwen_file() == spark_generate.QWEN
    monkeypatch.setenv("SPARK_IMAGE_WEIGHTS", "int8")
    assert spark_generate.qwen_file() == spark_generate.QWEN_INT8
    # the H3 text encoder: the regular int8 file, or the one other build of it that is here (an NVFP4 one)
    assert spark_generate.default_text_encoder() == "regular"  # nothing here: the regular name, as before
    (tmp_path / "models" / H3_ENCODER_NVFP4).parent.mkdir(parents=True)
    (tmp_path / "models" / H3_ENCODER_NVFP4).write_bytes(b"x")
    assert spark_generate.default_text_encoder() == "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    (tmp_path / "models" / H3_ENCODER_NVFP4).with_name("qwen3vl_32b_minimax_h3_w4a8.safetensors").write_bytes(b"x")
    assert spark_generate.default_text_encoder() == "regular"  # two others: no guess, the person picks one in the app
    (tmp_path / "models" / H3_ENCODER).write_bytes(b"x")
    assert spark_generate.default_text_encoder() == "regular"
    # app/discover.py lists the 8-bit checkpoint, so a copy elsewhere on the machine is linked instead of downloaded
    sys.path.insert(0, str(ROOT / "app"))
    import discover
    assert ("QWEN_INT8", None) in discover.SPARK_IDS
    assert any(f.endswith(spark_generate.QWEN_INT8) for _, _, f in discover.spark_fetches())


# ---------- a smaller Mac, and a Mac with no models ----------

def test_a_64_gb_mac_installs_video_with_small(tmp_path):
    code, said = mac_install(tmp_path, 64, "--small", "--no-render")
    assert code == 0 and "need=64 video=1" in said
    code, said = mac_install(tmp_path, 95, "--small", "--no-render")
    assert code == 0 and "need=64 video=1" in said
    code, said = mac_install(tmp_path, 48, "--small", "--no-render")  # under 64 GB: pictures only
    assert code == 1 and "needs 64 GB" in said and "bash oneshot-setup.sh --image-only" in said
    code, said = mac_install(tmp_path, 48, "--small", "--image-only", "--no-render")
    assert code == 0 and "need=48 video=0" in said


def test_a_64_gb_mac_without_small_is_told_the_flag(tmp_path):
    code, said = mac_install(tmp_path, 64, "--no-render")
    assert code == 1 and "--small installs video and music from 64 GB" in said and "bash oneshot-setup.sh --image-only" in said
    code, said = mac_install(tmp_path, 48, "--no-render")  # under 64 GB there is nothing --small adds
    assert code == 1 and "--small" not in said and "bash oneshot-setup.sh --image-only" in said


def test_small_leaves_voice_and_bolt_out_under_96_gb(tmp_path):
    code, said = mac_install(tmp_path, 64, "--small", "--voice", "--bolt", "--no-render")
    assert code == 0 and "Voice left out" in said and "Bolt brain left out" in said and "need=64 video=1" in said
    code, said = mac_install(tmp_path, 96, "--small", "--voice", "--no-render")
    assert code == 0 and "Voice left out" not in said
    code, said = mac_install(tmp_path, 64, "--small", "--all", "--no-render")
    assert code == 0 and "--all --small on 64 GB: video, music, sound, image edit and Motion Previs" in said
    code, said = mac_install(tmp_path, 64, "--all", "--no-render")  # --all without --small says what --small adds
    assert code == 0 and "pictures only (video, voice, music and Bolt need 96 GB)" in said and "--small installs video and music from 64 GB" in said


def test_a_macbook_neo_is_a_client(tmp_path):
    """A MacBook Neo (A18 Pro, 8 GB) holds no model: --client passes the memory check with no floor and no video."""

    done = subprocess.run([BASH, str(ROOT / "oneshot-setup.sh"), "--client"], capture_output=True, text=True, timeout=60,
                          env=env(PATH=fake_mac(tmp_path, 8, chip="Apple A18 Pro"), STUDIO_NO_CHECKLIST="1", STUDIO_MEMORY_DRY="1",
                                  PREFIX=str(tmp_path / "prefix"), H3_MODEL_DIR=str(tmp_path / "h3"), HOME=str(tmp_path)))
    said = done.stdout + done.stderr
    assert done.returncode == 0, said
    assert "need=0 video=0" in said and "--client: no models on this Mac" in said and "Apple A18 Pro, macOS" in said
    # --client with model flags: they are left out, not installed
    code, said = mac_install(tmp_path, 8, "--client", "--voice", "--music")
    assert code == 0 and "need=0 video=0" in said


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_the_estimate_of_a_small_card_says_the_clip_is_tried(monkeypatch, tmp_path):
    """/api/estimate on a small-card install: the guard warns instead of refusing, and the page was handed the
    refusal's own sentence ("so it is refused") beside "fits": true. It gets the warning instead, and no reason."""

    card(monkeypatch, tmp_path, 16311, "NVIDIA GeForce RTX 5060 Ti")
    sys.path.insert(0, str(ROOT / "app"))
    import server

    monkeypatch.setattr(server, "SPARK", True)
    monkeypatch.setattr(server, "memory_of", lambda *_a, **_k: memory_guard.check(864, 480, 124, engine=False))
    said = server.estimate_time({"preset": "small", "quality": "fh4", "seconds": 5, "text_only": True})["memory"]
    assert said["fits"] is True and said["reason"] == ""
    assert "refused" not in said["warning"] and "It is tried anyway" in said["warning"]
    # a guard set back to strict refuses as before, with its reason and no warning
    monkeypatch.setenv("STUDIO_MEMORY_GUARD", "strict")
    strict = server.estimate_time({"preset": "small", "quality": "fh4", "seconds": 5, "text_only": True})["memory"]
    assert strict["fits"] is False and "so it is refused" in strict["reason"] and "warning" not in strict


def test_a_small_card_offers_the_other_builds_of_the_video_model(monkeypatch, tmp_path):
    """Another build of FastH3 or of Ref2VA in the diffusion_models folders (a 4-bit one, a hybrid) is offered by name
    where the guard warns, for the kind of clip its folder says; a strict guard (a DGX Spark) offers none, since its
    estimates are the regular files' measurements."""

    from fastapi import HTTPException

    card(monkeypatch, tmp_path, 16311, "NVIDIA GeForce RTX 5060 Ti")
    sys.path.insert(0, str(ROOT / "app"))
    import server

    models = tmp_path / "models"
    for path in (FASTH3_INT8, REF2VA_INT8, "FastH3-Comfy/diffusion_models/fasth3_omniref_int8.safetensors",
                 "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_w4a8_mixed.safetensors"):
        (models / path).parent.mkdir(parents=True, exist_ok=True)
        (models / path).write_bytes(b"x")
    monkeypatch.setattr(server, "SPARK", True)
    monkeypatch.setattr(server, "SPARK_REF2VA", models / "MiniMax-H3-Comfy")
    assert server.transformers() == {"fasth3_omniref_int8.safetensors": "frames", "minimax_h3_ref2va_pruned_w4a8_mixed.safetensors": "references"}
    w4a8 = "minimax_h3_ref2va_pruned_w4a8_mixed.safetensors"
    assert server.own_transformer({}) is None and server.own_transformer({"transformer": "regular"}) is None
    assert server.own_transformer({"transformer": w4a8, "references": ["a.png"]}) == w4a8
    assert server.own_transformer({"transformer": "fasth3_omniref_int8.safetensors"}) == "fasth3_omniref_int8.safetensors"
    for bad in ({"transformer": w4a8}, {"transformer": "nothing.safetensors"}, {"transformer": "../" + w4a8},
                {"transformer": w4a8, "references": ["a.png"], "loras": [{"id": "x"}]}):
        with pytest.raises(HTTPException):
            server.own_transformer(bad)
    monkeypatch.setenv("STUDIO_MEMORY_GUARD", "strict")
    assert server.transformers() == {}
