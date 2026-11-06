"""The native Windows CUDA tier's install steps that are easier in Python than in PowerShell (run with the GPU venv's
python by scripts/setup-windows-cuda.ps1):

    python scripts/windows-cuda-setup.py models --models C:\\Studio\\models --parts video,image,music
    python scripts/windows-cuda-setup.py config --models C:\\Studio\\models [--bolt-model DIR --bolt-draft DIR]
    python scripts/windows-cuda-setup.py nodes --parts workflows,foley
    python scripts/windows-cuda-setup.py train

``nodes`` puts the authors' ComfyUI node packs the Library's actions and foley run (spark/setup-workflows.sh and
oneshot-setup-cuda.sh --sound) into ComfyUI's custom_nodes at the same pinned commits, and installs a pack's
requirements only where they ADD packages to the GPU venv (every installed package is held at its version, as
oneshot-setup-cuda.sh does it).

``models`` downloads what oneshot-setup-cuda.sh's ``fetch`` lines download, the same files from the same repos into the
same folders under the model folder (a file already there, not empty, is kept). Gated repos (FLUX3-K, Stable Audio Open)
use the Hugging Face token the setup saved (huggingface_hub reads it). ``config`` writes ComfyUI's
extra_model_paths.yaml (the DGX Spark's list of folders) and cuda-tier.json, the record app/windows_cuda.py reads,
with the CUDA pool measured here (torch.cuda.mem_get_info): the Studio chooses the native tier only from it.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import model_catalog

ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ImaginationStudio"
TENSORFOLD = {"repo": "https://github.com/drowzeys/TensorFold.git", "branch": "studio-1.2.2",
              "commit": "1c8bdbfd7ea58fdb110870c3ff17d065522b65d2"}
# (repo, folder under the model folder, files): oneshot-setup-cuda.sh's fetch lines, part by part
PARTS = model_catalog.parts()
# files checked against a SHA-256 (spark/setup-workflows.sh, spark/setup-swap.sh, spark/setup-train.sh: the same pins;
# tests/test_windows_cuda.py holds the two lists in step): (repo, revision, file in it, folder under the model folder,
# name there, sha256 or "")
CHECKED = model_catalog.checked_downloads()
# the authors' ComfyUI node packs, at the commits spark/setup-workflows.sh and oneshot-setup-cuda.sh pin
NODE_PACKS = {
    "workflows": [("ComfyUI-H3-ContactSheet", "https://github.com/matlowai/ComfyUI-H3-ContactSheet", "3c8c8c66e69362e5abf9cf4c4e693baddab90ca3"),
                  ("ComfyUI-H3-Video-Upsampler", "https://github.com/dntpi/ComfyUI-H3-Video-Upsampler", "36cb612ec3df30094eeb4fec66f1528925ebba73"),
                  ("h3-relay", "https://github.com/akatz-ai/h3-relay", "cf489b7f2472fdf33866fae36de49aea03ef464f")],
    "foley": [("ComfyUI-MMAudio", "https://github.com/kijai/ComfyUI-MMAudio", "8eaeb72edc3aaf2059b57f2d96a1f6f689f19ae2")],
}
# NVIDIA's checkpoints for the Bolt brain, at the revisions app/bolt.py names (whole repos)
BOLT = {key: value for key, value in model_catalog.checkpoints().items() if key != "drama-3"}
# ComfyUI's folders, as oneshot-setup-cuda.sh writes them on a DGX Spark
YAML = """studio:
  base_path: {base}
  diffusion_models: |
    FastH3-Comfy/diffusion_models
    Qwen-Image-2.1-Comfy/diffusion_models
    MiniMax-H3-Comfy/diffusion_models
    FLUX3-K-Comfy/diffusion_models
    MOSS-Image-Comfy/diffusion_models
    MiniMax-Music3-Comfy/diffusion_models
  text_encoders: |
    FastH3-Comfy/text_encoders
    Qwen-Image-2.1-Comfy/text_encoders
    FLUX3-K-Comfy/text_encoders
    MOSS-Image-Comfy/text_encoders
    MiniMax-Music3-Comfy/text_encoders
    Sound-Comfy/text_encoders
  vae: |
    FastH3-Comfy/vae
    Qwen-Image-2.1-Comfy/vae
    FLUX3-K-Comfy/vae
    MOSS-Image-Comfy/vae
    MiniMax-Music3-Comfy/vae
  loras: |
    loras
    MiniMax-H3-Comfy/loras
  checkpoints: |
    Workflows-Comfy/checkpoints
    Sound-Comfy/checkpoints
  latent_upscale_models: |
    Workflows-Comfy/latent_upscale_models
  mmaudio: |
    Sound-Comfy/mmaudio
"""


def say(text: str) -> None:
    print(f"  {text}", flush=True)


def link(source: Path, target: Path) -> None:
    """Stable Audio Open under the names ComfyUI's loaders list: a hard link (a copy across drives)."""

    if target.is_file() and target.stat().st_size == source.stat().st_size:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(16 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def checked(base: Path, rows: list) -> int:
    """CHECKED's files into the model folder: a file already there (not empty) is kept, a new one is checked against
    its SHA-256 before it takes its name. Returns how many did not arrive."""

    from huggingface_hub import hf_hub_download

    failed = 0
    for repo, revision, name, folder, as_name, digest in rows:
        target = base / folder / as_name
        if target.is_file() and target.stat().st_size:
            say(f"ok {folder}/{as_name}")
            continue
        say(f"downloading {repo} {name} -> {target}")
        scratch = base / folder / ".download"
        try:
            got = Path(hf_hub_download(repo, name, revision=revision, local_dir=scratch))
        except Exception as error:  # noqa: BLE001 - said, the rest goes on
            failed += 1
            print(f"  ! {repo} {name} did not download: {str(error).splitlines()[0][:300]}", file=sys.stderr)
            continue
        if digest and sha256(got) != digest:
            got.unlink(missing_ok=True)
            failed += 1
            print(f"  ! {as_name}: checksum mismatch, not kept", file=sys.stderr)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(got, target)
        shutil.rmtree(scratch, ignore_errors=True)
    return failed


def models(args) -> int:
    from huggingface_hub import hf_hub_download, snapshot_download

    base = Path(args.models)
    failed = 0
    for part in [p.strip() for p in args.parts.split(",") if p.strip()]:
        if part in CHECKED:
            failed += checked(base, CHECKED[part])
            continue
        if part == "finish":  # Tesseract is a program, not a model: scripts\\setup-windows-finish.ps1
            continue
        if part == "bolt":
            for folder, (repo, revision) in BOLT.items():
                target = base / folder
                if (target / "config.json").is_file() and ((target / "model.safetensors.index.json").is_file() or (target / "model.safetensors").is_file()):
                    say(f"ok {folder}")
                    continue
                say(f"downloading {repo}@{revision[:8]} -> {target}")
                snapshot_download(repo, revision=revision, local_dir=target)
            continue
        if part == "voice":  # Fish Audio Drama 3 (Fish Audio Community License), and its codec converted once for the engine
            target = base / "drama-3"
            if not (target / "model.safetensors.index.json").is_file():
                say(f"downloading fishaudio/drama-3 -> {target}")
                snapshot_download("fishaudio/drama-3", local_dir=target)
            if not (target / "codec-v1.safetensors").is_file() and (target / "codec.pth").is_file():
                import subprocess
                subprocess.run([sys.executable, "-I", str(Path(__file__).resolve().parents[1] / "spark" / "drama3_codec.py"),
                                str(target), str(target / "codec-v1.safetensors")], check=False)
            continue
        if part not in PARTS:
            print(f"unknown part {part}: one of {', '.join([*PARTS, *CHECKED, 'bolt', 'voice'])}", file=sys.stderr)
            return 2
        for repo, folder, files in PARTS[part]:
            for name in files:
                target = base / folder / name
                if target.is_file() and target.stat().st_size:
                    say(f"ok {folder}/{name}")
                    continue
                say(f"downloading {repo} {name} -> {base / folder}")
                try:
                    hf_hub_download(repo, name, local_dir=base / folder)
                except Exception as error:  # noqa: BLE001 - a gated repo without access: said, the rest goes on
                    failed += 1
                    print(f"  ! {repo} {name} did not download: {str(error).splitlines()[0][:300]}", file=sys.stderr)
        if part == "sound":
            sao = base / "Sound-Comfy" / "stable-audio-open-1.0"
            if (sao / "model.safetensors").is_file() and (sao / "text_encoder" / "model.safetensors").is_file():
                link(sao / "model.safetensors", base / "Sound-Comfy" / "checkpoints" / "stable_audio_open_1.0.safetensors")
                link(sao / "text_encoder" / "model.safetensors", base / "Sound-Comfy" / "text_encoders" / "stable_audio_open_t5_base.safetensors")
    return 1 if failed else 0


def nodes(args) -> int:
    """NODE_PACKS into ComfyUI's custom_nodes at their commits, and a pack's requirements where they only add packages."""

    import subprocess
    import tempfile
    from importlib import metadata

    custom = ROOT / "ComfyUI" / "custom_nodes"
    changed = False
    if not custom.is_dir():
        print(f"no ComfyUI at {custom.parent}: run scripts\\setup-windows-comfy.ps1 first", file=sys.stderr)
        return 1
    for part in [p.strip() for p in args.parts.split(",") if p.strip()]:
        if part not in NODE_PACKS:
            print(f"unknown part {part}: one of {', '.join(NODE_PACKS)}", file=sys.stderr)
            return 2
        for folder, repo, commit in NODE_PACKS[part]:
            target = custom / folder
            head = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            if head != commit:
                if not (target / ".git").is_dir():
                    subprocess.run(["git", "clone", "-q", repo, str(target)], check=True)
                if subprocess.run(["git", "-C", str(target), "fetch", "-q", "origin", commit]).returncode:
                    subprocess.run(["git", "-C", str(target), "fetch", "-q", "origin"], check=True)
                subprocess.run(["git", "-C", str(target), "-c", "advice.detachedHead=false", "checkout", "-q", commit], check=True)
                changed = True
            say(f"ok {folder} @ {commit[:8]}")
            wanted = target / "requirements.txt"
            if not wanted.is_file():
                continue
            destination = getattr(args, "target", "")
            if destination:
                from packaging.requirements import Requirement
                satisfied = True
                for line in wanted.read_text(encoding="utf-8").splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    requirement = Requirement(line)
                    if requirement.marker and not requirement.marker.evaluate():
                        continue
                    try:
                        version = metadata.version(requirement.name)
                    except metadata.PackageNotFoundError:
                        satisfied = False
                        break
                    if version not in requirement.specifier:
                        satisfied = False
                        break
                if satisfied and subprocess.run([sys.executable, "-X", "utf8", "-m", "pip", "check"]).returncode == 0:
                    say(f"ok {folder}'s requirements (isolated runtime reused)")
                    continue
            # every package already in the GPU venv held at its version: a pack only adds what it lacks
            held = sorted({f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata["Name"]})
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as hold:
                hold.write("\n".join(held) + "\n")
            try:
                command = [getattr(args, "pip_python", "") or sys.executable, "-X", "utf8", "-m", "pip", "install",
                           "-r", str(wanted),
                           "-r", str(Path(__file__).resolve().parents[1] / "app" / "requirements-windows-gpu.txt"),
                           "--constraint", hold.name]
                if destination:
                    command += ["--target", destination, "--ignore-installed", "--upgrade"]
                done = subprocess.run(command)
            finally:
                os.unlink(hold.name)
            if done.returncode:
                print(f"  ! {folder}'s dependency installation failed; see pip's error above", file=sys.stderr)
                return 1
            say(f"ok {folder}'s requirements (only added, nothing moved)")
    if getattr(args, "target", ""):
        if subprocess.run([sys.executable, "-X", "utf8", "-m", "pip", "check"]).returncode:
            print("  ! The isolated node environment has inconsistent dependencies; setup did not pass", file=sys.stderr)
            return 1
    if changed:  # a running ComfyUI read its nodes when it started: stopped, the next render starts it with them
        subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve().parents[1] / "spark" / "comfy_win.py"), "stop"],
                       check=False)
    return 0


def train_env(args) -> int:
    """Looks -> Train a LoRA natively: kohya-ss's musubi-tuner at the commit app/train.py pins, in a venv of its own
    (train\\venv: Python 3.13 ARM64, NVIDIA's PyTorch for CUDA 13.4 as the GPU venv has it, musubi-tuner's pinned
    requirements; its pyproject leaves opencv out on Windows on Arm and uses its own Pillow fallback), and the optimizer
    that runs here: bitsandbytes' 8-bit AdamW when it runs a step on this GPU, else the plain AdamW (spark/setup-train.sh's
    test)."""

    import subprocess

    import re
    from types import SimpleNamespace

    pack = Path(__file__).resolve().parents[1]
    # app/train.py's pin, read from the file (it imports the app's web framework, which this venv does not have)
    pinned = re.search(r'^MUSUBI_COMMIT = "([0-9a-f]{40})"', (pack / "app" / "train.py").read_text(encoding="utf-8"), re.M)
    train = SimpleNamespace(MUSUBI_COMMIT=pinned.group(1))

    root = ROOT / "train"
    musubi, venv = root / "musubi-tuner", root / "venv"
    python = venv / "Scripts" / "python.exe"
    root.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(musubi), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != train.MUSUBI_COMMIT:
        if not (musubi / ".git").is_dir():
            subprocess.run(["git", "clone", "-q", "https://github.com/kohya-ss/musubi-tuner", str(musubi)], check=True)
        if subprocess.run(["git", "-C", str(musubi), "fetch", "-q", "origin", train.MUSUBI_COMMIT]).returncode:
            subprocess.run(["git", "-C", str(musubi), "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(musubi), "-c", "advice.detachedHead=false", "checkout", "-q", train.MUSUBI_COMMIT], check=True)
    say(f"ok musubi-tuner @ {train.MUSUBI_COMMIT[:7]} (Apache License 2.0)")
    if not python.is_file():
        subprocess.run(["py", "-V:3.13-arm64", "-m", "venv", str(venv)], check=True)
    pip = [str(python), "-X", "utf8", "-m", "pip", "install", "-q"]
    done = subprocess.run([str(python), "-c", "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"], capture_output=True)
    if done.returncode:
        subprocess.run(pip + ["-r", str(pack / "app" / "requirements-windows-gpu.txt")], check=True)
    subprocess.run(pip + ["-e", str(musubi)], check=True)
    subprocess.run([str(python), "-c", "import musubi_tuner, accelerate, diffusers, transformers, av"], check=True)
    if not (venv / "Scripts" / "accelerate.exe").is_file():
        print(f"  ! no accelerate.exe in {venv}", file=sys.stderr)
        return 1
    test = ("import torch, bitsandbytes as bnb\n"
            "p = torch.nn.Parameter(torch.ones(4096, device='cuda'))\n"
            "opt = bnb.optim.AdamW8bit([p], lr=1e-3)\n"
            "p.grad = torch.ones_like(p)\n"
            "opt.step(); torch.cuda.synchronize()\n"
            "assert torch.isfinite(p).all()\n")
    optimizer = "adamw8bit" if subprocess.run([str(python), "-c", test], capture_output=True).returncode == 0 else "adamw"
    (root / "optimizer").write_text(optimizer + "\n", encoding="utf-8")
    say(f"ok optimizer: {optimizer}" + ("" if optimizer == "adamw8bit" else " (bitsandbytes does not run a step on this GPU)"))
    return 0


def pool_mb() -> tuple[int, str]:
    """The CUDA pool this machine gives one process (cudaMemGetInfo's total) and the GPU's name."""

    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available in the GPU venv")
    free, total = torch.cuda.mem_get_info(0)
    return total // (1 << 20), torch.cuda.get_device_name(0)


def config(args) -> int:
    base = Path(args.models)
    comfy = ROOT / "ComfyUI"
    if not (comfy / "main.py").is_file():
        print(f"no ComfyUI at {comfy}: run scripts\\setup-windows-comfy.ps1 first", file=sys.stderr)
        return 1
    (comfy / "extra_model_paths.yaml").write_text(YAML.format(base=base), encoding="utf-8")
    say(f"ComfyUI's model folders: {comfy / 'extra_model_paths.yaml'} (base {base})")
    total, name = pool_mb()
    record = {"version": 1, "gpu": name, "cuda_pool_mb": total, "machine": platform.machine(), "models": str(base),
              "python": sys.executable, "tf_h3": str(ROOT / "tf-h3" / "tf_h3.dll"),
              "bolt_bin": str(ROOT / "bolt" / "native" / "bin" / "tensorfold-native.exe"),
              "drama3_bin": str(ROOT / "drama3" / "bin" / "drama3-generate.exe"),
              "music3_bin": str(ROOT / "music3" / "bin" / "music3-generate.exe"),
              "cuda_bin": str(Path(args.cuda) / "bin" / "arm64"), "tensorfold": TENSORFOLD}
    for key in ("bolt_model", "bolt_draft"):
        value = getattr(args, key)
        if value:
            record[key] = value
    drama = Path(args.drama_model) if args.drama_model else base / "drama-3"
    if (drama / "config.json").is_file():
        record["drama3_model"] = str(drama)
        record["drama3_codec"] = str(drama / "codec-v1.safetensors")
    if not record.get("bolt_model") and (base / "Nemotron-3.5-Bolt-24GB" / "config.json").is_file():
        record["bolt_model"] = str(base / "Nemotron-3.5-Bolt-24GB")
    if not record.get("bolt_draft") and (base / "Nemotron-3.5-Bolt-24GB-LiLiCorr" / "config.json").is_file():
        record["bolt_draft"] = str(base / "Nemotron-3.5-Bolt-24GB-LiLiCorr")
    target = ROOT / "cuda-tier.json"
    target.write_text(json.dumps(record, indent=1), encoding="utf-8")
    say(f"{name}: a CUDA pool of {total / 1024:.1f} GiB; recorded in {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("models")
    one.add_argument("--models", required=True)
    one.add_argument("--parts", default="video,image")
    one.set_defaults(run=models)
    two = sub.add_parser("config")
    two.add_argument("--models", required=True)
    two.add_argument("--cuda", default="C:\\cuda-13.4")
    two.add_argument("--bolt-model", dest="bolt_model", default="")
    two.add_argument("--bolt-draft", dest="bolt_draft", default="")
    two.add_argument("--drama-model", dest="drama_model", default="")
    two.set_defaults(run=config)
    three = sub.add_parser("nodes")
    three.add_argument("--parts", default="workflows")
    three.add_argument("--pip-python", default="")
    three.add_argument("--target", default="")
    three.set_defaults(run=nodes)
    four = sub.add_parser("train")
    four.set_defaults(run=train_env)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
