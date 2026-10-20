"""Render one FastH3 clip with the transformer's passes on TensorFold 1.0's native Zig + Metal runtime.

Three stages: the engine's h3_case.py exports the prompt's rows, tables and noise (Python: text encoder and the
checkpoint's small projections), tf-h3-dit runs every pass on the GPU, and the rows are decoded here to an MP4 with
sound (Python: the video and audio decoders, the same ones h3_generate.py uses). --first-frame starts the clip from
an image: the export encodes it and the native passes carry its rows, held, ahead of the audio.

With an engine that can (its h3_case.py writes the case under another name and renames it when complete), the
native tool starts at once and rounds the checkpoint to int8 while the prompt is being encoded. --lora merges style adapters into the native engine's weights: their block factors are added
to the bf16 projections in float32 before the int8 rounding, as the MLX engine merges them, never into a saved copy.
An adapter the native engine cannot apply ends the run with exit status 3 before any pass (scripts/video.sh then
renders on the MLX engine).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

TILE_PIXELS = 128  # one routing tile is 4 tokens a side, a token 32 pixels
STEP = re.compile(r"^step (\d+)/(\d+): ([0-9.]+) s wall")
REFUSED = 3  # h3_case.py's exit status for an adapter the native engine cannot apply


def aligned(value: int) -> int:
    return -(-value // TILE_PIXELS) * TILE_PIXELS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", help="the MiniMax H3 pipeline folder (text encoder and decoders)")
    parser.add_argument("--fasth3", required=True, help="the FastH3 checkpoint folder")
    parser.add_argument("--engine", required=True, help="the folder holding tf-h3-dit and h3_case.py")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--width", type=int, default=864)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frames", type=int, default=124)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--steps", type=int, default=0, help="passes, when not the checkpoint's trained count")
    parser.add_argument("--first-frame", default=None, help="image the clip starts from (image to video)")
    parser.add_argument("--align", action="store_true",
                        help="generate at the size rounded up to whole routing tiles and crop back to width x height")
    parser.add_argument("--upscale-vae", help="safetensors of a packed-head (2x) video decoder; frames come out larger")
    parser.add_argument("--crop", help="WxH: centre-crop the decoded frames before the MP4 is written")
    parser.add_argument("--options", default="", help="tf-h3-dit's kernel options, if any")
    parser.add_argument("--lora", action="append", default=[], metavar="PATH[:STRENGTH]",
                        help="a style adapter merged into the native engine's weights (repeat for several)")
    parser.add_argument("--keep", action="store_true", help="keep the case folder beside the clip")
    args = parser.parse_args()
    if (args.prompt is None) == (args.prompt_file is None):
        parser.error("give exactly one of --prompt and --prompt-file")

    engine = Path(args.engine)
    native, case_py = engine / "tf-h3-dit", engine / "h3_case.py"
    if not native.is_file() or not case_py.is_file():
        raise SystemExit(f"no native engine at {engine} (run oneshot-setup.sh)")
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height, crop = args.width, args.height, args.crop
    if args.align and (aligned(width), aligned(height)) != (width, height):
        scale = 2 if args.upscale_vae else 1
        crop = crop or f"{width * scale}x{height * scale}"
        width, height = aligned(width), aligned(height)
    case = Path(tempfile.mkdtemp(prefix="h3case_", dir=str(out.parent)))
    prompt = Path(args.prompt_file) if args.prompt_file else case / "prompt.txt"
    if args.prompt is not None:
        prompt.write_text(args.prompt)
    size = ["--width", str(width), "--height", str(height), "--frames", str(args.frames)]
    shards = len(list((Path(args.fasth3) / "transformer").glob("diffusion_pytorch_model-*.safetensors")))
    print(f"[tensorfold] engine: zig (TensorFold native runtime), generating {width}x{height}, {args.frames} frames"
          + (f", cropped to {crop}" if crop else ""), flush=True)

    tool_text = case_py.read_text()
    overlap = "case.partial.safetensors" in tool_text  # an engine that renames its case into place when complete
    if args.lora and "--lora" not in tool_text:
        print("[tensorfold] this native engine cannot merge a LoRA", file=sys.stderr)
        shutil.rmtree(case, ignore_errors=True)
        return REFUSED

    started = time.perf_counter()
    export = [sys.executable, str(case_py), args.model_dir, args.fasth3, str(case), "--tools", str(engine),
              "--prompt-file", str(prompt), "--seed", str(args.seed), "--no-reference", *size]
    if args.steps:
        export += ["--steps", str(args.steps)]
    if args.first_frame:
        export += ["--first-frame", args.first_frame]
    for spec in args.lora:
        export += ["--lora", spec]
    options = [word for word in args.options.split(",") if word]
    if overlap:
        options.append("wait")
    if args.lora:
        options.append(f"lora={case / 'lora.safetensors'}")
    command = [str(native), str(Path(args.fasth3) / "transformer"), str(shards), str(case / "case.safetensors"),
               str(case / "zig")] + ([",".join(options)] if options else [])
    process = None
    try:
        exporter = subprocess.Popen(export, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if overlap:  # the native tool reads the checkpoint while the prompt is encoded, and waits for the case
            process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
        out_text, err_text = exporter.communicate()
        if exporter.returncode:
            sys.stderr.write(out_text[-2000:] + err_text[-4000:])
            if exporter.returncode == REFUSED:
                shutil.rmtree(case, ignore_errors=True)
                return REFUSED
            raise SystemExit("the export stage failed")
        rows = json.loads(out_text.strip().splitlines()[-1])
        export_seconds = time.perf_counter() - started

        started = time.perf_counter()
        if process is None:
            process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
        passes: list[float] = []
        tail: list[str] = []
        for line in process.stderr:
            tail = (tail + [line])[-20:]
            found = STEP.match(line)
            if found:
                passes.append(float(found.group(3)))
                print(f"[tensorfold] step {found.group(1)}/{found.group(2)} {found.group(3)}s", flush=True)
            elif line.startswith("weights in") and args.lora:
                print(f"[tensorfold] {line.strip()}", flush=True)
        process.wait()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
    if process.returncode or not passes:
        sys.stderr.write("".join(tail))
        raise SystemExit("the native transformer failed")
    denoise_seconds = time.perf_counter() - started
    (case / "case.safetensors").unlink()

    started = time.perf_counter()
    import mlx.core as mx
    import numpy as np

    # MLX keeps freed buffers for reuse, by default up to tens of GiB; the decoders need 13 GiB, and are no faster for more
    mx.set_cache_limit(6 * 2**30)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import h3_generate as tool
    from minimax_h3_mlx.media import save_mp4
    from tensorfold.families.h3 import config as h3
    from tensorfold.families.h3 import fasth3

    config = fasth3.config(args.fasth3)
    video = np.fromfile(str(case / "zig.video.f32"), dtype=np.float32).reshape(-1, config.latents_dim * 4)
    audio = np.fromfile(str(case / "zig.audio.f32"), dtype=np.float32).reshape(-1, config.audio_latents_dim)
    latents = SimpleNamespace(video_rows=mx.array(video), audio_rows=mx.array(audio),
                              latent_frames=h3.latent_frames(args.frames),
                              latent_height=height // h3.VAE_SPATIAL_RATIO, latent_width=width // h3.VAE_SPATIAL_RATIO,
                              audio_latents=h3.audio_latents(args.frames))
    frames, wave, rate = tool.decode(args.model_dir, h3.pipeline_root(args.model_dir), latents,
                                     h3.DiTConfig.from_checkpoint(args.model_dir), upscale_decoder=args.upscale_vae)
    if crop:
        crop_w, crop_h = (int(v) for v in crop.lower().split("x"))
        full_h, full_w = frames.shape[1:3]
        if crop_w > full_w or crop_h > full_h or (full_w - crop_w) % 2 or (full_h - crop_h) % 2:
            raise SystemExit(f"cannot centre-crop {full_w}x{full_h} frames to {crop_w}x{crop_h}")
        top, left = (full_h - crop_h) // 2, (full_w - crop_w) // 2
        frames = np.ascontiguousarray(frames[:, top : top + crop_h, left : left + crop_w])
    save_mp4(str(out), frames, h3.FPS, audio=wave, sample_rate=rate)
    decode_seconds = time.perf_counter() - started
    if not args.keep:
        shutil.rmtree(case, ignore_errors=True)
    print("[tensorfold] " + json.dumps({
        "output": str(out), "engine": "zig", "rows": rows.get("rows"), "forwards": len(passes),
        "loras": len(args.lora),
        "export_s": round(export_seconds, 1), "denoise_s": round(denoise_seconds, 1),
        "decode_s": round(decode_seconds, 1), "per_forward_s": round(float(np.median(passes)), 2),
        "frames": int(frames.shape[0]), "size": [int(frames.shape[2]), int(frames.shape[1])]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
