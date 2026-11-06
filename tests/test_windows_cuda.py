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


@pytest.mark.parametrize("what, core_ready, why", [
    ("pool", True, "90000 MB"), ("dll", True, "tf_h3.dll"), ("comfy", False, "no ComfyUI"),
    ("record", True, "no valid pool measurement"), ("preview", False, "preview")])
def test_native_core_does_not_depend_on_h3_installation_or_capacity(install, monkeypatch, what, core_ready, why):
    base = install(cuda_pool_mb=36000 if what == "pool" else 113103)
    if what == "dll":
        (base / "tf-h3/tf_h3.dll").unlink()
    if what == "comfy":
        (base / "ComfyUI/main.py").unlink()
    if what == "record":
        (base / "cuda-tier.json").unlink()
    if what == "preview":
        monkeypatch.setenv("STUDIO_WINDOWS_TIER", "preview")
    assert (not windows_cuda.why_not()) == core_ready
    assert why in windows_cuda.h3_why_not()


def test_the_spark_commands_become_their_native_ports(monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_PYTHON", r"C:\venv\python.exe")
    out = windows_cuda.translate(["bash", str(ROOT / "scripts/video.sh"), "a prompt", "out.mp4"])
    assert out[0] == r"C:\venv\python.exe" and out[-4:] == [str(ROOT / "spark/windows_scripts.py"), "video", "a prompt", "out.mp4"]
    for script, command in (("studio.sh", "studio"), ("scout.sh", "scout"), ("image.sh", "image"), ("edit.sh", "edit")):
        assert windows_cuda.translate(["bash", str(ROOT / "scripts" / script), "x"])[-2] == command
    assert windows_cuda.translate(["python3", "app/hlg.py", "run"]) == [r"C:\venv\python.exe", "app/hlg.py", "run"]
    assert windows_cuda.translate(["/home/spark/.local/opt/tensorfold-studio/venv/bin/python", "s.py"])[0] == r"C:\venv\python.exe"
    assert windows_cuda.translate([r"C:\ffmpeg.exe", "-i"]) == [r"C:\ffmpeg.exe", "-i"]
    out = windows_cuda.translate(["bash", str(ROOT / "scripts/turnaround.sh"), "a.png", "folder/turn"])
    assert out[-4:] == [str(ROOT / "spark/windows_scripts.py"), "turnaround", "a.png", "folder/turn"]
    with pytest.raises(RuntimeError, match="fast.sh has no native Windows port"):
        windows_cuda.translate(["bash", str(ROOT / "scripts/fast.sh"), "a.png"])
    # musubi-tuner's own venv (spark/train_lora.py's commands) is the tier's train\venv
    trained = windows_cuda.translate(["/home/spark/.local/opt/tensorfold-studio/train/venv/bin/python", "x.py"])
    assert trained[0].replace("\\", "/").endswith("train/venv/Scripts/python.exe") and trained[1:] == ["x.py"]


def test_every_spark_job_kind_runs_natively_and_an_unported_one_is_refused():
    for kind in ("video", "studio", "long", "scout", "edit", "music", "sfx", "voice", "export", "graphics",
                 "turnaround", "upscale", "mask", "remove", "swap", "train", "finish", "foley"):
        assert windows_cuda.unsupported(kind) == "", kind
    said = windows_cuda.unsupported("something-new")
    assert said.startswith("Not available on Windows yet") and "WSL" not in said
    assert windows_cuda.NOT_NATIVE == {}


def test_turnaround_keeps_turnaround_shs_arguments(ran, monkeypatch, tmp_path):
    picture, lora = tmp_path / "subject.png", tmp_path / "turnaround.safetensors"
    picture.write_bytes(b"png")
    lora.write_bytes(b"lora")
    for name, value in (("SIZE", "512"), ("STRENGTH", "0.7"), ("SEED", "3"), ("TURNAROUND_LORA", str(lora))):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PROMPT", raising=False)
    windows_scripts.turnaround([str(picture), "out/turn"])
    script, args, _ = ran[0]
    assert script == "spark_workflows.py" and args[:3] == ["turnaround", "--ref", str(picture)]
    assert args[args.index("--lora") + 1] == f"{lora}:0.7" and args[args.index("--size") + 1] == "512"
    assert args[args.index("--steps") + 1] == "28" and args[-2:] == ["-o", "out/turn"]
    assert "<Picture 1>" in args[args.index("--prompt") + 1]
    monkeypatch.setenv("SIZE", "640")
    with pytest.raises(SystemExit):
        windows_scripts.turnaround([str(picture), "out/turn"])


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
    for words in ("zig build tf-h3-int8", "zig build native bolt", "1c8bdbfd7ea58fdb110870c3ff17d065522b65d2", "fish-drama3-generate",
                  "zig build music3-generate", "windows-arm64", "sha256", "vcvarsall", "setup-windows-comfy.ps1",
                  "setup-windows-gpu.ps1", "windows-cuda-setup.py", "setup-windows-finish.ps1"):
        assert words in text, words
    one = (ROOT / "oneshot-setup-rtx-spark-n1x.ps1").read_text()
    assert "-Native" in one and "setup-windows-cuda.ps1" in one
    helper = (ROOT / "scripts/windows-cuda-setup.py").read_text()
    assert "cuda_pool_mb" in helper and "extra_model_paths.yaml" in helper
    assert "1c8bdbfd7ea58fdb110870c3ff17d065522b65d2" in helper


def _setup_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("windows_cuda_setup", ROOT / "scripts/windows-cuda-setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("isolated", [False, True])
def test_node_restore_uses_the_gpu_wheel_manifest_and_preserves_installed_versions(tmp_path, monkeypatch, isolated):
    from importlib import metadata
    from types import SimpleNamespace
    import subprocess

    setup = _setup_module()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    node = tmp_path / "ComfyUI" / "custom_nodes" / "ComfyUI-MMAudio"
    node.mkdir(parents=True)
    (node / "requirements.txt").write_text("torchdiffeq>=0.2.3\n")
    installed = SimpleNamespace(metadata={"Name": "torch"}, version="2.14.0+cu134")
    monkeypatch.setattr(metadata, "distributions", lambda: [installed])

    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "rev-parse" in command:
            return SimpleNamespace(stdout=setup.NODE_PACKS["foley"][0][2], returncode=0)
        if "install" in command:
            constraint = Path(command[command.index("--constraint") + 1])
            assert constraint.read_text().strip() == "torch==2.14.0+cu134"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    destination = str(tmp_path / "isolated") if isolated else ""
    assert setup.nodes(SimpleNamespace(parts="foley", target=destination, pip_python="authenticated-python")) == 0
    command = next(command for command in calls if "install" in command)
    assert command[0] == "authenticated-python"
    manifests = [command[i + 1] for i, value in enumerate(command) if value == "-r"]
    assert str(ROOT / "app" / "requirements-windows-gpu.txt") in manifests
    assert "-q" not in command
    assert ("--target" in command) == isolated
    assert any("check" in command for command in calls) == isolated


@pytest.mark.parametrize("repaired", [True, False])
def test_reused_isolated_node_environment_repairs_and_rechecks_missing_dependencies(tmp_path, monkeypatch, repaired):
    from importlib import metadata
    from types import SimpleNamespace
    import subprocess

    setup = _setup_module()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    node = tmp_path / "ComfyUI" / "custom_nodes" / "ComfyUI-MMAudio"
    node.mkdir(parents=True)
    (node / "requirements.txt").write_text("torchdiffeq>=0.2.3\n")
    monkeypatch.setattr(metadata, "version", lambda name: "0.2.5")
    monkeypatch.setattr(metadata, "distributions", lambda: [])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "rev-parse" in command:
            return SimpleNamespace(stdout=setup.NODE_PACKS["foley"][0][2], returncode=0)
        if "install" in command:
            return SimpleNamespace(returncode=0)
        assert command[-2:] == ["pip", "check"]
        return SimpleNamespace(returncode=0 if repaired and any("install" in call for call in calls) else 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert setup.nodes(SimpleNamespace(parts="foley", target=str(tmp_path / "isolated"))) == (0 if repaired else 1)
    assert sum("install" in command for command in calls) == 1
    assert sum(command[-2:] == ["pip", "check"] for command in calls) == 2


def test_the_native_installer_pins_what_the_spark_scripts_pin():
    """windows-cuda-setup.py's node packs, files and checksums are the Spark's (setup-workflows.sh, setup-swap.sh,
    setup-train.sh, oneshot-setup-cuda.sh --sound): the same commits and the same SHA-256s, never a second opinion."""

    setup = _setup_module()
    shell = "".join((ROOT / one).read_text() for one in ("spark/setup-workflows.sh", "spark/setup-swap.sh", "spark/setup-train.sh",
                                                          "oneshot-setup-cuda.sh"))
    for packs in setup.NODE_PACKS.values():
        for folder, repo, commit in packs:
            assert repo in shell and commit in shell, folder
    for rows in setup.CHECKED.values():
        for repo, revision, name, folder, as_name, digest in rows:
            assert repo in shell and name.rsplit("/", 1)[-1] in shell, name
            assert not digest or digest in shell, name
            assert revision == "main" or revision in shell, name
    train = (ROOT / "app/train.py").read_text()
    assert all(one in train for one in ("Train-H3/vae/minimax_h3_video_vae_fp16.safetensors", "Train-H3/adapters/ostris_dd_adapter_v2.safetensors"))


def test_the_finish_setup_pins_the_releases_finish_py_names():
    text = (ROOT / "scripts/setup-windows-finish.ps1").read_text()
    sys.path.insert(0, str(ROOT / "app"))
    import finish

    assert f'$TsrctVersion = "{finish.TSRCT_VERSION}"' in text and f'$ConvVersion = "{finish.CONVERTER_VERSION}"' in text
    assert "windows-x86_64" in text and "CHECKSUMS.sha256" in text and "manifest.json" in text
    linux = (ROOT / "scripts/setup-finish.sh").read_text()
    for digest in ("498efd461f6ddfcb7a111bf9a565709d2085d48201d501ead960d93e84ffbb88", "73894e0448cae90a92b6c2f8732b7bb9acb7b94c418bff559dad4a18e1de9659"):
        assert digest in text and digest in linux


@pytest.mark.skipif(sys.platform != "win32", reason="the job object's flags are Windows'")
def test_a_breakaway_job_is_still_killed_on_close():
    from windows_process import OwnedJob

    job = OwnedJob(breakaway=True)
    job.close()
