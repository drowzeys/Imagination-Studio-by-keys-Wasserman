"""The native Windows CUDA tier (app/windows_cuda.py, spark/windows_scripts.py, spark/comfy_win.py): chosen only with the
full install, the Spark's commands translated for it, the bash scripts' arguments kept, refusals said. Model-free: no
GPU, no ComfyUI, on any platform (the Windows-only parts are read, or skipped off Windows)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_cuda  # noqa: E402
import windows_scripts  # noqa: E402
import memory_guard  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake full install under a LOCALAPPDATA of its own, the platform said to be Windows."""

    base = tmp_path / "ImaginationStudio"
    for part in ("gpu-venv/Scripts/python.exe", "ComfyUI/main.py", "tf-h3/tf_h3.dll"):
        (base / part).parent.mkdir(parents=True, exist_ok=True)
        (base / part).write_bytes(b"x")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("STUDIO_WINDOWS_TIER", raising=False)
    monkeypatch.delenv("STUDIO_CUDA_TIER", raising=False)
    monkeypatch.setattr(windows_cuda.sys, "platform", "win32")
    monkeypatch.setattr(windows_cuda, "_STATE", {})

    def record(**more):
        data = {"version": 1, "cuda_pool_mb": 113103, "models": str(tmp_path / "models"), **more}
        (base / "cuda-tier.json").write_text(json.dumps(data))
        return base
    return record


def test_off_windows_the_tier_is_never_chosen_and_reads_nothing(monkeypatch):
    monkeypatch.setattr(windows_cuda.sys, "platform", "linux")
    monkeypatch.setattr(windows_cuda, "_STATE", {})
    monkeypatch.setattr(windows_cuda, "config", lambda: pytest.fail("read the install record off Windows"))
    assert windows_cuda.active() is False
    assert windows_cuda.why_not() == "not Windows"


def test_the_full_install_and_a_large_pool_choose_the_native_tier(install, monkeypatch):
    install()
    for key in ("PREFIX", "SPARK_MODELS", "TF_H3_LIB", "STUDIO_WINDOWS_CUDA", "BOLT_BIN"):
        monkeypatch.delenv(key, raising=False)
    assert windows_cuda.why_not() == ""
    assert windows_cuda.active()
    import os
    assert os.environ["STUDIO_WINDOWS_CUDA"] == "1" and os.environ["TF_H3_LIB"].endswith("tf_h3.dll")
    assert os.environ["SPARK_MODELS"].endswith("models")


@pytest.mark.parametrize("what, why", [
    ("pool", "under 90000 MB"), ("dll", "tf_h3.dll"), ("comfy", "no ComfyUI"), ("record", "cuda-tier.json"), ("preview", "preview")])
def test_anything_short_of_the_full_install_keeps_the_preview_tier(install, monkeypatch, what, why):
    base = install(cuda_pool_mb=36000 if what == "pool" else 113103)
    if what == "dll":
        (base / "tf-h3/tf_h3.dll").unlink()
    if what == "comfy":
        (base / "ComfyUI/main.py").unlink()
    if what == "record":
        (base / "cuda-tier.json").unlink()
    if what == "preview":
        monkeypatch.setenv("STUDIO_WINDOWS_TIER", "preview")
    assert why in windows_cuda.why_not()
    assert not windows_cuda.active()


def test_the_spark_commands_become_their_native_ports(monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_PYTHON", r"C:\venv\python.exe")
    out = windows_cuda.translate(["bash", str(ROOT / "scripts/video.sh"), "a prompt", "out.mp4"])
    assert out[0] == r"C:\venv\python.exe" and out[-4:] == [str(ROOT / "spark/windows_scripts.py"), "video", "a prompt", "out.mp4"]
    for script, command in (("studio.sh", "studio"), ("scout.sh", "scout"), ("image.sh", "image"), ("edit.sh", "edit")):
        assert windows_cuda.translate(["bash", str(ROOT / "scripts" / script), "x"])[-2] == command
    assert windows_cuda.translate(["python3", "app/hlg.py", "run"]) == [r"C:\venv\python.exe", "app/hlg.py", "run"]
    assert windows_cuda.translate(["/home/spark/.local/opt/tensorfold-studio/venv/bin/python", "s.py"])[0] == r"C:\venv\python.exe"
    assert windows_cuda.translate([r"C:\ffmpeg.exe", "-i"]) == [r"C:\ffmpeg.exe", "-i"]
    with pytest.raises(RuntimeError, match="turnaround.sh has no native Windows port"):
        windows_cuda.translate(["bash", str(ROOT / "scripts/turnaround.sh"), "a.png"])


def test_what_is_not_native_is_refused_with_its_reason():
    for kind in ("video", "studio", "long", "scout", "edit", "music", "sfx", "voice", "export", "graphics"):
        assert windows_cuda.unsupported(kind) == ""
    for kind in ("turnaround", "upscale", "mask", "remove", "swap", "train", "finish", "foley"):
        said = windows_cuda.unsupported(kind)
        assert said.startswith("Not available on native Windows yet") and "WSL" in said


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(windows_scripts, "generate", lambda script, args, extra=None: calls.append((script, args, extra or {})))
    for name in ("WIDTH", "HEIGHT", "FRAMES", "SEED", "STEPS", "FIRST_FRAME", "LAST_FRAME", "LORA", "REF_IMAGES", "REF_VIDEOS",
                 "REF_AUDIOS", "PROMPT_FILE", "SPARK_BASE", "IMAGE_MODEL", "EXTRA", "TURBO", "IMAGE_FILE", "RAW_PROMPT",
                 "DELIVERY_AUDIO_RATE", "SPARK_ENGINE", "TEXT_ENCODER", "CROP", "SPARK_SPARSITY", "ENGINE", "X2"):
        monkeypatch.delenv(name, raising=False)
    return calls


def test_video_keeps_spark_video_shs_arguments(ran, monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    first.write_bytes(b"png")
    monkeypatch.setenv("FIRST_FRAME", str(first))
    monkeypatch.setenv("FRAMES", "124")
    monkeypatch.setenv("SEED", "42")
    monkeypatch.setenv("LORA", "a.safetensors:0.8\nb.safetensors:1\n")
    windows_scripts.video(["A boat at dawn.", "out.mp4"])
    script, args, _ = ran[0]
    assert script == "spark_generate.py"
    assert args[:10] == ["video", "-o", "out.mp4", "--width", "864", "--height", "480", "--frames", "124", "--seed"]
    assert args[10:12] == ["42", "--steps"] and args[12] == "8"
    assert ["--first-frame", str(first)] == args[13:15]
    assert args.count("--lora") == 2
    # the ambient line, as scripts/quiet-prompt.sh adds it to a prompt with no spoken line
    assert args[-2] == "--prompt" and "overall_soundscape:" in args[-1] and args[-1].startswith("integrated_multimodal_description")


def test_video_refuses_what_the_spark_script_refuses(ran, monkeypatch):
    monkeypatch.setenv("X2", "1")
    with pytest.raises(SystemExit):
        windows_scripts.video(["x", "out.mp4"])
    monkeypatch.delenv("X2")
    monkeypatch.setenv("REF_AUDIOS", "a.wav")
    with pytest.raises(SystemExit):
        windows_scripts.video(["x", "out.mp4"])
    assert ran == []


def test_scout_runs_one_image_load_for_its_seeds(ran, monkeypatch, tmp_path):
    from PIL import Image

    for seed in (3, 4):
        Image.new("RGB", (64, 36)).save(tmp_path / f"scout_s{seed}.png")
    monkeypatch.setenv("SEED", "3")
    windows_scripts.scout(["a picture", "2", str(tmp_path)])
    _, args, _ = ran[0]
    assert args[:3] == ["image", "-o", str(tmp_path / "scout.png")] and args[-2:] == ["--seeds", "3,4"]
    assert (tmp_path / "sheet.jpg").is_file()


def test_qwen_full_is_the_base_model_as_image_sh_runs_it(ran, monkeypatch):
    monkeypatch.setenv("IMAGE_MODEL", "qwen-full")
    windows_scripts.image(["a picture", "out.png"])
    _, args, extra = ran[0]
    assert "--base" in args and args[args.index("--steps") + 1] == "40" and extra == {"IMAGE_MODEL": "qwen"}


def test_the_comfy_port_refuses_to_run_off_windows():
    import comfy_win

    if sys.platform != "win32":
        assert comfy_win.main(["status"]) == 2


def test_spark_generate_uses_the_port_only_on_windows(monkeypatch):
    import spark_generate

    if sys.platform == "win32":
        assert spark_generate.comfy_control("stop")[-2:] == [str(ROOT / "spark/comfy_win.py"), "stop"]
    else:
        assert spark_generate.comfy_control("stop") == ["bash", str(spark_generate.COMFY_SH), "stop"]


def test_the_memory_guard_reads_windows_memory_only_on_the_native_tier(monkeypatch):
    monkeypatch.setattr(memory_guard.sys, "platform", "win32")
    monkeypatch.setattr(memory_guard, "windows_meminfo", lambda: {"MemTotal": 31000, "MemAvailable": 20000, "MemFree": 20000})
    monkeypatch.delenv("STUDIO_WINDOWS_CUDA", raising=False)
    monkeypatch.setattr(memory_guard, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no /proc")), raising=False)
    assert memory_guard.meminfo() == {}  # the preview tier: as before
    monkeypatch.setenv("STUDIO_WINDOWS_CUDA", "1")
    assert memory_guard.meminfo()["MemAvailable"] == 20000


def test_the_installer_builds_from_the_pinned_branch_and_records_the_tier():
    text = (ROOT / "scripts/setup-windows-cuda.ps1").read_text()
    for words in ("zig build tf-h3-int8", "zig build native bolt", "a545f7867cd82feab630a2d015086da602f53347", "fish-drama3-generate", "windows-arm64",
                  "sha256", "vcvarsall", "setup-windows-comfy.ps1", "setup-windows-gpu.ps1", "windows-cuda-setup.py"):
        assert words in text, words
    one = (ROOT / "oneshot-setup-rtx-spark-n1x.ps1").read_text()
    assert "-Native" in one and "setup-windows-cuda.ps1" in one
    helper = (ROOT / "scripts/windows-cuda-setup.py").read_text()
    assert "cuda_pool_mb" in helper and "extra_model_paths.yaml" in helper


@pytest.mark.skipif(sys.platform != "win32", reason="the job object's flags are Windows'")
def test_a_breakaway_job_is_still_killed_on_close():
    from windows_process import OwnedJob

    job = OwnedJob(breakaway=True)
    job.close()
