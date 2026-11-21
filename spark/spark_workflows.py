"""Three Library actions on a DGX Spark, each through its author's own ComfyUI nodes (spark/setup-workflows.sh).

    spark_workflows.py turnaround --ref subject.png --lora turnaround.safetensors -o folder/name
    spark_workflows.py upscale    --video clip.mp4 --lora video-upscaler.safetensors -o bigger.mp4
    spark_workflows.py mask       --video clip.mp4 --person "man in a gray shirt" -o folder/name
    spark_workflows.py remove     --video clip.mp4 --person "man in a gray shirt" --lora person-remover.safetensors -o clean.mp4
    spark_workflows.py facefix    --video clip.mp4 --face face.png -o fixed.mp4

turnaround  five views of one subject from one picture: MATLOWAI's contact-sheet nodes pack five still pictures where
            a five-step video latent would go, and decode each on its own. Writes name_sheet.png and name_view1..5.png.
upscale     a finished clip made larger: dntpi's H3 Video Upsampler (LBH-123-AI's latent upscaler, then one light
            re-draw in tiles that the h3upscale LoRA guides with the clip itself). The clip's own sound is kept.
mask        the person found by a few words and tracked through the clip (SAM 3.1, ComfyUI's own nodes): writes
            name_mask.mp4 (the person painted green, the picture the remover is given), name_matte.mp4 (the mask
            alone) and name_plate.png (a first frame without the person, built from the other frames).
remove      the person taken out and the background made again: Akatz Labs' Person Remover LoRA through their H3
            Relay node, in overlapping windows of 22 frames. --clean-frame is a first frame without the person; with
            none given, the plate is built as `mask` builds it and made sharp by one window of the remover itself.
facefix     a clip's face drawn again close up: Carasibana's H3 FaceRefine crops it, SAM 3.1 and drozbay's
            MaskVidExperiments mask it, MiniMax H3 Ref2VA redraws it from a face picture at half strength, ylchen333's
            VOSR 2.0 nodes restore the crops when memory permits, and PixelAI Labs' face size fade limits the paste on
            close-ups before the face is stitched back. The clip's own sound is kept.

All three render with MiniMax H3 Ref2VA in bf16: the LoRAs are merged at run time, which spoils the 8-bit file.
"""
import argparse
import asyncio
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import spark_generate as sg

SAM = "sam3.1_multiplex_fp16.safetensors"
UPSCALER = "minimax_h3_latent_upscaler_3d_bf16.safetensors"
# the caption the h3upscale LoRA was trained on (the node's own default)
UPSCALE_PROMPT = "h3upscale, increase resolution and restore fine details while preserving the original content and visual style."
REMOVE_PROMPT = ("Remove the green-masked person and reconstruct the background. Preserve the rest of the video, "
                 "including its camera motion and frame timing.")
# The LoRA's own instruction is "the camera orbits the subject of <Picture 1> ninety degrees clockwise". On its own the
# model sometimes reads that as a roll of the picture (measured: seed 42 rolled a front-facing subject, seed 1 did
# not; without the LoRA it always rolls). Saying that the orbit is level and the subject stays upright held on every
# subject and seed tried.
TURN_PROMPT = "the camera orbits horizontally around the subject of <Picture 1> at eye level, ninety degrees clockwise, the subject stays upright on the ground"
WINDOWS = tuple(range(22, 363, 17))
# The clip's last frames are the end of the last window, with no later window to draw them again: measured, the last
# three kept patches of green. The clip is lengthened by its own last frames played backwards and the result cut back
# (copies of the last frame, a frozen picture, made the last windows give up on the person altogether).
TAIL = 8
NODES = {"turnaround": ("H3ContactSheet", "H3ContactSheetDecode"), "upscale": ("H3VideoUpsampler",),
         "mask": ("SAM3_Detect", "SAM3_VideoTrack", "SAM3_TrackToMask", "H3RelayGreenMask"),
         "remove": ("SAM3_Detect", "SAM3_VideoTrack", "SAM3_TrackToMask", "H3RelayGreenMask", "H3RelayPersonRemover"),
         "facefix": ("H3FaceTrackCrop", "PixelAIH3FaceSizeFade", "H3FaceStitch", "MVEx_MaskCleanup", "MVEx_MaskToLatentSpace", "VOSR2ModelLoader",
                     "VOSR2Upscale", "SAM3_VideoTrack", "SAM3_TrackToMask", "DifferentialDiffusion", "LTXVConcatAVLatent",
                     "LTXVSeparateAVLatent",
                     "MiniMaxH3ReferenceToVideo")}
# Fix faces: the face detector H3 FaceRefine tracks with, and the VOSR 2.0 bundle its loader reads (setup-workflows.sh)
FACE_DETECTOR = "face_yolov8n.pt"
VOSR2_FILES = ("args.json", "checkpoints/ema_model.safetensors", "dinov2_vitl14.safetensors",
               "Qwen-Image-vae-2d/config.json", "Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors")
CANVASES = (512, 640, 768)        # the face crop's side H3 draws again at: 768 is the author's, 512 costs 0.44 of it


def say(text: str) -> None:
    print("[tensorfold] " + text, flush=True)


def listed(loader: str, field: str) -> list:
    return sg.call(f"/object_info/{loader}")[loader]["input"]["required"][field][0]


# The upsampler's pack and the relay's each replace the layout of H3's packed sequence when they first run, and the
# relay refuses to work in a server where another pack got there first (measured: a removal after an upscale failed
# with "the inline layout patch could not be enabled"). So each of the two starts from a server the other has not
# run in: PATCHED names the pack that has, and the ComfyUI process it ran in.
PATCHED = sg.PREFIX / "comfy.patched"
PACK = {"upscale": "upsampler", "remove": "relay"}


# what installs the actions' node packs and models: spark/setup-workflows.sh, natively on Windows the CUDA tier's setup
SETUP = ("powershell -ExecutionPolicy Bypass -File scripts\\setup-windows-cuda.ps1 -SkipToolchain -Parts workflows"
         if sg.WINDOWS else "bash spark/setup-workflows.sh")


def comfy_pid() -> str:
    return sg.comfy_pid()


def server(action: str) -> None:
    """ComfyUI up with nothing of another kind loaded, and the action's nodes and files present."""

    last = sg.side("kind").read_text().strip() if sg.side("kind").exists() else ""
    other = PATCHED.read_text().split() if PATCHED.exists() else []
    clash = action in PACK and len(other) == 2 and other[0] != PACK[action] and other[1] == comfy_pid()
    # a server another kind of job used (a clip, a picture model, a song) starts again empty: ComfyUI kept FLUX3-K's
    # weights beside the turnaround's Ref2VA transformer and text encoder (64 GB) and the memory floor stopped it
    # (2026-10-20, a DGX Spark); as for a reference clip, TensorFold's engine keeps its 8-bit weights until it is gone
    if sg.up() and ((last and not last.startswith("workflow-")) or clash):
        say("restarting ComfyUI: this starts from a server with nothing loaded" if not clash else
            "restarting ComfyUI: the upscaler's and the remover's node packs do not share one server")
        sg.run_comfy_control("stop", check=False)
        for _ in range(60):
            if not sg.up():
                break
            time.sleep(1)
        time.sleep(5)
    if not sg.MAC and not sg.WINDOWS:  # Windows has no per-file page-cache release (posix_fadvise)
        # the actions load 64 to 75 GB at once (MiniMax H3's Ref2VA transformer and text encoder): the model files' page
        # cache is given back first, so the load lands in free memory instead of the kernel reclaiming cache while it
        # streams in. That reclaim, on a head with 57 GB of cache and Bolt's weights read once, made the memory floor
        # stop the server with 38 GB still available (2026-10-20). A model another job had cached is read again.
        count = sg.memory_guard.release_cache(sg.memory_guard.model_files())
        say(f"[tensorfold] gave back the page cache of {count} model files before loading")
    sg.ensure_server("workflow-" + action)
    have = sg.call("/object_info")
    missing = [n for n in NODES[action] if n not in have]
    if missing:
        sys.exit(f"ComfyUI has no node {', '.join(missing)}: run `{'bash scripts/setup-comfy.sh' if sg.MAC else SETUP}`, "
                 f"then `{'bash mac/comfy.sh stop' if sg.MAC else 'python spark/comfy_win.py stop' if sg.WINDOWS else 'bash spark/comfy.sh stop'}`")
    if action != "mask" and ref2va() is None:  # finding a person is SAM 3.1's alone
        sys.exit("MiniMax H3 Ref2VA is not installed: " + (SETUP.replace("workflows", "swap") if sg.WINDOWS else "bash oneshot-setup-spark.sh --ref2va"))
    if action in PACK:
        PATCHED.write_text(f"{PACK[action]} {comfy_pid()}")


def lora_name(spec: str) -> tuple[str, float]:
    """A LoRA file as ComfyUI's loader names it (linked into its loras folder), and its strength."""

    path, strength = spec, 1.0
    head, _, tail = spec.rpartition(":")
    try:
        if head:
            path, strength = head, float(tail)
    except ValueError:
        pass
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        sys.exit(f"no LoRA at {path}")
    link = sg.MODELS / "loras" / f"hub_{source.name}"
    if sg.WINDOWS:  # a hard link (or a copy): a symlink needs a privilege a person may not have
        sg.link_lora(source, link)
    elif not link.exists() or link.resolve() != source:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(source)
    if link.name not in listed("LoraLoaderModelOnly", "lora_name"):
        sys.exit(f"ComfyUI does not list {link.name}")
    return link.name, strength


async def follow(workflow: dict, sampler: str | list, parts: int) -> str:
    """Queue a workflow and print the progress of `sampler` as `[tensorfold] step i/n` until it ends; returns its id.

    A node that expands into a graph of its own (the remover: one sampler a window) reports its inner nodes as
    `sampler.x.y`: with `parts` such samplers expected, their steps are counted as one run."""

    sg.gate()
    import aiohttp

    workflow = sg.gpus.place(workflow)  # two cards: the text encoders and decoders on the second (spark/gpus.py)
    client = uuid.uuid4().hex
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(sg.URL.replace("http", "ws", 1) + f"/ws?clientId={client}", max_msg_size=0) as ws:
            prompt_id = sg.call("/prompt", {"prompt": workflow, "client_id": client})["prompt_id"]
            print(f"[tensorfold] comfy prompt {prompt_id}", flush=True)  # what the app's Cancel stops (server.stop_comfy)
            inner, last = [], None
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                event = json.loads(message.data)
                data = event.get("data", {})
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                node = str(data.get("node"))
                if event["type"] == "progress" and isinstance(sampler, list) and node in sampler:
                    # several samplers one after another (Fix faces: one a person): their passes counted as one run
                    at, of = int(data["value"]), int(data["max"])
                    at, of = sampler.index(node) * of + at, len(sampler) * of
                    if (at, of) != last:
                        last = (at, of)
                        say(f"step {at}/{of}")
                elif event["type"] == "progress" and isinstance(sampler, str) and (node == sampler or node.startswith(sampler + ".")):
                    at, of = int(data["value"]), int(data["max"])
                    if parts > 1 and node != sampler:
                        if of < 2:
                            continue                      # a decode or an encode inside the window, not its passes
                        if node not in inner:
                            inner.append(node)
                        at, of = (inner.index(node)) * of + at, max(parts, len(inner)) * of
                    if (at, of) != last:
                        last = (at, of)
                        say(f"step {at}/{of}")
                elif event["type"] == "executing" and data.get("node") is None:
                    break
                elif event["type"] in ("execution_error", "execution_interrupted", "execution_success"):
                    break
    return prompt_id


def execute(workflow: dict, sampler: str | list, parts: int = 1) -> tuple[dict, float]:
    """Run a workflow; returns ({node id: [saved file records]}, seconds)."""

    started = time.time()
    try:
        prompt_id = asyncio.run(follow(workflow, sampler, parts))
    except KeyboardInterrupt:
        sg.call("/interrupt", {})
        raise
    record = sg.history_of(prompt_id)
    if record["status"].get("status_str") != "success":
        errors = [m[1] for m in record["status"].get("messages", []) if m[0] == "execution_error"]
        detail = errors[0].get("exception_message", "").strip() if errors else json.dumps(record["status"])[:600]
        sys.exit(f"ComfyUI failed: {detail} (log: {sg.side('log')})")
    saved = {}
    for node, output in record["outputs"].items():
        saved[node] = [item for items in output.values() for item in (items if isinstance(items, list) else [])
                       if isinstance(item, dict) and item.get("filename") and item.get("type", "output") == "output"]
    return saved, time.time() - started


def take(item: dict, target: Path) -> None:
    """Fetch a saved file and remove ComfyUI's copy."""

    sg.fetch(item, target)
    (sg.PREFIX / "ComfyUI" / "output" / item.get("subfolder", "") / item["filename"]).unlink(missing_ok=True)


def ref2va() -> str | None:
    """Prefer the measured bf16 action weights. Windows refuses int8 runtime adapter merging; the existing
    Linux fallback is retained. Character Swap has its own weight-route selection."""

    have = listed("UNETLoader", "unet_name")
    if sg.WINDOWS:
        return sg.REF2VA["bf16"] if sg.REF2VA["bf16"] in have else None
    return next((sg.REF2VA[one] for one in ("bf16", "int8") if sg.REF2VA.get(one) in have), None)


def text_encoder() -> str:
    """The H3 text encoder these graphs load: the regular file, or, on an install that kept another build in its
    place (a small card's NVFP4 one, docs/small-cards.md), the one a clip uses by default there
    (sg.default_text_encoder). ComfyUI turns a graph away for a file it does not list."""

    have = listed("CLIPLoader", "clip_name")
    if sg.H3_TEXT in have:
        return sg.H3_TEXT
    other = sg.default_text_encoder()
    return next((one for one in (other, f"H3/{other}") if other != "regular" and one in have), sg.H3_TEXT)


def loaders(w: dict, vae: str | None = None, lora: bool = True, transformer: str = "") -> None:
    unet_name = transformer or ref2va() or sg.REF2VA["bf16"]
    if transformer and transformer not in listed("UNETLoader", "unet_name"):
        sys.exit(f"ComfyUI does not list the video model {transformer}")
    w["1"] = {"class_type": "UNETLoader", "inputs": {"unet_name": unet_name, "weight_dtype": "default"}}
    # what the log says is the file that loads: a LoRA merged at run time into an 8-bit (int8_convrot) file is known to
    # spoil the picture without any error (sg.REF2VA's note), so a run on that fallback says so
    unet = w["1"]["inputs"]["unet_name"]
    if unet != sg.REF2VA["bf16"] and lora:   # without a LoRA (Fix faces may run without one) the 8-bit file is the plain model
        # measured once on an RTX 5060 Ti (2026-11-17): the Turnaround LoRA on the int8 file turned a full-body still
        # from front to profile, the faces soft at 512 a view; one run, so the note stays a note
        say(f"note: the bf16 Ref2VA file ({sg.REF2VA['bf16']}) is not here, so this runs on {unet}: a LoRA merged at "
            "run time into an 8-bit file may come out weaker than on bf16. Check the result.")
    w["3"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder(), "type": "minimax", "device": "default"}}
    w["4"] = {"class_type": "VAELoader", "inputs": {"vae_name": vae or sg.H3_VAE}}
    w["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": sg.H3_AUDIO_VAE}}


def still_vae() -> str:
    """The fp16 video decoder for still pictures when it is installed (the 8-bit one is the clips' default)."""

    wanted = os.environ.get("SPARK_VAE") or "minimax_h3_video_vae_fp16.safetensors"
    return wanted if wanted in listed("VAELoader", "vae_name") else sg.H3_VAE


# ---------------------------------------------------------------------------------------------------------- turnaround
def turnaround(args) -> None:
    if args.size % 32 or not 512 <= args.size <= 2048:
        sys.exit("--size is a multiple of 32 between 512 and 2048")
    if "<Picture 1>" not in args.prompt:
        sys.exit("the prompt names the picture as <Picture 1>")
    if not Path(args.ref).is_file():
        sys.exit(f"no picture at {args.ref}")
    server("turnaround")
    name, strength = lora_name(args.lora)
    prefix = f"studio/turn_{uuid.uuid4().hex[:8]}"
    w = {}
    loaders(w, still_vae())
    w.update({
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": name, "strength_model": strength}},
        "6": {"class_type": "LoadImage", "inputs": {"image": sg.upload(args.ref)}},
        "7": {"class_type": "H3ContactSheet", "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": args.prompt,
                                                         "ref_image": ["6", 0], "size": args.size}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": args.seed}},
        # the settings the LoRA was trained against (the node's README): res_multistep / simple, 28 passes
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "10": {"class_type": "BasicScheduler", "inputs": {"model": ["2", 0], "scheduler": "simple", "steps": args.steps, "denoise": 1.0}},
        "11": {"class_type": "BasicGuider", "inputs": {"model": ["2", 0], "conditioning": ["7", 0]}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0],
                                                                 "sigmas": ["10", 0], "latent_image": ["7", 1]}},
        "13": {"class_type": "H3ContactSheetDecode", "inputs": {"vae": ["4", 0], "samples": ["12", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": prefix + "_view"}},
        "15": {"class_type": "SaveImage", "inputs": {"images": ["13", 1], "filename_prefix": prefix + "_sheet"}},
    })
    say(f"engine: {sg.ENGINE} (ComfyUI, MiniMax H3 Ref2VA {w['1']['inputs']['unet_name']} with the Turnaround LoRA at {strength:g}), five views of "
        f"{args.size}x{args.size}, {args.steps} passes")
    try:
        saved, seconds = execute(w, "12")
    finally:
        sg.forget_uploads()
    views, sheet = sorted(saved.get("14", []), key=lambda i: i["filename"]), saved.get("15", [])
    if len(views) != 5 or not sheet:
        sys.exit("ComfyUI finished without the five views")
    base = Path(args.output)
    base.parent.mkdir(parents=True, exist_ok=True)
    outputs = [str(base.parent / f"{base.name}_sheet.png")] + [str(base.parent / f"{base.name}_view{i + 1}.png") for i in range(5)]
    take(sheet[0], Path(outputs[0]))
    for item, target in zip(views, outputs[1:]):
        take(item, Path(target))
    say(json.dumps({"outputs": outputs, "engine": sg.ENGINE, "action": "turnaround", "size": args.size, "forwards": args.steps,
                    "total_s": round(seconds, 1)}))


# ------------------------------------------------------------------------------------------------------------- upscale
def upscale(args) -> None:
    probe = sg.probe_media(args.video)
    if probe["video"] is None:
        sys.exit(f"{args.video} has no picture")
    if not 0.1 <= args.megapixels <= 8:
        sys.exit("--megapixels is between 0.1 and 8")
    server("upscale")
    if UPSCALER not in listed("H3VideoUpsampler", "upscale_model"):
        sys.exit(f"{UPSCALER} is not installed: {SETUP}")
    name, strength = lora_name(args.lora)
    w = {}
    loaders(w)
    w.update({
        "6": {"class_type": "LoadVideo", "inputs": {"file": sg.upload(args.video)}},
        "7": {"class_type": "GetVideoComponents", "inputs": {"video": ["6", 0]}},
        # the plain transformer: the LoRA was trained on it, and the node merges the LoRA itself
        "8": {"class_type": "H3VideoUpsampler", "inputs": {
            "model": ["1", 0], "clip": ["3", 0], "vae": ["4", 0], "images": ["7", 0], "prompt": args.prompt,
            "upscale_model": UPSCALER, "megapixels": args.megapixels, "denoise": args.denoise, "lora": name,
            "lora_strength": strength, "tile_tokens": args.tile_tokens, "sampler_name": "euler"}},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": ["7", 2]}},
        "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": f"studio/up_{uuid.uuid4().hex[:8]}",
                                                     "format": "auto", "codec": "auto"}},
    })
    if probe["audio"]:  # the re-draw hears the clip's sound, and the node hands the same sound back
        w["8"]["inputs"].update(audio=["7", 1], audio_vae=["5", 0])
        w["10"]["inputs"]["audio"] = ["8", 1]
    width, height = probe["video"]
    say(f"engine: {sg.ENGINE} (ComfyUI, H3 Video Upsampler on MiniMax H3 Ref2VA {w['1']['inputs']['unet_name']} with the h3upscale LoRA), {width}x{height} "
        f"to {args.megapixels:g} megapixels, re-draw {args.denoise:g}, tiles of {args.tile_tokens} tokens")
    try:
        saved, seconds = execute(w, "8")
    finally:
        sg.forget_uploads()
    clips = saved.get("11", [])
    if not clips:
        sys.exit("ComfyUI finished without writing the clip")
    target = Path(args.output)
    take(clips[0], target)
    after = sg.probe_media(str(target))
    say(json.dumps({"output": str(target), "engine": sg.ENGINE, "action": "upscale", "from": [width, height],
                    "size": list(after["video"] or ()), "total_s": round(seconds, 1)}))


# --------------------------------------------------------------------------------------------------------- mask, remove
def prepared_source(path: str, folder: Path) -> tuple[Path, dict]:
    """The clip as the remover takes it: 24 frames a second, sides that are multiples of 32, at most 15 seconds."""

    probe = sg.probe_media(path)
    if probe["video"] is None:
        sys.exit(f"{path} has no picture")
    width, height = probe["video"]
    w32, h32 = max(32, width // 32 * 32), max(32, height // 32 * 32)
    if w32 * h32 > sg.MODEL_MAX_PIXELS:  # the model's largest picture: bring it down, sides kept in proportion
        scale = math.sqrt(sg.MODEL_MAX_PIXELS / (w32 * h32))
        w32, h32 = max(32, int(width * scale) // 32 * 32), max(32, int(height * scale) // 32 * 32)
    target = folder / "source.mp4"
    # scaled to cover, then cropped about the centre: nothing is stretched
    chain = (f"fps={sg.FPS},scale={w32}:{h32}:force_original_aspect_ratio=increase:flags=lanczos,crop={w32}:{h32},setsar=1,"
             f"trim=end_frame={15 * sg.FPS}")
    plain = folder / "plain.mp4"
    encode = ["-an", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p"]
    done = subprocess.run([sg._tool("ffmpeg"), "-v", "error", "-y", "-i", path, "-vf", chain, *encode, str(plain)], capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"the clip could not be read: {done.stderr.strip()[-300:]}")
    frames = int(subprocess.run([sg._tool("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                 "stream=nb_read_frames", "-of", "csv=p=0", str(plain)], capture_output=True, text=True).stdout.strip() or 0)
    if frames < 22:
        sys.exit(f"the clip is {frames} frames at 24 a second: the remover works in windows of 22, so it needs at least 22")
    # the clip, then its frames n-2 down to n-1-TAIL: the motion runs back the way it came
    back = (f"[0:v]split[a][b];[b]trim=start_frame={frames - 1 - TAIL}:end_frame={frames - 1},setpts=PTS-STARTPTS,reverse[r];"
            f"[a][r]concat=n=2:v=1[v]")
    done = subprocess.run([sg._tool("ffmpeg"), "-v", "error", "-y", "-i", str(plain), "-filter_complex", back, "-map", "[v]",
                           *encode, str(target)], capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"the clip could not be prepared: {done.stderr.strip()[-300:]}")
    plain.unlink()
    return target, {"width": w32, "height": h32, "frames": frames, "sound": bool(probe["audio"])}


def green_of(frames):
    """Where each frame still shows the mask's green, and which frames do (more than one point in 500)."""

    import numpy as np

    wide = frames.astype(np.int16)
    green = (wide[..., 1] > 170) & (wide[..., 1] - wide[..., 0] > 70) & (wide[..., 1] - wide[..., 2] > 70)
    return green, [int(i) for i in np.nonzero(green.reshape(len(green), -1).mean(1) > 0.002)[0]]


def green_left(clip: Path, size: dict) -> list[int]:
    """The frames of a result that still show the mask's green: a window that did not take the person out."""

    return green_of(read_frames(clip, size["width"], size["height"]))[1]


def mend(clip: Path, size: dict, reach: int = 4) -> tuple[int, int]:
    """Green that flashes back for a frame or two (a fast hand, measured on one frame in ten of a hard clip) is
    painted over from the nearest frames that are clean there: each green point takes the mix of the clean frame
    before and the clean frame after, at most `reach` frames away. A longer run of green is left as it is: there is
    no clean frame near enough to take it from. Returns (frames mended, frames still green). The clip is written
    again only when something was mended."""

    import numpy as np

    frames = read_frames(clip, size["width"], size["height"])
    green, flagged = green_of(frames)
    # A frame that is another picture altogether (measured: the very last frame of a clip came out as a different
    # scene) differs from the frames on both its sides several times more than frames of one shot do. It is replaced
    # whole, the same way.
    small = frames[:, ::8, ::8].astype(np.int16)
    step = np.abs(small[1:] - small[:-1]).reshape(len(frames) - 1, -1).mean(1)
    jump = max(4 * float(np.median(step)), 25.0)          # measured: 60 against 6 to 8 between frames of the shot
    alien = [i for i in range(len(frames)) if (i == 0 or step[i - 1] > jump) and (i == len(frames) - 1 or step[i] > jump)
             and len(frames) > 2]
    for i in alien:
        green[i] = True
    # What a window leaves where it half took the person out is not always the mask's own green: measured at a clip's
    # end, a dull green block the size of a few latent points. A frame whose share of greenish points jumps above the
    # frames before it is mended too, where it is greenish and the frame it is mended from is not.
    wide = frames.astype(np.int16)
    tint = (wide[..., 1] - wide[..., 0] > 14) & (wide[..., 1] - wide[..., 2] > 14)
    level = tint.reshape(len(frames), -1).mean(1)
    for i in range(1, len(frames)):
        calm = [j for j in range(max(0, i - 8), i) if j not in flagged and not green[j].any()]
        if i in alien or not calm or level[i] - float(np.median(level[calm])) < 0.015:
            continue
        stain = tint[i] & ~grown(tint[calm[-1]], 6)
        if stain.mean() > 0.004:
            green[i] |= stain
    flagged = sorted(set(flagged) | set(alien) | {i for i in range(len(frames)) if green[i].mean() > 0.002})
    if not flagged:
        return 0, 0
    out, bad, mended = frames.copy(), set(flagged), 0
    for i in flagged:
        def wider(mask, by):
            for axis in (0, 1):
                wide = mask.copy()
                for step in range(1, by + 1):
                    wide |= np.roll(mask, step, axis) | np.roll(mask, -step, axis)
                mask = wide
            return mask

        # the green and its soft, half-clear fringe: every greenish point within 16 points of real green
        one = frames[i].astype(np.int16)
        tint = (one[..., 1] - one[..., 0] > 14) & (one[..., 1] - one[..., 2] > 14)
        hole = wider(wider(green[i], 16) & (tint | wider(green[i], 5)), 3)
        before = next((j for j in range(i - 1, max(-1, i - 1 - reach), -1) if j not in bad), None)
        after = next((j for j in range(i + 1, min(len(frames), i + 1 + reach)) if j not in bad), None)
        if before is None and after is None:
            continue
        if before is None or after is None:
            fill = frames[before if after is None else after].astype(np.float32)
        else:
            share = (i - before) / (after - before)
            fill = frames[before].astype(np.float32) * (1 - share) + frames[after].astype(np.float32) * share
        soft = hole.astype(np.float32)
        for _ in range(4):                                    # a feathered edge, so the patch has no outline
            soft = (soft + np.roll(soft, 1, 0) + np.roll(soft, -1, 0) + np.roll(soft, 1, 1) + np.roll(soft, -1, 1)) / 5
        soft = np.maximum(soft, hole * 1.0)[..., None]
        out[i] = (frames[i] * (1 - soft) + fill * soft).round().astype(np.uint8)
        mended += 1
    if mended:
        fresh = clip.with_name(clip.stem + ".mended.mp4")
        done = subprocess.run([sg._tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                               f"{size['width']}x{size['height']}", "-r", str(sg.FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
                               "-crf", "14", "-pix_fmt", "yuv420p", str(fresh)], input=out.tobytes(), capture_output=True)
        if done.returncode or not fresh.is_file():
            return 0, len(flagged)
        fresh.replace(clip)
    return mended, len(set(green_of(out)[1]) | {i for i in alien if (out[i] == frames[i]).all()})


def grown(mask, by: int):
    """`mask` made wider by `by` points each way."""

    import numpy as np

    for axis in (0, 1):
        wide = mask.copy()
        for step in range(1, by + 1):
            wide |= np.roll(mask, step, axis) | np.roll(mask, -step, axis)
        mask = wide
    return mask


def write_frames(frames, target: Path, size: dict, crf: int = 14) -> bool:
    done = subprocess.run([sg._tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                           f"{size['width']}x{size['height']}", "-r", str(sg.FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
                           "-crf", str(crf), "-pix_fmt", "yuv420p", str(target)], input=frames.tobytes(), capture_output=True)
    return done.returncode == 0 and target.is_file() and target.stat().st_size > 0


def again(clip: Path, masked: Path, size: dict, lora: tuple[str, float], args, scratch: Path, limit: int) -> tuple[int, int, float]:
    """A second look at the frames the first pass left green on (measured on a hard clip: one frame in six, and a
    run of them where the person fills the picture). Up to `limit` stretches of 22 frames, the worst first, go
    through the remover once more as clips of their own: the first pass's own frames, with the person painted green
    again on the frames that kept green, starting from a frame that came out clean. Another start and another seed
    draw them differently. Where the second look is clean, the person's place in the frame is taken from it; the rest
    of the frame stays as the first pass drew it. Returns (stretches run, frames repaired, seconds)."""

    import numpy as np

    frames = read_frames(clip, size["width"], size["height"]).copy()
    green, flagged = green_of(frames)
    count = len(frames)
    if not flagged or limit < 1 or count < 22:
        return 0, 0, 0.0
    share, bad = green.reshape(count, -1).mean(1), set(flagged)
    todo, starts = set(flagged), []
    while todo and len(starts) < limit:
        worst = max(todo, key=lambda i: share[i])
        first = [s for s in range(max(0, worst - 21), min(worst - 1, count - 22) + 1) if s not in bad]
        if not first:
            todo.discard(worst)
            continue
        start = max(first, key=lambda s: (len(todo & set(range(s, s + 22))), -abs(s + 11 - worst)))
        starts.append(start)
        todo -= set(range(start, start + 22))
    if not starts:
        return 0, 0, 0.0
    person = green_of(read_frames(masked, size["width"], size["height"]))[0]      # where the person is, frame by frame
    source = read_frames(masked, size["width"], size["height"])
    repaired, total = 0, 0.0
    for number, start in enumerate(starts):
        span = frames[start:start + 22].copy()
        for k in range(22):
            if start + k in bad:
                span[k][person[start + k]] = source[start + k][person[start + k]]
        stretch, anchor = scratch / f"again{number}.mp4", scratch / f"again{number}.png"
        if not write_frames(span, stretch, size, 10):
            continue
        from PIL import Image

        Image.fromarray(frames[start]).save(anchor)
        w = {"20": {"class_type": "LoadVideo", "inputs": {"file": sg.upload(str(stretch))}},
             "21": {"class_type": "GetVideoComponents", "inputs": {"video": ["20", 0]}}}
        remover_nodes(w, lora, sg.upload(str(anchor)), ["21", 0], args, 22, args.seed + 101 + number)
        w["37"] = {"class_type": "CreateVideo", "inputs": {"images": ["31", 0], "fps": ["21", 2]}}
        w["38"] = {"class_type": "SaveVideo", "inputs": {"video": ["37", 0], "filename_prefix": f"studio/again_{uuid.uuid4().hex[:8]}",
                                                         "format": "auto", "codec": "auto"}}
        saved, seconds = execute(w, "31")
        total += seconds
        if not saved.get("38"):
            continue
        take(saved["38"][0], scratch / "again.mp4")
        second = read_frames(scratch / "again.mp4", size["width"], size["height"])
        if len(second) < 22:
            continue
        left = green_of(second[:22])[0].reshape(22, -1).mean(1)
        for k in range(1, 22):
            i = start + k
            if i not in bad or left[k] > 0.002 or left[k] >= share[i]:
                continue                                      # the second look kept green there too
            hole = grown(person[i] | green[i], 8).astype(np.float32)
            for _ in range(6):                                # a feathered edge: the two passes' pictures meet softly
                hole = (hole + np.roll(hole, 1, 0) + np.roll(hole, -1, 0) + np.roll(hole, 1, 1) + np.roll(hole, -1, 1)) / 5
            soft = np.maximum(hole, (person[i] | green[i]) * 1.0)[..., None]
            frames[i] = (frames[i] * (1 - soft) + second[k].astype(np.float32) * soft).round().astype(np.uint8)
            bad.discard(i)
            repaired += 1
    if repaired:
        fresh = clip.with_name(clip.stem + ".again.mp4")
        if write_frames(frames, fresh, size):
            fresh.replace(clip)
        else:
            repaired = 0
    return len(starts), repaired, total


def cut(source: Path, target: Path, frames: int, sound: str | None = None) -> bool:
    """`source` cut back to its first `frames` frames, with the sound of the file `sound` when one is given. The
    picture is encoded again: a copy of the stream cuts by the order the frames are stored in, not shown in, and
    measured, that put a frame from after the cut at the clip's end."""

    command = [sg._tool("ffmpeg"), "-v", "error", "-y", "-i", str(source)] + (["-i", sound] if sound else [])
    command += ["-filter_complex", f"[0:v]trim=end_frame={frames},setpts=PTS-STARTPTS[v]", "-map", "[v]"]
    command += (["-map", "1:a:0", "-c:a", "aac", "-b:a", sg.SOUND_BITRATE] if sound else ["-an"])
    command += ["-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-t", f"{frames / sg.FPS:.6f}", "-movflags", "+faststart", str(target)]
    return subprocess.run(command, capture_output=True, text=True).returncode == 0 and target.is_file() and target.stat().st_size > 0


def mask_nodes(w: dict, source: str, person: str, expand: int) -> None:
    """SAM 3.1 finds `person` in the first frame and follows it; the relay's node paints it green (nodes 20 to 27)."""

    w.update({
        "20": {"class_type": "LoadVideo", "inputs": {"file": source}},
        "21": {"class_type": "GetVideoComponents", "inputs": {"video": ["20", 0]}},
        "22": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM}},
        "23": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["22", 1], "text": person}},
        "24": {"class_type": "ImageFromBatch", "inputs": {"image": ["21", 0], "batch_index": 0, "length": 1}},
        # the author's settings: detection threshold 0.4, tracking as in their workflow
        "25": {"class_type": "SAM3_Detect", "inputs": {"model": ["22", 0], "image": ["24", 0], "conditioning": ["23", 0]}},
        "26": {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["21", 0], "model": ["22", 0], "initial_mask": ["25", 0],
                                                         "conditioning": ["23", 0]}},
        "27": {"class_type": "SAM3_TrackToMask", "inputs": {"track_data": ["26", 0]}},
        "28": {"class_type": "H3RelayGreenMask", "inputs": {"images": ["21", 0], "mask": ["27", 0], "expand_pixels": expand}},
    })
    # widget values in the order the author's workflow saved them, by each node's own input names
    info = sg.call("/object_info")
    for node, values in (("25", [0.4, 2, False]), ("26", [0.5, 1, 12]), ("27", [""])):
        spec = info[w[node]["class_type"]]["input"]
        names = [n for group in ("required", "optional") for n, d in spec.get(group, {}).items()
                 if n not in w[node]["inputs"] and not (isinstance(d[0], str) and d[0].isupper() and d[0] not in ("INT", "FLOAT", "STRING", "BOOLEAN"))]
        for name, value in zip(names, values):
            w[node]["inputs"][name] = value


def read_frames(path: Path, width: int, height: int):
    import numpy as np

    raw = subprocess.run([sg._tool("ffmpeg"), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)


def plate(source: Path, matte: Path, size: dict, target: Path) -> float:
    """A first frame without the person, from the clip itself: each masked point of frame 0 takes the median of the
    frames where it is not masked (a still camera shows the background there sooner or later), and what is masked in
    every frame is filled in from its edges. Returns the share of frame 0's mask that other frames could fill."""

    import numpy as np
    from PIL import Image

    frames = read_frames(source, size["width"], size["height"])
    mask = read_frames(matte, size["width"], size["height"])[..., 0] > 127
    count = min(len(frames), len(mask))
    frames, mask = frames[:count], mask[:count]
    grown = mask.copy()                                     # the person's soft edge and a little shadow: 8 points wider
    for axis in (1, 2):
        wide = grown.copy()
        for step in range(1, 9):
            wide |= np.roll(grown, step, axis) | np.roll(grown, -step, axis)
        grown = wide
    mask = grown
    first, hole = frames[0].astype(np.float32), mask[0]
    if not hole.any():
        Image.fromarray(frames[0]).save(target)
        return 1.0
    seen = (~mask).sum(0)                                   # frames in which a point is background
    stack = np.where(mask[..., None], np.nan, frames.astype(np.float32))
    ys, xs = np.nonzero(hole & (seen >= 3))
    out = first.copy()
    if len(ys):
        out[ys, xs] = np.nanmedian(stack[:, ys, xs], axis=0)
    # a moving camera: the median of other frames is the wrong place. Accept it only where it agrees with the first
    # frame around the mask's edge (measured on a ring four points wide)
    left = hole & (seen < 3)
    ring = np.zeros_like(hole)
    for dy, dx in ((0, 4), (0, -4), (4, 0), (-4, 0)):
        ring |= np.roll(hole, (dy, dx), (0, 1))
    ring &= ~hole
    if ring.any():
        still = np.abs(np.nanmedian(stack[:, ring], axis=0) - first[ring]).mean()
        if still > 18:                                       # the scene moves: do not trust other frames
            left, out = hole.copy(), first.copy()
    filled = 1.0 - left.sum() / hole.sum()
    if left.any():                                           # fill from the edges inwards, then smooth what was filled
        known = ~left
        for _ in range(4000):
            if known.all():
                break
            total, weight = np.zeros_like(out), np.zeros(hole.shape, np.float32)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                k = np.roll(known, (dy, dx), (0, 1))
                total += np.where(k[..., None], np.roll(out, (dy, dx), (0, 1)), 0)
                weight += k
            grow = ~known & (weight > 0)
            out[grow] = total[grow] / weight[grow][:, None]
            known |= grow
        for _ in range(60):
            blur = sum(np.roll(out, s, (0, 1)) for s in ((0, 1), (0, -1), (1, 0), (-1, 0))) / 4
            out[left] = blur[left]
    Image.fromarray(out.clip(0, 255).astype(np.uint8)).save(target)
    return float(filled)


def find_person(args, scratch: Path) -> tuple[str, dict, Path, Path]:
    """Upload the prepared clip and run the mask alone; returns (uploaded name, sizes, green clip, matte clip)."""

    source, size = prepared_source(args.video, scratch)
    name = sg.upload(str(source))
    w = {}
    mask_nodes(w, name, args.person, args.expand)
    prefix = f"studio/mask_{uuid.uuid4().hex[:8]}"
    w.update({
        "40": {"class_type": "CreateVideo", "inputs": {"images": ["28", 0], "fps": ["21", 2]}},
        "41": {"class_type": "SaveVideo", "inputs": {"video": ["40", 0], "filename_prefix": prefix + "_green", "format": "auto", "codec": "auto"}},
        "42": {"class_type": "MaskToImage", "inputs": {"mask": ["27", 0]}},
        "43": {"class_type": "CreateVideo", "inputs": {"images": ["42", 0], "fps": ["21", 2]}},
        "44": {"class_type": "SaveVideo", "inputs": {"video": ["43", 0], "filename_prefix": prefix + "_matte", "format": "auto", "codec": "auto"}},
    })
    say(f"engine: {sg.ENGINE} (ComfyUI, SAM 3.1), finding \"{args.person}\" in {size['frames']} frames of {size['width']}x{size['height']}")
    saved, seconds = execute(w, "26")
    if not saved.get("41") or not saved.get("44"):
        sys.exit("ComfyUI finished without the mask")
    green, matte = scratch / "green.mp4", scratch / "matte.mp4"
    for item, target in ((saved["41"][0], green), (saved["44"][0], matte)):   # without the copies of the last frame
        take(item, scratch / "long.mp4")
        if not cut(scratch / "long.mp4", target, size["frames"]):
            (scratch / "long.mp4").replace(target)
    import numpy as np

    covered = read_frames(matte, size["width"], size["height"])[..., 0] > 127
    share = covered.reshape(len(covered), -1).mean(1)
    size.update(mask_s=round(seconds, 1), mask_first=round(float(share[0]), 4), mask_mean=round(float(share.mean()), 4),
                mask_frames=int((share > 0).sum()))
    if share[0] == 0:
        sys.exit(f"nobody matching \"{args.person}\" was found in the first frame: describe the person differently "
                 "(clothes, position), or start the clip where the person is in the picture")
    say(f"mask: {share[0] * 100:.1f}% of the first frame, on {size['mask_frames']} of {len(share)} frames, {seconds:.0f} s")
    return name, size, green, matte


def deliver(made: Path, target) -> None:
    """A file made in the job's scratch folder, moved to where it is kept. The scratch folder is the system's temporary
    one, often a file system of its own (a tmpfs /tmp on Fedora): a rename across file systems fails with EXDEV
    ("Invalid cross-device link"), so the file is copied there and the scratch copy removed."""

    shutil.move(str(made), str(target))


def mask(args) -> None:
    server("mask")
    if SAM not in listed("CheckpointLoaderSimple", "ckpt_name"):
        sys.exit(f"{SAM} is not installed: {SETUP}")
    base = Path(args.output)
    base.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="studio-mask-") as scratch:
            _, size, green, matte = find_person(args, Path(scratch))
            outputs = [str(base.parent / f"{base.name}_mask.mp4"), str(base.parent / f"{base.name}_matte.mp4"),
                       str(base.parent / f"{base.name}_plate.png")]
            size["plate_from_clip"] = round(plate(Path(scratch) / "source.mp4", matte, size, Path(outputs[2])), 3)
            deliver(green, outputs[0]); deliver(matte, outputs[1])
    finally:
        sg.forget_uploads()
    say(json.dumps({"outputs": outputs, "engine": sg.ENGINE, "action": "mask", **size}))


def remover_nodes(w: dict, lora: tuple[str, float], anchor: str, frames_from: list, args, window: int, seed: int) -> None:
    """The author's graph from the green clip on: nodes 2 and 30 to 33. `frames_from` is the green-masked picture."""

    loaders(w, still_vae())
    w.update({
        "2": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["1", 0]}},
        "29": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": lora[0], "strength_model": lora[1]}},
        "30": {"class_type": "LoadImage", "inputs": {"image": anchor}},
        "31": {"class_type": "H3RelayPersonRemover", "inputs": {
            "model": ["29", 0], "clip": ["3", 0], "video_vae": ["4", 0], "audio_vae": ["5", 0], "masked_source": frames_from,
            "clean_first_frame": ["30", 0], "fps": 24.0, "prompt": args.prompt, "seed": seed, "steps": args.steps,
            "window_frames": str(window), "window_controls": "{}"}},
    })
    spec = sg.call("/object_info/MiniMaxH3SigmaShift")["MiniMaxH3SigmaShift"]["input"]["required"]
    for name, value in zip([n for n in spec if n != "model"], (12, 3)):   # video and audio shift, as the author set them
        w["2"]["inputs"][name] = value


def remove(args) -> None:
    if args.window not in WINDOWS:
        sys.exit("--window is 22, 39, 56, ... (17n + 5)")
    server("remove")
    if SAM not in listed("CheckpointLoaderSimple", "ckpt_name"):
        sys.exit(f"{SAM} is not installed: {SETUP}")
    lora = lora_name(args.lora)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {}
    try:
        with tempfile.TemporaryDirectory(prefix="studio-remove-") as scratch:
            scratch = Path(scratch)
            name, size, green, matte = find_person(args, scratch)
            window = args.window if size["frames"] >= args.window else 22
            if args.clean_frame:
                from PIL import Image

                clean = scratch / "clean.png"
                Image.open(args.clean_frame).convert("RGB").resize((size["width"], size["height"]), Image.LANCZOS).save(clean)
                report["clean_frame"] = "given"
            else:
                rough = scratch / "plate.png"
                share = plate(scratch / "source.mp4", matte, size, rough)
                report.update(clean_frame="built", plate_from_clip=round(share, 3))
                # one window of the remover with that plate as its first frame: the model draws the place again, and
                # its first frame is the clean frame the whole run then starts from
                w = {}
                mask_nodes(w, name, args.person, args.expand)
                w["34"] = {"class_type": "ImageFromBatch", "inputs": {"image": ["28", 0], "batch_index": 0, "length": 22}}
                remover_nodes(w, lora, sg.upload(str(rough)), ["34", 0], args, 22, args.seed + 1)
                w["35"] = {"class_type": "ImageFromBatch", "inputs": {"image": ["31", 0], "batch_index": 0, "length": 1}}
                w["36"] = {"class_type": "SaveImage", "inputs": {"images": ["35", 0], "filename_prefix": f"studio/clean_{uuid.uuid4().hex[:8]}"}}
                say("clean first frame: built from the clip, then drawn again by one window of the remover")
                saved, seconds = execute(w, "31")
                if not saved.get("36"):
                    sys.exit("ComfyUI finished without the clean frame")
                clean = scratch / "clean.png"
                take(saved["36"][0], clean)
                report["clean_frame_s"] = round(seconds, 1)
            starts, long = [0], size["frames"] + TAIL     # the relay's own windows over the lengthened clip
            while starts[-1] + window < long:
                starts.append(max(window - 1, min(starts[-1] + window - 1, long - window)))
            w = {}
            mask_nodes(w, name, args.person, args.expand)
            remover_nodes(w, lora, sg.upload(str(clean)), ["28", 0], args, window, args.seed)
            w["37"] = {"class_type": "CreateVideo", "inputs": {"images": ["31", 0], "fps": ["21", 2]}}
            w["38"] = {"class_type": "SaveVideo", "inputs": {"video": ["37", 0], "filename_prefix": f"studio/clean_{uuid.uuid4().hex[:8]}",
                                                             "format": "auto", "codec": "auto"}}
            say(f"engine: {sg.ENGINE} (ComfyUI, MiniMax H3 Ref2VA {w['1']['inputs']['unet_name']} with the Person Remover LoRA at {lora[1]:g}), "
                f"{size['frames']} frames of {size['width']}x{size['height']} in {len(starts)} windows of {window}, {args.steps} passes each")
            saved, seconds = execute(w, "31", len(starts))
            if not saved.get("38"):
                sys.exit("ComfyUI finished without writing the clip")
            silent = scratch / "silent.mp4"
            take(saved["38"][0], silent)
            # cut back to the clip's own length, then green that flashed back for a frame or two painted over
            trimmed = scratch / "trimmed.mp4"
            if not cut(silent, trimmed, size["frames"]):
                silent.replace(trimmed)
            stretches, repaired, extra = again(trimmed, green, size, lora, args, scratch, args.again)
            if stretches:
                seconds += extra
                report.update(second_look=stretches, second_look_frames=repaired, second_look_s=round(extra, 1))
                say(f"check: the first pass left green on some frames; {stretches} stretch{'es' if stretches > 1 else ''} of 22 frames "
                    f"went through the remover again and {repaired} frame{'s' if repaired != 1 else ''} came back clean")
            mended, left = mend(trimmed, size)
            report.update(mended_frames=mended, green_frames=left)
            if mended:
                say(f"check: green had come back on {mended} of {size['frames']} frames; painted over from the frames beside them")
            if left:
                say(f"check: {left} of {size['frames']} frames still show green: that part was not removed. Another seed "
                    "draws those windows again.")
            # under the clip's own sound, as the author's guide has it: the removed person's voice stays in it
            if size["sound"] and not args.silent and subprocess.run(
                    [sg._tool("ffmpeg"), "-v", "error", "-y", "-i", str(trimmed), "-i", args.video, "-map", "0:v:0", "-map", "1:a:0",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", sg.SOUND_BITRATE, "-t", f"{size['frames'] / sg.FPS:.6f}",
                     "-movflags", "+faststart", str(target)], capture_output=True).returncode == 0:
                report["sound"] = "the clip's own"
            else:
                deliver(trimmed, target)
                report["sound"] = "none"
            keep = Path(args.keep) if args.keep else None
            if keep:
                keep.parent.mkdir(parents=True, exist_ok=True)
                deliver(green, keep.parent / f"{keep.name}_mask.mp4")
                deliver(clean, keep.parent / f"{keep.name}_clean.png")
                report["kept"] = [str(keep.parent / f"{keep.name}_mask.mp4"), str(keep.parent / f"{keep.name}_clean.png")]
    finally:
        sg.forget_uploads()
    say(json.dumps({"output": str(target), "engine": sg.ENGINE, "action": "remove", "windows": len(starts), "window": window,
                    "forwards": args.steps, "total_s": round(seconds, 1), **size, **report}))


# ----------------------------------------------------------------------------------------------------------- facefix
def face_source(path: str, folder: Path) -> tuple[Path, dict]:
    """The clip as the face pass takes it: 24 frames a second, a length MiniMax H3 draws (17n + 5, at most 362 frames)
    and a sound track (the audio latent is kept as it is, so a clip without sound is given silence). A clip that is all
    of that already is used as it is."""

    probe = sg.probe_media(path)
    if probe["video"] is None:
        sys.exit(f"{path} has no picture")
    counted = subprocess.run([sg._tool("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                              "stream=nb_read_frames,r_frame_rate", "-of", "json", path], capture_output=True, text=True)
    stream = (json.loads(counted.stdout or "{}").get("streams") or [{}])[0]
    frames, rate = int(stream.get("nb_read_frames") or 0), str(stream.get("r_frame_rate") or "0/1")
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1) if num.replace(".", "").isdigit() and float(den or 1) else 0.0
    if abs(fps - sg.FPS) > 0.01:
        frames = int(frames / fps * sg.FPS) if fps else frames
    usable = [n for n in WINDOWS if n <= frames]
    if not usable:
        sys.exit(f"the clip is {frames} frames at 24 a second: MiniMax H3 draws at least 22")
    length = usable[-1]
    if length == frames and abs(fps - sg.FPS) <= 0.01 and probe["audio"]:
        return Path(path), {"width": probe["video"][0], "height": probe["video"][1], "frames": frames, "trimmed": 0}
    target = folder / "source.mp4"
    command = [sg._tool("ffmpeg"), "-v", "error", "-y", "-i", path]
    if not probe["audio"]:
        command += ["-f", "lavfi", "-i", f"anullsrc=r={sg.AUDIO_RATE}:cl=mono"]
    command += ["-map", "0:v:0", "-map", "0:a:0" if probe["audio"] else "1:a:0", "-vf", f"fps={sg.FPS},trim=end_frame={length},setpts=PTS-STARTPTS",
                "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", sg.SOUND_BITRATE,
                "-t", f"{length / sg.FPS:.6f}", str(target)]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"the clip could not be prepared: {done.stderr.strip()[-300:]}")
    return target, {"width": probe["video"][0], "height": probe["video"][1], "frames": length, "trimmed": frames - length,
                    "silence_added": not probe["audio"]}


# which face a pass follows: H3 FaceRefine's own select modes, chosen once per shot where the face first appears
FACE_PICKS = ("largest_face", "smallest_face", "left_most", "right_most", "centre_most")
FACES = 4                         # people in one Fix faces job: one pass each, chained


def face_pass(w: dict, k: int, base: list, face: str, pick: str, args, model: list, restore: bool = True) -> list:
    """One person's pass, nodes k*100 + n: their face tracked on `base` (the clip, or the previous person's result),
    masked, redrawn from their picture, restored and stitched back. Returns the stitched frames. Shared nodes: the
    loaders (1 to 5), the clip (7), the sound's latent (22), SAM 3.1 (12, 13), the model (25), the sampler and its
    schedule (27, 28) and VOSR 2.0 (32)."""

    n = lambda i: str(k * 100 + i)
    w.update({
        # the author's tracking settings; identity_track off: it needs insightface, which is not installed (the face is
        # chosen once per shot by `pick`, then followed by continuity)
        n(8): {"class_type": "H3FaceTrackCrop", "inputs": {
            "images": base, "detector": FACE_DETECTOR, "confidence": 0.35, "crop_factor": args.crop_factor,
            "canvas_width": args.canvas, "canvas_height": args.canvas, "canvas_mode": "manual", "smooth_window": 21,
            "size_smooth_window": 51, "smooth_method": "gaussian", "size_mode": "per_frame", "identity_track": False,
            "identity_threshold": 0.28, "select": pick, "fallback_detector": "none", "fallback_head_frac": 0.5,
            "select_index": 0, "identity_model": "insightface", "cut_detection": "none", "cut_threshold": 3.0,
            "absent_shots": "off", "X": 0, "Y": 0, "frame_index": 0}},
        n(43): {"class_type": "PixelAIH3FaceSizeFade", "inputs": {
            "transform": [n(8), 1], "full_fix_below_px": args.full_fix_below_px,
            "no_fix_above_px": args.no_fix_above_px, "crop_factor": args.crop_factor,
            "reference_height": 1080}},
        # the face picture, at about 2 megapixels and sides in multiples of 32
        n(9): {"class_type": "LoadImage", "inputs": {"image": sg.upload(face)}},
        n(10): {"class_type": "ResizeImageMaskNode", "inputs": {"input": [n(9), 0], "resize_type": "scale total pixels",
                                                                "resize_type.megapixels": 2.0, "scale_method": "area"}},
        n(11): {"class_type": "ResizeImageMaskNode", "inputs": {"input": [n(10), 0], "resize_type": "scale to multiple",
                                                                "resize_type.multiple": 32, "scale_method": "area"}},
        # the face in the crops, by SAM 3.1, then cleaned and brought to the latent's grid (the author's values)
        n(14): {"class_type": "SAM3_VideoTrack", "inputs": {"images": [n(8), 0], "model": ["12", 0], "conditioning": ["13", 0],
                                                          "detection_threshold": 0.5, "max_objects": 1, "detect_interval": 1}},
        n(15): {"class_type": "SAM3_TrackToMask", "inputs": {"track_data": [n(14), 0], "object_indices": "0"}},
        n(16): {"class_type": "MVEx_MaskCleanup", "inputs": {"masks": [n(15), 0], "threshold": 0.3, "method": "shrink_grow",
                                                           "method.shrink": 12, "method.min_frames": 4, "edge_grow": 16}},
        n(17): {"class_type": "MVEx_MaskToLatentSpace", "inputs": {"masks": [n(16), 0], "vae": ["4", 0], "compression": "auto",
                                                                 "spatial_method": "max", "temporal_method": "max",
                                                                 "grow_spatial": 36, "grow_temporal": 1, "grow_temp_mode": "both"}},
        # the crops as the latent to start from, drawn again only inside the mask; the sound's latent is kept whole
        n(18): {"class_type": "VAEEncode", "inputs": {"pixels": [n(8), 0], "vae": ["4", 0]}},
        n(19): {"class_type": "SetLatentNoiseMask", "inputs": {"samples": [n(18), 0], "mask": [n(17), 0]}},
        n(23): {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": [n(19), 0], "audio_latent": ["22", 0]}},
        n(24): {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["3", 0], "vae": ["4", 0], "audio_vae": ["5", 0], "ref_images.ref_image_0": [n(11), 0], "prompt": args.prompt,
            "width": [n(8), 4], "height": [n(8), 5], "length": [n(8), 6], "ref_image_size": "match"}},
        n(26): {"class_type": "RandomNoise", "inputs": {"noise_seed": args.seed + k - 1}},
        n(29): {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": [n(24), 0]}},
        n(30): {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": [n(26), 0], "guider": [n(29), 0], "sampler": ["27", 0],
                                                                "sigmas": ["28", 0], "latent_image": [n(23), 0]}},
        n(31): {"class_type": "VAEDecode", "inputs": {"samples": [n(30), 0], "vae": ["4", 0]}},
        n(34): {"class_type": "H3FaceStitch", "inputs": {"base_images": base, "refined_crops": [n(33), 0] if restore else [n(31), 0], "transform": [n(43), 0],
                                                       "masks": [n(15), 0], "paste_region": "face_only", "mask_dilation": 24,
                                                       "feather": 24, "colour_match": 1.0, "blend": 1.0,
                                                       "undetected_frames": "fade_out", "feather_scales_with_crop": False}},
    })
    if restore:  # VOSR needs another 3 GB beside H3; on a small machine stitch the H3 crop directly
        w[n(33)] = {"class_type": "VOSR2Upscale", "inputs": {"model": ["32", 0], "image": [n(31), 0], "upscale": 1, "seed": args.seed,
                                                     "color_alignment": "wavelet", "tile_size": 512, "tile_overlap": 32,
                                                     "vae_tile_size": 1024, "vae_tile_overlap": 32}}
    if args.keep:  # this person's crops before and after, side by side, to see what the pass did to the face
        w[n(37)] = {"class_type": "ImageStitch", "inputs": {"image1": [n(8), 0], "image2": [n(33), 0] if restore else [n(31), 0], "direction": "right",
                                                            "match_image_size": True, "spacing_width": 0, "spacing_color": "white"}}
        w[n(38)] = {"class_type": "CreateVideo", "inputs": {"images": [n(37), 0], "fps": ["7", 2]}}
        w[n(39)] = {"class_type": "SaveVideo", "inputs": {"video": [n(38), 0], "filename_prefix": f"studio/facecrops{k}_{uuid.uuid4().hex[:8]}",
                                                          "format": "auto", "codec": "auto"}}
    return [n(34), 0]


def facefix_staged(args, source: Path, size: dict, faces: list, picks: list, lora, target: Path,
                   scratch: Path, restore: bool) -> None:
    """On a small card, save each face's latent before its video decoder loads. A fresh ComfyUI then
    decodes and stitches it; another fresh server starts the next face. H3 and the decoder never share
    the memory peak that stopped a 243-frame, two-face job on a 32 GB machine."""

    current, total, kept = source, 0.0, []
    for k, (face, pick) in enumerate(zip(faces, picks), 1):
        say(f"face {k}/{len(faces)}: drawing the face into a latent")
        w = {}
        loaders(w, lora=bool(lora), transformer=args.transformer)
        say(f"video model: {w['1']['inputs']['unet_name']}")
        model = ["1", 0]
        if lora:
            w["2"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": lora[0], "strength_model": lora[1]}}
            model = ["2", 0]
        w.update({
            "6": {"class_type": "LoadVideo", "inputs": {"file": sg.upload(str(current))}},
            "7": {"class_type": "GetVideoComponents", "inputs": {"video": ["6", 0]}},
            "12": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM}},
            "13": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["12", 1], "text": "face"}},
            "20": {"class_type": "VAEEncodeAudio", "inputs": {"audio": ["7", 1], "vae": ["5", 0]}},
            "21": {"class_type": "SolidMask", "inputs": {"value": 0.0, "width": 32, "height": 32}},
            "22": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["20", 0], "mask": ["21", 0]}},
            "25": {"class_type": "DifferentialDiffusion", "inputs": {"model": model, "strength": 1.0}},
            "27": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "28": {"class_type": "BasicScheduler", "inputs": {"model": ["25", 0], "scheduler": "beta",
                                                         "steps": args.steps, "denoise": args.denoise}},
        })
        face_pass(w, 1, ["7", 0], face, pick, args, ["25", 0], restore=False)
        stitch = w["134"]
        for node in ("131", "134", "137", "138", "139"):
            w.pop(node, None)  # only the latent is saved in this server; decoding is the next stage
        # The sampler returns a NestedTensor of (video, audio). ComfyUI's SaveLatent accepts one plain tensor;
        # the original clip supplies the audio again when the decoded face is stitched into it.
        w["142"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["130", 0]}}
        w["140"] = {"class_type": "SaveLatent", "inputs": {"samples": ["142", 0],
                                                        "filename_prefix": f"studio/face_latent_{uuid.uuid4().hex[:8]}"}}
        saved, seconds = execute(w, "130")
        total += seconds
        if not saved.get("140"):
            sys.exit("ComfyUI finished without saving the face latent")
        item = saved["140"][0]
        latent_output = sg.PREFIX / "ComfyUI" / "output" / item.get("subfolder", "") / item["filename"]
        latent_name = f"studio_face_{uuid.uuid4().hex[:10]}.latent"
        latent_input = sg.PREFIX / "ComfyUI" / "input" / latent_name
        shutil.copy2(latent_output, latent_input)
        try:
            sg.fresh_server("release H3 before decoding the refined face")
            server("facefix")
            say(f"face {k}/{len(faces)}: decoding and stitching on a fresh server")
            stage = {node: w[node] for node in ("4", "12", "13", "108", "114", "115", "143")}
            stage["134"] = stitch
            stage["6"] = {"class_type": "LoadVideo", "inputs": {"file": sg.upload(str(current))}}
            stage["7"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["6", 0]}}
            stage["141"] = {"class_type": "LoadLatent", "inputs": {"latent": latent_name}}
            stage["131"] = {"class_type": "VAEDecode", "inputs": {"samples": ["141", 0], "vae": ["4", 0]}}
            if restore:
                stage["32"] = {"class_type": "VOSR2ModelLoader", "inputs": {"model": "VOSR2", "dtype": "default"}}
                stage["133"] = {"class_type": "VOSR2Upscale", "inputs": {"model": ["32", 0], "image": ["131", 0],
                                                              "upscale": 1, "seed": args.seed, "color_alignment": "wavelet",
                                                              "tile_size": 512, "tile_overlap": 32,
                                                              "vae_tile_size": 1024, "vae_tile_overlap": 32}}
                stage["134"]["inputs"]["refined_crops"] = ["133", 0]
            if args.keep:
                stage["137"] = {"class_type": "ImageStitch", "inputs": {"image1": ["108", 0],
                    "image2": ["133", 0] if restore else ["131", 0], "direction": "right",
                    "match_image_size": True, "spacing_width": 0, "spacing_color": "white"}}
                stage["138"] = {"class_type": "CreateVideo", "inputs": {"images": ["137", 0], "fps": ["7", 2]}}
                stage["139"] = {"class_type": "SaveVideo", "inputs": {"video": ["138", 0],
                    "filename_prefix": f"studio/facecrops{k}_{uuid.uuid4().hex[:8]}", "format": "auto", "codec": "auto"}}
            stage["35"] = {"class_type": "CreateVideo", "inputs": {"images": ["134", 0], "audio": ["7", 1], "fps": ["7", 2]}}
            stage["36"] = {"class_type": "SaveVideo", "inputs": {"video": ["35", 0],
                "filename_prefix": f"studio/face_{uuid.uuid4().hex[:8]}", "format": "auto", "codec": "auto"}}
            saved, seconds = execute(stage, "134")
            total += seconds
            if not saved.get("36"):
                sys.exit("ComfyUI finished without writing the stitched face clip")
            current = target if k == len(faces) else scratch / f"face{k}.mp4"
            take(saved["36"][0], current)
            if args.keep and saved.get("139"):
                keep = Path(args.keep)
                keep.parent.mkdir(parents=True, exist_ok=True)
                name = keep.parent / (f"{keep.name}_crops.mp4" if k == 1 else f"{keep.name}{k}_crops.mp4")
                take(saved["139"][0], name)
                kept.append(str(name))
        finally:
            latent_input.unlink(missing_ok=True)
            latent_output.unlink(missing_ok=True)
        if k < len(faces):
            sg.fresh_server("release the decoder before drawing the next face")
            server("facefix")
    after = sg.probe_media(str(target))
    say(json.dumps({"output": str(target), "engine": sg.ENGINE, "action": "facefix", "canvas": args.canvas,
                    "forwards": args.steps, "denoise": args.denoise, "size": list(after["video"] or ()),
                    "total_s": round(total, 1), **size, "faces": len(faces), "select": picks,
                    "restoration": "vosr2" if restore else "h3", "kept": kept}))


def facefix(args) -> None:
    """Carasibana's H3 FaceRefine pass on a finished clip, as the 2026-11-18 "perfect face refine" workflow wires it:
    the face is found and tracked on every frame and cropped to a square canvas so it fills it (H3FaceTrackCrop); SAM 3.1
    finds the face in the crops, drozbay's MaskVidExperiments cleans that mask and brings it to the latent's grid; the
    crops are drawn again by MiniMax H3 Ref2VA from the face picture (<Picture 1>), at half strength, only inside the
    mask, the sound's latent kept untouched; VOSR 2.0 restores the refined crops at their own size, and H3FaceStitch
    pastes the face back into the clip with a feathered, colour-matched edge; PixelAI Labs' face size fade keeps large
    close-up faces as they were. The clip's own sound is kept.
    Several people: one pass each, with each face stitched into the clip before the next pass. On a small machine,
    sampling and decoding use separate ComfyUI servers so their model memory does not overlap."""

    faces, picks = args.face, args.select or []
    if not 1 <= len(faces) <= FACES:
        sys.exit(f"--face is given once a person, 1 to {FACES} times")
    picks = picks + [None] * (len(faces) - len(picks))
    if len(picks) != len(faces):
        sys.exit("--select is given at most once a face")
    # unnamed: one face is the largest; several are taken left to right (two: left_most then right_most)
    default = ["largest_face"] if len(faces) == 1 else ["left_most", "right_most", "centre_most", "largest_face"]
    picks = [one or default[i] for i, one in enumerate(picks)]
    if any(one not in FACE_PICKS for one in picks):
        sys.exit(f"--select is one of {', '.join(FACE_PICKS)}")
    if len(set(picks)) != len(picks):
        sys.exit("each face needs its own --select, or two passes follow the same person")
    if args.canvas not in CANVASES:
        sys.exit(f"--canvas is one of {', '.join(map(str, CANVASES))}")
    if not 0.1 <= args.denoise <= 0.9 or not 2 <= args.steps <= 40:
        sys.exit("--denoise is between 0.1 and 0.9 and --steps between 2 and 40")
    if not 8 <= args.full_fix_below_px < args.no_fix_above_px <= 4096:
        sys.exit("face size fade needs 8 <= --full-fix-below-px < --no-fix-above-px <= 4096")
    for face in faces:
        if not Path(face).is_file():
            sys.exit(f"no picture at {face}")
    server("facefix")
    if SAM not in listed("CheckpointLoaderSimple", "ckpt_name"):
        sys.exit(f"{SAM} is not installed: {SETUP}")
    if FACE_DETECTOR not in listed("H3FaceTrackCrop", "detector"):
        sys.exit(f"the face detector {FACE_DETECTOR} is not installed: {SETUP}")
    bundle = sg.PREFIX / "ComfyUI" / "models" / "vosr2" / "VOSR2"
    if any(not (bundle / part).is_file() for part in VOSR2_FILES):
        # the loader would fetch 7 GB at run time, inside the render: the setup fetches it, checked, beforehand
        sys.exit(f"VOSR 2.0 is not installed in {bundle}: {SETUP}")
    lora = lora_name(args.lora) if args.lora else None
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="studio-facefix-") as scratch:
            source, size = face_source(args.video, Path(scratch))
            ram_mb = sg.memory_guard.meminfo().get("MemTotal", 0)
            restore = args.restore == "on" or (args.restore == "auto" and (not ram_mb or ram_mb >= 48 * 1024))
            if not restore:
                say("VOSR 2.0 restoration skipped: stitching the H3-refined crops directly"
                    + (f" ({ram_mb // 1024} GB RAM; automatic mode reserves VOSR for 48 GB or more)" if args.restore == "auto" and ram_mb else ""))
            if ram_mb and ram_mb < 48 * 1024:
                say("small-machine route: saving each face latent, then restarting ComfyUI before its decoder loads")
                facefix_staged(args, source, size, faces, picks, lora, target, Path(scratch), restore)
                return
            w = {}
            loaders(w, lora=bool(lora), transformer=args.transformer)
            model = ["1", 0]
            if lora:
                w["2"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": lora[0], "strength_model": lora[1]}}
                model = ["2", 0]
            w.update({
                "6": {"class_type": "LoadVideo", "inputs": {"file": sg.upload(str(source))}},
                "7": {"class_type": "GetVideoComponents", "inputs": {"video": ["6", 0]}},
                "12": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM}},
                "13": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["12", 1], "text": "face"}},
                "20": {"class_type": "VAEEncodeAudio", "inputs": {"audio": ["7", 1], "vae": ["5", 0]}},
                "21": {"class_type": "SolidMask", "inputs": {"value": 0.0, "width": 32, "height": 32}},
                "22": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["20", 0], "mask": ["21", 0]}},
                "25": {"class_type": "DifferentialDiffusion", "inputs": {"model": model, "strength": 1.0}},
                "27": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
                # 8 passes over the lower half of the noise, the author's schedule (beta)
                "28": {"class_type": "BasicScheduler", "inputs": {"model": ["25", 0], "scheduler": "beta", "steps": args.steps,
                                                                "denoise": args.denoise}},
            })
            if restore:
                w["32"] = {"class_type": "VOSR2ModelLoader", "inputs": {"model": "VOSR2", "dtype": "default"}}
            frames = ["7", 0]
            for k, (face, pick) in enumerate(zip(faces, picks), 1):
                frames = face_pass(w, k, frames, face, pick, args, ["25", 0], restore=restore)
            w["35"] = {"class_type": "CreateVideo", "inputs": {"images": frames, "audio": ["7", 1], "fps": ["7", 2]}}
            w["36"] = {"class_type": "SaveVideo", "inputs": {"video": ["35", 0], "filename_prefix": f"studio/face_{uuid.uuid4().hex[:8]}",
                                                             "format": "auto", "codec": "auto"}}
            unet = w["1"]["inputs"]["unet_name"]
            say(f"engine: {sg.ENGINE} (ComfyUI, H3 FaceRefine + MaskVidExperiments" + (" + VOSR 2.0" if restore else "") + f" on MiniMax H3 Ref2VA {unet}"
                + (f" with {lora[0]} at {lora[1]:g}" if lora else "") + f"), {size['frames']} frames of {size['width']}x{size['height']}, "
                f"{len(faces)} face{'s' if len(faces) > 1 else ''} ({', '.join(picks)}), crops of {args.canvas}x{args.canvas}, "
                f"{args.steps} passes at {args.denoise:g}")
            if size.get("trimmed"):
                say(f"note: the clip is cut to {size['frames']} frames, the longest length MiniMax H3 draws within it "
                    f"({size['trimmed']} frame{'s' if size['trimmed'] != 1 else ''} left off the end)")
            saved, seconds = execute(w, [str(k * 100 + 30) for k in range(1, len(faces) + 1)])
            if not saved.get("36"):
                sys.exit("ComfyUI finished without writing the clip")
            take(saved["36"][0], target)
            report = {"faces": len(faces), "select": picks, "restoration": "vosr2" if restore else "h3"}
            if args.keep:
                keep = Path(args.keep)
                keep.parent.mkdir(parents=True, exist_ok=True)
                kept = []
                for k in range(1, len(faces) + 1):
                    item = saved.get(str(k * 100 + 39))
                    if item:
                        name = keep.parent / (f"{keep.name}_crops.mp4" if k == 1 else f"{keep.name}{k}_crops.mp4")
                        take(item[0], name)
                        kept.append(str(name))
                report["kept"] = kept
    finally:
        sg.forget_uploads()
    after = sg.probe_media(str(target))
    say(json.dumps({"output": str(target), "engine": sg.ENGINE, "action": "facefix", "canvas": args.canvas, "forwards": args.steps,
                    "denoise": args.denoise, "size": list(after["video"] or ()), "total_s": round(seconds, 1), **size, **report}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    t = sub.add_parser("turnaround")
    t.add_argument("--ref", required=True); t.add_argument("--lora", required=True); t.add_argument("-o", "--output", required=True)
    t.add_argument("--size", type=int, default=1024, help="each view's side: 512 (what the LoRA was trained at), 1024 or 2048")
    t.add_argument("--prompt", default=TURN_PROMPT); t.add_argument("--seed", type=int, default=0)
    t.add_argument("--steps", type=int, default=28)
    u = sub.add_parser("upscale")
    u.add_argument("--video", required=True); u.add_argument("--lora", required=True); u.add_argument("-o", "--output", required=True)
    u.add_argument("--megapixels", type=float, default=2.0, help="the result's area, sides kept in proportion (2 is about 1920x1080)")
    u.add_argument("--denoise", type=float, default=0.1, help="how hard the one re-draw works: 0.1 is the author's tested setting")
    u.add_argument("--tile-tokens", type=int, default=70000, help="memory: the longest row of one tile (the author's default)")
    u.add_argument("--prompt", default=UPSCALE_PROMPT)
    for name in ("mask", "remove"):
        m = sub.add_parser(name)
        m.add_argument("--video", required=True); m.add_argument("--person", required=True, help="a few words: man in a gray shirt")
        m.add_argument("-o", "--output", required=True)
        m.add_argument("--expand", type=int, default=5, help="points the mask grows by (the author's 5)")
        if name == "remove":
            m.add_argument("--lora", required=True); m.add_argument("--clean-frame", default="")
            m.add_argument("--window", type=int, default=22); m.add_argument("--steps", type=int, default=12)
            m.add_argument("--seed", type=int, default=904234); m.add_argument("--prompt", default=REMOVE_PROMPT)
            m.add_argument("--silent", action="store_true", help="leave the clip's own sound out")
            m.add_argument("--again", type=int, default=3, help="stretches of 22 frames to run a second time where green was left (0: none)")
            m.add_argument("--keep", default="", help="folder/name: keep the green clip and the clean frame beside the result")
    f = sub.add_parser("facefix")
    f.add_argument("--video", required=True)
    f.add_argument("--face", required=True, action="append", help="a clear picture of a face (<Picture 1>); once a person, up to 4")
    f.add_argument("--select", action="append", choices=FACE_PICKS,
                   help="which face the --face of the same place follows (default: the largest; several: left to right)")
    f.add_argument("-o", "--output", required=True)
    f.add_argument("--canvas", type=int, default=512, help="the face crop's side, 512 (safer on 32 GB), 640 or 768 (the author's)")
    f.add_argument("--crop-factor", type=float, default=2.5, help="the crop's side as a multiple of the face's height (the author's 2.5)")
    f.add_argument("--full-fix-below-px", type=int, default=110, help="full face fix at this height or smaller, at 1080p")
    f.add_argument("--no-fix-above-px", type=int, default=180, help="keep the original face at this height or larger, at 1080p")
    f.add_argument("--steps", type=int, default=8); f.add_argument("--denoise", type=float, default=0.5)
    f.add_argument("--seed", type=int, default=983546012546031); f.add_argument("--prompt", default="",
                                                                               help="the author's is empty: the face picture alone guides the pass")
    f.add_argument("--lora", default="", help="file[:strength], merged into the Ref2VA transformer for the pass")
    f.add_argument("--transformer", default="", help="another Ref2VA checkpoint listed by ComfyUI")
    f.add_argument("--restore", choices=("auto", "on", "off"), default="auto", help="VOSR 2.0 restoration: auto reserves it for 48 GB RAM or more")
    f.add_argument("--keep", default="", help="folder/name: keep the face crops before and after, side by side")
    args = parser.parse_args()
    try:
        {"turnaround": turnaround, "upscale": upscale, "mask": mask, "remove": remove, "facefix": facefix}[args.action](args)
    except Exception as error:  # ComfyUI's memory guard stopped the server: show its reason, not urllib's traceback
        if not isinstance(error, (OSError, ConnectionError)) and type(error).__module__.split(".")[0] != "aiohttp":
            raise
        why = sg.stopped()
        if why and args.action == "facefix" and "memory" in why:
            why = why.split(". Use a shorter clip", 1)[0] + ". Try a 512 face crop, one face at a time, or H3 redraw only."
        sys.exit(why or f"ComfyUI stopped answering during the render: {error} (log: {sg.side('log')})")
    finally:
        sg.mac_release()


if __name__ == "__main__":
    main()
