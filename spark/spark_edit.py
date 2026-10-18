"""Edit a picture on a DGX Spark (CUDA): Qwen-Image-2.1 or FLUX3-K with the picture as its reference, through ComfyUI.

    spark_edit.py --image photo.png --prompt "make it night" -o out.png
    spark_edit.py --image photo.png --lora angles.safetensors:0.9 --set '[["<mva> back view, eye-level shot", "back.png"], ...]'
    spark_edit.py --image room.png --image person.png --prompt "the person of image 2 sits on the sofa of image 1" -o out.png

Qwen-Image-2.1 makes and edits pictures with one set of weights: the picture goes to the text encoder's eyes and into
the sequence as latents (ComfyUI's TextEncodeQwenImage21). The Turbo checkpoint runs its own 8 steps; --base is the
40-step model, which is what image LoRAs are trained on. A LoRA is merged at run time on the bf16 file.

The first --image is the picture that is edited: the result has its size. Further ones (ten pictures in all) are seen
beside it; the model knows them as image 1, image 2, ... in the order given. The noise is drawn for the seed and the
pictures together, and the sizes are worked out in edit_plan.py (beside qwen_image_edit.py), where both are explained.

--model flux3k-fast or flux3k-full: FLUX3-K (BFL's FLUX 3 Image Klein) on ComfyUI's FLUX 3 nodes, the layout of their
edit templates: every picture is a reference latent (Set Reference Multi Latent) on the prompt and on the negative, the
result takes the first picture's size, and BFL's prompt upsampler sees the pictures and writes the caption (its i2i
system prompt). Fast runs 16 steps, Full 50 at CFG 3.5. No LoRAs and no base model: those are Qwen's.

Credit: the Qwen team for Qwen-Image-2.1 and Qwen-Image-2.1-Turbo, Black Forest Labs for FLUX 3 Image Klein and its
prompt upsampler, ComfyUI for the runtime.
"""
import time
import subprocess
import argparse
import json
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import edit_plan  # noqa: E402  (the noise seed and the sizes, shared with the Mac build)
import spark_generate as sg  # noqa: E402


def prepared(path: str, size: tuple[int, int], folder: Path, index: int) -> Path:
    """A picture at exactly ``size`` (Lanczos, as ComfyUI's node would do it), so the node leaves it alone."""

    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        picture = ImageOps.exif_transpose(opened)
        picture = picture.convert("RGBA" if "A" in picture.getbands() or "transparency" in picture.info else "RGB")
        if picture.size != size:
            picture = picture.resize(size, Image.Resampling.LANCZOS)
        target = folder / f"edit_{index}.png"
        picture.save(target)
    return target


# BFL's upsampler: the user turn that carries the pictures (flux3-mlx's pup.py, from their reference), the most pixels
# a picture is shown at for one, two, or three and more pictures, its grid, and its JPEG quality
I2I_SCAFFOLD = "Reference images follow in order (the first image, the second image, ...).\nUser request: {}\nOutput the caption."
UPSAMPLER_PIXELS = {1: 1024 * 1024, 2: 724 * 724}
UPSAMPLER_GRID, UPSAMPLER_JPEG = 32, 92


def upsampler_pictures(paths: list[str], folder: Path) -> list[Path]:
    """The pictures as BFL's upsampler is shown them: over white, at its size budget, through JPEG. ComfyUI's
    TextGenerate takes one batch, so with several pictures each is centred on a white canvas of the largest size."""

    import io
    from PIL import Image, ImageOps

    budget = UPSAMPLER_PIXELS.get(len(paths), 512 * 512)
    shown = []
    for path in paths:
        with Image.open(path) as opened:
            picture = ImageOps.exif_transpose(opened).convert("RGBA")
        picture = Image.alpha_composite(Image.new("RGBA", picture.size, (255, 255, 255, 255)), picture).convert("RGB")
        w, h = (max(UPSAMPLER_GRID, round(v / UPSAMPLER_GRID) * UPSAMPLER_GRID) for v in picture.size)
        if w * h > budget:
            scale = (budget / (w * h)) ** 0.5
            w, h = (max(UPSAMPLER_GRID, int(v * scale) // UPSAMPLER_GRID * UPSAMPLER_GRID) for v in (w, h))
        buffer = io.BytesIO()
        picture.resize((w, h), Image.Resampling.LANCZOS).save(buffer, format="JPEG", quality=UPSAMPLER_JPEG)
        shown.append(Image.open(io.BytesIO(buffer.getvalue())).convert("RGB"))
    wide, tall = max(p.width for p in shown), max(p.height for p in shown)
    out = []
    for index, picture in enumerate(shown):
        if picture.size != (wide, tall):
            canvas = Image.new("RGB", (wide, tall), (255, 255, 255))
            canvas.paste(picture, ((wide - picture.width) // 2, (tall - picture.height) // 2))
            picture = canvas
        target = folder / f"shown_{index}.png"
        picture.save(target)
        out.append(target)
    return out


def flux3_workflow(args, pictures: list[str], shown: list[str], prompt: str, seed: int, made: tuple[int, int] | None = None) -> dict:
    """FLUX3-K's edit on ComfyUI's FLUX 3 nodes: the pictures as reference latents, the caption from BFL's upsampler."""

    _, steps, cfg = sg.FLUX3[args.model]
    unet = sg.flux3_unet(args.model)
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": sg.FLUX3_TEXT, "type": "flux3", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": sg.FLUX3_VAE}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        # Fast is distilled without guidance (its template zeroes the negative); Full runs an empty prompt against it
        "7": ({"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}} if args.model == "flux3k-fast"
              else {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ""}}),
        # every picture goes to the prompt and to the negative, at the size it was brought to (edit_plan.py)
        "17": {"class_type": "ReferenceMultiLatent", "inputs": {"positive": ["5", 0], "negative": ["7", 0], "vae": ["4", 0],
                                                               "resolution": "keep resolution"}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": args.steps if args.steps_set else steps,
                                                    "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "positive": ["17", 0],
                                                    "negative": ["17", 1], "latent_image": ["17", 2], "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": f"studio/edit_{uuid.uuid4().hex[:8]}"}},
    }
    for index, picture in enumerate(pictures):
        w[str(30 + index)] = {"class_type": "LoadImage", "inputs": {"image": picture}}
        w["17"]["inputs"][f"images.image{index}"] = [str(30 + index), 0]
    if made:  # a size asked for by hand in another shape than the picture's
        w["6"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": made[0], "height": made[1], "batch_size": 1}}
        w["12"]["inputs"]["latent_image"] = ["6", 0]
    if shown:  # BFL's upsampler writes the caption from the pictures and the prompt, inside the same workflow
        batch = ["50", 0]
        for index, picture in enumerate(shown):
            w[str(50 + index)] = {"class_type": "LoadImage", "inputs": {"image": picture}}
            if index:
                w[str(70 + index)] = {"class_type": "ImageBatch", "inputs": {"image1": batch, "image2": [str(50 + index), 0]}}
                batch = [str(70 + index), 0]
        w["20"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": sg.FLUX3_UPSAMPLER, "type": "flux3", "device": "default"}}
        w["21"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["20", 0], "prompt": I2I_SCAFFOLD.format(prompt), "image": batch, "max_length": 3584, "sampling_mode": "on",
            "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 1, "sampling_mode.top_p": 0.95, "sampling_mode.min_p": 0.0,
            "sampling_mode.repetition_penalty": 1.05, "sampling_mode.seed": 0, "sampling_mode.presence_penalty": 0.0,
            "thinking": False, "use_default_template": True, "mtp": "auto", "system_prompt": sg.flux3_system_prompt("i2i")}}
        w["22"] = {"class_type": "PreviewAny", "inputs": {"source": ["21", 0]}}
        w["5"]["inputs"]["text"] = ["21", 0]
    return w


def workflow(args, pictures: list[str], prompt: str, seed: int, loras: list[tuple[str, float]], made: tuple[int, int] | None = None) -> dict:
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": sg.QWEN_BASE if args.base else sg.QWEN, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": sg.QWEN_TEXT, "type": "qwen_image", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": sg.QWEN_VAE}},
        # the pictures are seen by the text encoder and spliced into the sequence as latents; the node's own latent is
        # an empty one on the first picture's size, the size an edit is made at. resolution 0: each picture stays at
        # the size it was brought to before the upload (edit_plan.py)
        "5": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["3", 0], "prompt": prompt, "negative_prompt": args.negative,
                                                                "vae": ["4", 0], "resolution": 0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": f"studio/edit_{uuid.uuid4().hex[:8]}"}},
    }
    for index, picture in enumerate(pictures):
        w[str(20 + index)] = {"class_type": "LoadImage", "inputs": {"image": picture}}
        w["5"]["inputs"][f"images.image_{index + 1}"] = [str(20 + index), 0]
    model = ["1", 0]
    for index, (name, strength) in enumerate(loras):
        w[str(80 + index)] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": name, "strength_model": strength}}
        model = [str(80 + index), 0]
    latent = ["5", 2]
    if made:  # a size asked for by hand in another shape than the picture's
        w["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": made[0], "height": made[1], "batch_size": 1}}
        latent = ["6", 0]
    if args.base:
        w["12"] = {"class_type": "KSampler", "inputs": {"model": model, "seed": seed, "steps": args.steps, "cfg": args.cfg, "sampler_name": "euler",
                                                         "scheduler": "simple", "positive": ["5", 0], "negative": ["5", 1], "latent_image": latent, "denoise": 1.0}}
    else:
        w["8"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        w["9"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        w["10"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": sg.TURBO_SIGMAS}}
        w["11"] = {"class_type": "CFGGuider", "inputs": {"model": model, "positive": ["5", 0], "negative": ["5", 1], "cfg": 1.0}}
        w["12"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": latent}}
    return w


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--image", required=True, action="append", help="the picture to edit; repeat for further pictures the prompt draws on (image 2, image 3, ...)")
    parser.add_argument("--prompt", default="", help="what to change (one picture, written to -o)")
    parser.add_argument("-o", "--output", default="")
    parser.add_argument("--set", default="", help='several edits of the one picture from one load: JSON [[prompt, output], ...], or @FILE holding it')
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--base", action="store_true", help="the 40-step base model (when it has been downloaded) instead of Turbo's 8 steps")
    parser.add_argument("--steps", type=int, default=40, help="base model only")
    parser.add_argument("--cfg", type=float, default=1.0, help="base model only; 1 runs the prompt alone")
    parser.add_argument("--negative", default="")
    parser.add_argument("--size", choices=("source", "model"), default="source",
                        help="source: the result has the picture's own size (the default); model: about --resolution pixels squared, keeping its shape")
    parser.add_argument("--resolution", type=int, default=1024, help="with --size model: the picture is brought to about this many pixels squared")
    parser.add_argument("--most", type=int, default=0, help="with --size source: the most pixels an edit is made at (0: 1536x1536)")
    parser.add_argument("--width", type=int, default=0, help="with --height: another size for the result (the edit shifts when its shape differs from the picture's)")
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--lora", action="append", default=[], help="an image LoRA, PATH or PATH:STRENGTH (repeat to stack)")
    parser.add_argument("--model", choices=("qwen", *sg.FLUX3), default="qwen",
                        help="qwen: Qwen-Image-2.1 (the default); flux3k-fast or flux3k-full: FLUX3-K (oneshot-setup-spark.sh --flux3)")
    parser.add_argument("--no-upsample", dest="upsample", action="store_false",
                        help="FLUX3-K: send the prompt as written (a full caption of the result) instead of BFL's prompt upsampler's")
    args = parser.parse_args()
    args.steps_set = "--steps" in sys.argv[1:]
    flux3 = args.model in sg.FLUX3
    if flux3 and (args.base or args.lora):
        sys.exit("image LoRAs and the base model are Qwen-Image-2.1's: FLUX3-K takes neither")
    for path in args.image:
        if not Path(path).is_file():
            sys.exit(f"no picture at {path}")
    if len(args.image) > edit_plan.PICTURES:
        sys.exit(f"at most {edit_plan.PICTURES} pictures in one edit, got {len(args.image)}")
    if bool(args.width) != bool(args.height):
        sys.exit("--width and --height go together")
    if args.width and edit_plan.refused(args.width, args.height):
        sys.exit(edit_plan.refused(args.width, args.height))
    if args.resolution % 32 or not 256 <= args.resolution <= 2048:
        sys.exit("--resolution is a multiple of 32 between 256 and 2048")
    edits = json.loads(Path(args.set[1:]).read_text() if args.set.startswith("@") else args.set) if args.set else [[args.prompt, args.output]]
    if not edits or any(not str(p).strip() or not o for p, o in edits):
        sys.exit("give --prompt and -o, or --set with a prompt and an output for each edit")
    # After a Library action (Ref2VA in bfloat16, SAM, the latent upscaler) ComfyUI's own unloading leaves too little
    # for Qwen-Image beside it (measured: 12.7 GB free during an edit): that edit starts from a server with nothing loaded.
    last = sg.STATE.read_text().strip() if sg.STATE.exists() else ""
    if sg.up() and last.startswith("workflow"):
        print("[tensorfold] restarting ComfyUI: a picture edit after a Library action starts from a server with nothing loaded", flush=True)
        subprocess.run(["bash", str(sg.HERE / "comfy.sh"), "stop"], check=False)
        for _ in range(60):
            if not sg.up():
                break
            time.sleep(1)
        time.sleep(5)
    sg.ensure_server("flux3" if flux3 else "image")
    if flux3 and not sg.installed("unet", sg.flux3_unet(args.model)):
        sys.exit(f"{args.model} is not installed: oneshot-setup-spark.sh --flux3")
    if args.base and not sg.installed("unet", sg.QWEN_BASE):
        sys.exit("the base model is not installed: oneshot-setup-spark.sh --image-edit fetches Qwen-Image-2.1 (14 GB; Qwen Research License, non-commercial)")
    loras = sg.hub_loras(args.lora)
    for name, _ in loras:
        if not sg.installed("lora", name):
            sys.exit(f"ComfyUI does not list the LoRA {name}")
    # the size the edit is made at and the size that comes back, and the noise for this seed and these pictures
    sizes = [edit_plan.picture_size(path) for path in args.image]
    plan = edit_plan.canvas(*sizes[0], args.size, args.resolution, (args.width, args.height) if args.width else None, args.most)
    made, size = tuple(plan["made"]), tuple(plan["size"])
    if plan["note"]:
        print(f"[tensorfold] {plan['note']}", flush=True)
    noise = edit_plan.noise_seed(args.seed, args.image)
    try:
        with tempfile.TemporaryDirectory(prefix="studio-edit-") as scratch:
            fitted = [tuple(plan["source"])] + [edit_plan.beside(*one, made) for one in sizes[1:]]
            pictures = [sg.upload(str(prepared(path, fit, Path(scratch), index))) for index, (path, fit) in enumerate(zip(args.image, fitted))]
            shown = [sg.upload(str(p)) for p in upsampler_pictures(args.image, Path(scratch))] if flux3 and args.upsample else []
        for prompt, output in edits:
            other = made if tuple(plan["source"]) != made else None
            if flux3:
                item, seconds = sg.render(flux3_workflow(args, pictures, shown, prompt, noise, other), "12")
            else:
                item, seconds = sg.render(workflow(args, pictures, prompt, noise, loras, other), "12")
            sg.fetch(item, Path(output))
            if size != made:  # a side that is not on the model's grid: the result is brought back to the picture's own
                from PIL import Image

                with Image.open(output) as opened:
                    opened.resize(size, Image.Resampling.LANCZOS).save(output)
            print("[tensorfold] " + json.dumps({"output": str(output), "engine": "cuda", "model": args.model, "edit": prompt[:80], "seed": args.seed,
                                                "noise_seed": noise, "turbo": not args.base, **({"upsampled": args.upsample} if flux3 else {}), "loras": [name for name, _ in loras], "pictures": len(pictures),
                                                "size": list(size), "made_at": list(made), "total_s": round(seconds, 1)}), flush=True)
    finally:
        for name in sg.UPLOADED:
            (sg.PREFIX / "ComfyUI" / "input" / name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
