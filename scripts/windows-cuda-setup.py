"""The native Windows CUDA tier's install steps that are easier in Python than in PowerShell (run with the GPU venv's
python by scripts/setup-windows-cuda.ps1):

    python scripts/windows-cuda-setup.py models --models C:\\Studio\\models --parts video,image,music
    python scripts/windows-cuda-setup.py config --models C:\\Studio\\models [--bolt-model DIR --bolt-draft DIR]

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

ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ImaginationStudio"
TENSORFOLD = {"repo": "https://github.com/drowzeys/TensorFold.git", "branch": "studio-1.2.2",
              "commit": "a545f7867cd82feab630a2d015086da602f53347"}
# (repo, folder under the model folder, files): oneshot-setup-cuda.sh's fetch lines, part by part
PARTS = {
    "video": [("FastVideo/FastVideo-FastH3-Comfy", "FastH3-Comfy",
               ["diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors",
                "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                "vae/minimax_h3_video_vae_int8_convrot.safetensors", "vae/minimax_h3_audio_vae_fp32.safetensors"])],
    "image": [("Comfy-Org/Qwen-Image-2.1", "Qwen-Image-2.1-Comfy",
               ["diffusion_models/qwen_image_2.1_turbo_bf16.safetensors", "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
                "vae/qwen_image_2.1_vae_bf16.safetensors"])],
    "image-edit": [("Comfy-Org/Qwen-Image-2.1", "Qwen-Image-2.1-Comfy", ["diffusion_models/qwen_image_2.1_bf16.safetensors"])],
    "ref2va": [("Comfy-Org/MiniMax-H3", "MiniMax-H3-Comfy",
                ["diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors",
                 "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"])],
    "flux3": [("black-forest-labs/flux-3-image-klein-comfy", "FLUX3-K-Comfy",
               ["diffusion_models/flux3-image-klein-bf16.safetensors", "diffusion_models/flux3-image-klein-base-bf16.safetensors",
                "text_encoders/qwen3vl_4b_bf16.safetensors",
                "text_encoders/qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors", "vae/flux2-vae.safetensors"]),
              ("black-forest-labs/flux-3-image-klein-pup", "FLUX3-K-Comfy", ["system_prompts.json"])],
    "moss": [("Comfy-Org/MOSS-Image", "MOSS-Image-Comfy",
              ["diffusion_models/moss_image_sft_bf16.safetensors", "text_encoders/qwen3.5_9b_bf16.safetensors",
               "vae/flux2-vae_small.safetensors"])],
    "music": [("Comfy-Org/MiniMax-Music-3", "MiniMax-Music3-Comfy",
               ["diffusion_models/minimax_music3_dit_fp16.safetensors",
                "text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors", "vae/minimax_music3_dav.safetensors"])],
    "sound": [("stabilityai/stable-audio-open-1.0", "Sound-Comfy/stable-audio-open-1.0",
               ["model.safetensors", "text_encoder/model.safetensors", "LICENSE.md"])],
}
# NVIDIA's checkpoints for the Bolt brain, at the revisions app/bolt.py names (whole repos)
BOLT = {"Nemotron-3.5-Bolt-24GB": ("nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB", "a326822af111290f2af5ac59ef4b5040347dabc4"),
        "Nemotron-3.5-Bolt-24GB-LiLiCorr": ("nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr", "4c6a5613b6d887194a3a5493e3a18a5b3930bb86")}
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


def models(args) -> int:
    from huggingface_hub import hf_hub_download, snapshot_download

    base = Path(args.models)
    failed = 0
    for part in [p.strip() for p in args.parts.split(",") if p.strip()]:
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
            print(f"unknown part {part}: one of {', '.join([*PARTS, 'bolt'])}", file=sys.stderr)
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
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
