"""The studio's card as a profile (spark/gpu_profile.py): a GB300 (compute capability 10.3) renders on ComfyUI's own
blocks with PyTorch's attention and the fp16 video decoder, every other card keeps its defaults, and a variable that
is set wins over the profile. nvidia-smi is stood in for: no GPU is needed.
Run: STUDIO_HOME=$(mktemp -d) python3 -m pytest tests/test_gpu_profile.py"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import gpu_profile  # noqa: E402
import memory_guard  # noqa: E402

VARS = ("STUDIO_GPU_PROFILE", "SPARK_ENGINE", "SPARK_COMFY_ARGS", "SPARK_VAE", "STUDIO_GPU", "STUDIO_GPUS",
        "STUDIO_LOCAL_GPU_UUID", "CUDA_VISIBLE_DEVICES")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gpu_profile, "_SEEN", {})
    monkeypatch.setattr(memory_guard, "_CHOSEN", {"index": ""})


def card(monkeypatch, cap="12.1", name="NVIDIA GB10", old_driver=False):
    """nvidia-smi for one card: its compute capability and name (an older driver refuses the compute_cap field)."""

    asked = []

    def smi(*query):
        asked.append(query[0])
        if query[0] == "--query-gpu=compute_cap,name":
            return [] if old_driver else [[cap, name]]
        if query[0] == "--query-gpu=name":
            return [[name]]
        return []
    monkeypatch.setattr(memory_guard, "smi", smi)
    return asked


def test_compute_capability_10_3_is_a_gb300_and_nothing_else_is():
    assert gpu_profile.classify("10.3", "NVIDIA GB300") == "gb300"
    assert gpu_profile.classify("10.3") == "gb300"
    for cap, name in (("12.1", "NVIDIA GB10"), ("12.0", "NVIDIA RTX PRO 6000 Blackwell Workstation Edition"),
                      ("10.0", "NVIDIA B200"), ("9.0", "NVIDIA H100 80GB HBM3"), ("12.0", "NVIDIA GeForce RTX 5090")):
        assert gpu_profile.classify(cap, name) == "default", name
    # the capability decides; the name is read only when the driver gives no capability
    assert gpu_profile.classify("12.1", "NVIDIA GB300") == "default"
    assert gpu_profile.classify('Field "compute_cap" is not a valid field to query.', "NVIDIA GB300") == "gb300"
    assert gpu_profile.classify("", "NVIDIA GB10") == "default"
    assert gpu_profile.classify("", "") == "default"


def test_the_card_is_asked_once(monkeypatch):
    asked = card(monkeypatch, "10.3", "NVIDIA GB300")
    assert gpu_profile.profile() == "gb300" and gpu_profile.profile() == "gb300"
    assert asked == ["--query-gpu=compute_cap,name"]


def test_an_older_driver_is_read_by_the_name(monkeypatch):
    card(monkeypatch, name="NVIDIA GB300", old_driver=True)
    assert gpu_profile.profile() == "gb300"
    monkeypatch.setattr(gpu_profile, "_SEEN", {})
    card(monkeypatch, name="NVIDIA GB10", old_driver=True)
    assert gpu_profile.profile() == "default"


def test_no_nvidia_smi_is_the_default(monkeypatch):
    def gone(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(memory_guard.subprocess, "run", gone)
    assert gpu_profile.profile() == "default"


def test_studio_gpu_is_the_card_that_is_classified(monkeypatch):
    """A DGX Station's display card is nvidia-smi's first row: STUDIO_GPU names the GB300, and only it is asked."""

    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="10.3, NVIDIA GB300\n" if "-i" in argv and "1" in argv
                               else "12.0, NVIDIA RTX PRO 2000 Blackwell\n10.3, NVIDIA GB300\n")
    monkeypatch.setattr(memory_guard.subprocess, "run", run)
    monkeypatch.setenv("STUDIO_GPU", "1")
    assert gpu_profile.profile() == "gb300"
    assert seen[0][:3] == ["nvidia-smi", "-i", "1"]


def test_the_profile_can_be_named(monkeypatch):
    asked = card(monkeypatch, "12.1")
    monkeypatch.setenv("STUDIO_GPU_PROFILE", "gb300")
    assert gpu_profile.profile() == "gb300"
    card(monkeypatch, "10.3", "NVIDIA GB300")
    monkeypatch.setenv("STUDIO_GPU_PROFILE", "default")
    assert gpu_profile.profile() == "default"
    monkeypatch.setenv("STUDIO_GPU_PROFILE", "something else")  # not a profile: the card is asked
    assert gpu_profile.profile() == "gb300"
    assert asked == []


def test_a_gb300_renders_on_comfyuis_blocks_with_pytorch_attention_and_the_fp16_decoder(monkeypatch, tmp_path):
    card(monkeypatch, "10.3", "NVIDIA GB300")
    lib = tmp_path / "libtf_h3.so"
    lib.write_bytes(b"\0")
    assert gpu_profile.engine(lib) == "comfy"
    assert gpu_profile.engine(tmp_path / "missing.so") == "comfy"
    assert gpu_profile.comfy_args() == "--use-pytorch-cross-attention"
    assert gpu_profile.video_vae("minimax_h3_video_vae_int8_convrot.safetensors") == "minimax_h3_video_vae_fp16.safetensors"


def test_every_other_card_keeps_its_defaults(monkeypatch, tmp_path):
    for cap, name in (("12.1", "NVIDIA GB10"), ("12.0", "NVIDIA RTX PRO 6000 Blackwell Workstation Edition")):
        monkeypatch.setattr(gpu_profile, "_SEEN", {})
        card(monkeypatch, cap, name)
        lib = tmp_path / "libtf_h3.so"
        lib.write_bytes(b"\0")
        assert gpu_profile.engine(lib) == "tensorfold", name
        assert gpu_profile.engine(tmp_path / "missing.so") == "comfy", name
        assert gpu_profile.comfy_args() == "--use-ck-attention", name
        assert gpu_profile.video_vae("minimax_h3_video_vae_int8_convrot.safetensors") == "minimax_h3_video_vae_int8_convrot.safetensors"


def test_no_engine_built_asks_no_nvidia_smi(monkeypatch, tmp_path):
    asked = card(monkeypatch, "10.3", "NVIDIA GB300")
    assert gpu_profile.engine(tmp_path / "missing.so") == "comfy"
    assert asked == []


def test_a_variable_that_is_set_wins_over_the_profile(monkeypatch, tmp_path):
    lib = tmp_path / "libtf_h3.so"
    lib.write_bytes(b"\0")
    card(monkeypatch, "10.3", "NVIDIA GB300")
    monkeypatch.setenv("SPARK_ENGINE", "tensorfold")
    monkeypatch.setenv("SPARK_COMFY_ARGS", "--use-ck-attention --fast")
    monkeypatch.setenv("SPARK_VAE", "minimax_h3_video_vae_int8_convrot.safetensors")
    assert gpu_profile.engine(lib) == "tensorfold"
    assert gpu_profile.comfy_args() == "--use-ck-attention --fast"
    assert gpu_profile.video_vae("x.safetensors") == "minimax_h3_video_vae_int8_convrot.safetensors"
    monkeypatch.setattr(gpu_profile, "_SEEN", {})
    card(monkeypatch, "12.1")
    monkeypatch.setenv("SPARK_ENGINE", "comfy")
    monkeypatch.setenv("SPARK_COMFY_ARGS", "--use-pytorch-cross-attention")
    monkeypatch.setenv("SPARK_VAE", "minimax_h3_video_vae_fp16.safetensors")
    assert gpu_profile.engine(lib) == "comfy"
    assert gpu_profile.comfy_args() == "--use-pytorch-cross-attention"
    assert gpu_profile.video_vae("x.safetensors") == "minimax_h3_video_vae_fp16.safetensors"


def flags(**env) -> str:
    """The flags spark/comfy.sh starts ComfyUI with, from its own two FLAGS lines."""

    script = (ROOT / "spark" / "comfy.sh").read_text()
    lines = [line.strip() for line in script.splitlines() if line.strip().startswith("FLAGS=")]
    assert len(lines) == 2
    here = env.pop("HERE", str(ROOT / "spark"))
    base = {key: value for key, value in os.environ.items() if key not in VARS}
    said = subprocess.run(["bash", "-c", "\n".join(lines) + '\nprintf "%s" "$FLAGS"'], capture_output=True, text=True,
                          env={**base, "HERE": here, **env}, check=True)
    return said.stdout


def test_comfy_sh_starts_comfyui_with_the_cards_flags(tmp_path):
    assert flags(STUDIO_GPU_PROFILE="gb300") == "--use-pytorch-cross-attention"
    assert flags(STUDIO_GPU_PROFILE="default") == "--use-ck-attention"
    assert flags(STUDIO_GPU_PROFILE="gb300", SPARK_COMFY_ARGS="--use-ck-attention") == "--use-ck-attention"
    assert flags(HERE=str(tmp_path)) == "--use-ck-attention"  # no gpu_profile.py to ask: the GB10's flags


def test_the_command_line_says_the_profile():
    base = {key: value for key, value in os.environ.items() if key not in VARS}
    run = [sys.executable, str(ROOT / "spark" / "gpu_profile.py")]
    said = subprocess.run(run, capture_output=True, text=True, env={**base, "STUDIO_GPU_PROFILE": "gb300"})
    assert said.returncode == 0 and said.stdout.strip() == "gb300"
    said = subprocess.run([*run, "comfy-args"], capture_output=True, text=True, env={**base, "STUDIO_GPU_PROFILE": "default"})
    assert said.returncode == 0 and said.stdout.strip() == "--use-ck-attention"
    assert subprocess.run([*run, "nonsense"], capture_output=True, text=True, env=base).returncode == 2


def test_spark_generate_follows_the_profile(monkeypatch):
    import spark_generate as sg

    int8, fp16 = "minimax_h3_video_vae_int8_convrot.safetensors", "minimax_h3_video_vae_fp16.safetensors"
    monkeypatch.setattr(sg, "H3_VAE", int8)
    monkeypatch.setattr(sg, "MAC", False)
    listed = {"vae": [fp16, int8]}
    monkeypatch.setattr(sg, "installed", lambda kind, name: name in listed[kind])
    monkeypatch.setenv("STUDIO_GPU_PROFILE", "gb300")
    assert sg.video_vae() == fp16
    listed["vae"] = [int8]  # an install from before: the 8-bit decoder, and the clip still renders
    assert sg.video_vae() == int8
    monkeypatch.setattr(sg, "installed", lambda kind, name: (_ for _ in ()).throw(OSError("ComfyUI is not up")))
    assert sg.video_vae() == int8
    monkeypatch.setenv("STUDIO_GPU_PROFILE", "default")
    assert sg.video_vae() == int8
    # the engine a clip asks for when --engine is not given
    monkeypatch.setattr(sg, "ENGINE_LIB", Path(__file__))  # an engine is built
    monkeypatch.delenv("TEXT_ENCODER", raising=False)
    monkeypatch.setattr(sys, "argv", ["spark_generate.py", "video", "--prompt", "a walk", "-o", "clip.mp4"])
    got = {}
    monkeypatch.setattr(sg, "video", lambda args: got.update(engine=args.engine))
    for profile, engine in (("gb300", "comfy"), ("default", "tensorfold")):
        monkeypatch.setenv("STUDIO_GPU_PROFILE", profile)
        sg.main()
        assert got["engine"] == engine, profile
    monkeypatch.setenv("SPARK_ENGINE", "comfy")
    sg.main()
    assert got["engine"] == "comfy"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the CUDA build's estimates run on Linux only (server.SPARK)")
def test_the_apps_times_and_memory_check_follow_the_engine_used(monkeypatch):
    import server

    if not server.SPARK:
        pytest.skip("not the CUDA build")
    monkeypatch.setattr(server, "SPARK_ENGINE_LIB", Path(__file__))  # an engine is built
    monkeypatch.setattr(server, "card_face", lambda: False)  # the GB10's tables, so that they can be compared
    told = {}
    monkeypatch.setattr(memory_guard, "check", lambda *args, **kwargs: told.update(kwargs) or {"fits": True})
    monkeypatch.setattr(memory_guard, "card_room_applies", lambda: False)
    monkeypatch.setattr(server, "held_room", lambda: None)

    monkeypatch.setenv("STUDIO_GPU_PROFILE", "default")
    assert server.spark_engine()
    assert server.estimate("small", "fh8", 5, text_only=True) == server.SPARK_FASTH3_5S["fh8"]["small"]
    server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 121, False)
    assert told["engine"] is True

    monkeypatch.setenv("STUDIO_GPU_PROFILE", "gb300")
    assert not server.spark_engine()
    assert server.estimate("small", "fh8", 5, text_only=True) == server.SPARK_COMFY_5S["fh8"]["small"]
    server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 121, False)
    assert told["engine"] is False

    monkeypatch.setenv("SPARK_ENGINE", "tensorfold")  # set: it wins on a GB300 too
    assert server.spark_engine()
    monkeypatch.setenv("STUDIO_GPU_PROFILE", "default")
    monkeypatch.setenv("SPARK_ENGINE", "comfy")  # and on a GB10, where the app used to ignore it
    assert not server.spark_engine()
