"""The model folders of the Mac's ComfyUI (mac/comfy.sh runs this before every start).

    python mac/comfy_files.py PREFIX            # write $PREFIX/comfy-models.yaml and the pointer files
    python mac/comfy_files.py PREFIX --check    # print what is there as JSON, change nothing

ComfyUI lists the files of $MAC_COMFY_MODELS ($PREFIX/comfy-models): what scripts/setup-comfy.sh downloaded (SAM 3.1,
the H3 latent upscaler, the audio decoder, MOSS-Image's three files) and three pointer files (`*.studio.sft`) that
mac/studio_files reads as MiniMax H3's transformer, text encoder and video decoder from the Mac's own copy in
H3_MODEL_DIR. The Spark's runner (spark/spark_generate.py) asks for these names on a Mac (`MAC_FILES`).
"""
import json
import os
import sys
from pathlib import Path

FOLDERS = ("diffusion_models", "text_encoders", "vae", "checkpoints", "latent_upscale_models", "loras")
# pointer file -> (kind, the folder under H3_MODEL_DIR it reads)
POINTERS = {
    "diffusion_models/minimax_h3_ref2va_bf16.studio.sft": ("h3-dit", "Ref2VA/transformer"),
    "text_encoders/qwen3vl_32b_minimax_h3_bf16.studio.sft": ("h3-text", "FL2VA/text_encoder"),
    "vae/minimax_h3_video_vae_fp32.studio.sft": ("h3-video-vae", "FL2VA/video_vae"),
}


def models(prefix: Path) -> Path:
    return Path(os.environ.get("MAC_COMFY_MODELS") or prefix / "comfy-models").expanduser()


def h3_dir() -> Path:
    return Path(os.environ.get("H3_MODEL_DIR") or Path.home() / "h3-models/MiniMax-H3").expanduser()


def write(prefix: Path) -> dict:
    root, h3 = models(prefix), h3_dir()
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    made = {}
    for name, (kind, source) in POINTERS.items():
        target = root / name
        if not (h3 / source).is_dir():
            target.unlink(missing_ok=True)     # a pointer to nothing would be listed and fail when loaded
            continue
        text = json.dumps({"studio_files": {"kind": kind, "folder": str(h3 / source)}})
        if not target.is_file() or target.read_text() != text:
            target.write_text(text)
        made[name] = str(h3 / source)
    lines = ["studio:", f"  base_path: {root}"] + [f"  {folder}: {folder}" for folder in FOLDERS]
    (prefix / "comfy-models.yaml").write_text("\n".join(lines) + "\n")
    return made


def check(prefix: Path) -> dict:
    root, h3 = models(prefix), h3_dir()
    return {"models": str(root), "h3": {name: (h3 / source).is_dir() for name, (_, source) in POINTERS.items()},
            "files": sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.is_dir() else []}


if __name__ == "__main__":
    prefix = Path(sys.argv[1]).expanduser()
    print(json.dumps(check(prefix) if "--check" in sys.argv[2:] else write(prefix), indent=1))
