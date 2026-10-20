"""Three Library actions on a DGX Spark, each through its author's own ComfyUI nodes (spark/setup-workflows.sh).

    spark_workflows.py turnaround --ref subject.png --lora turnaround.safetensors -o folder/name
    spark_workflows.py upscale    --video clip.mp4 --lora video-upscaler.safetensors -o bigger.mp4
    spark_workflows.py mask       --video clip.mp4 --person "man in a gray shirt" -o folder/name
    spark_workflows.py remove     --video clip.mp4 --person "man in a gray shirt" --lora person-remover.safetensors -o clean.mp4

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

All three render with MiniMax H3 Ref2VA in bf16: the LoRAs are merged at run time, which spoils the 8-bit file.
"""
import argparse
import asyncio
import json
import math
import os
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
         "remove": ("SAM3_Detect", "SAM3_VideoTrack", "SAM3_TrackToMask", "H3RelayGreenMask", "H3RelayPersonRemover")}


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


def comfy_pid() -> str:
    port = sg.URL.rsplit(":", 1)[-1]
    return subprocess.run(["pgrep", "-f", f"[m]ain.py --listen 127.0.0.1 --port {port}"], capture_output=True, text=True).stdout.split("\n")[0].strip()


def server(action: str) -> None:
    """ComfyUI up with nothing of another kind loaded, and the action's nodes and files present."""

    last = sg.STATE.read_text().strip() if sg.STATE.exists() else ""
    other = PATCHED.read_text().split() if PATCHED.exists() else []
    clash = action in PACK and len(other) == 2 and other[0] != PACK[action] and other[1] == comfy_pid()
    # a server another kind of job used (a clip, a picture model, a song) starts again empty: ComfyUI kept FLUX3-K's
    # weights beside the turnaround's Ref2VA transformer and text encoder (64 GB) and the memory floor stopped it
    # (2026-10-20, a DGX Spark); as for a reference clip, TensorFold's engine keeps its 8-bit weights until it is gone
    if sg.up() and ((last and not last.startswith("workflow-")) or clash):
        say("restarting ComfyUI: this starts from a server with nothing loaded" if not clash else
            "restarting ComfyUI: the upscaler's and the remover's node packs do not share one server")
        subprocess.run(["bash", str(sg.COMFY_SH), "stop"], check=False)
        for _ in range(60):
            if not sg.up():
                break
            time.sleep(1)
        time.sleep(5)
    sg.ensure_server("workflow-" + action)
    have = sg.call("/object_info")
    missing = [n for n in NODES[action] if n not in have]
    if missing:
        sys.exit(f"ComfyUI has no node {', '.join(missing)}: run `bash {'scripts/setup-comfy.sh' if sg.MAC else 'spark/setup-workflows.sh'}`, "
                 f"then `bash {'mac' if sg.MAC else 'spark'}/comfy.sh stop`")
    if sg.REF2VA["bf16"] not in listed("UNETLoader", "unet_name"):
        sys.exit("MiniMax H3 Ref2VA is not installed: bash oneshot-setup-spark.sh --ref2va")
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
    if not link.exists() or link.resolve() != source:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(source)
    if link.name not in listed("LoraLoaderModelOnly", "lora_name"):
        sys.exit(f"ComfyUI does not list {link.name}")
    return link.name, strength


async def follow(workflow: dict, sampler: str, parts: int) -> str:
    """Queue a workflow and print the progress of `sampler` as `[tensorfold] step i/n` until it ends; returns its id.

    A node that expands into a graph of its own (the remover: one sampler a window) reports its inner nodes as
    `sampler.x.y`: with `parts` such samplers expected, their steps are counted as one run."""

    sg.gate()
    import aiohttp

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
                if event["type"] == "progress" and (node == sampler or node.startswith(sampler + ".")):
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


def execute(workflow: dict, sampler: str, parts: int = 1) -> tuple[dict, float]:
    """Run a workflow; returns ({node id: [saved file records]}, seconds)."""

    started = time.time()
    try:
        prompt_id = asyncio.run(follow(workflow, sampler, parts))
    except KeyboardInterrupt:
        sg.call("/interrupt", {})
        raise
    while True:
        history = sg.call(f"/history/{prompt_id}")
        if prompt_id in history and history[prompt_id].get("status", {}).get("completed") is not None:
            break
        time.sleep(1)
    record = history[prompt_id]
    if record["status"].get("status_str") != "success":
        errors = [m[1] for m in record["status"].get("messages", []) if m[0] == "execution_error"]
        detail = errors[0].get("exception_message", "").strip() if errors else json.dumps(record["status"])[:600]
        sys.exit(f"ComfyUI failed: {detail} (log: {sg.PREFIX}/comfy.log)")
    saved = {}
    for node, output in record["outputs"].items():
        saved[node] = [item for items in output.values() for item in (items if isinstance(items, list) else [])
                       if isinstance(item, dict) and item.get("filename") and item.get("type", "output") == "output"]
    return saved, time.time() - started


def take(item: dict, target: Path) -> None:
    """Fetch a saved file and remove ComfyUI's copy."""

    sg.fetch(item, target)
    (sg.PREFIX / "ComfyUI" / "output" / item.get("subfolder", "") / item["filename"]).unlink(missing_ok=True)


def loaders(w: dict, vae: str | None = None) -> None:
    w["1"] = {"class_type": "UNETLoader", "inputs": {"unet_name": sg.REF2VA["bf16"], "weight_dtype": "default"}}
    w["3"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": sg.H3_TEXT, "type": "minimax", "device": "default"}}
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
    say(f"engine: {sg.ENGINE} (ComfyUI, MiniMax H3 Ref2VA bf16 with the Turnaround LoRA at {strength:g}), five views of "
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
        sys.exit(f"{UPSCALER} is not installed: bash spark/setup-workflows.sh")
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
    say(f"engine: {sg.ENGINE} (ComfyUI, H3 Video Upsampler on MiniMax H3 Ref2VA bf16 with the h3upscale LoRA), {width}x{height} "
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


def mask(args) -> None:
    server("mask")
    if SAM not in listed("CheckpointLoaderSimple", "ckpt_name"):
        sys.exit(f"{SAM} is not installed: bash spark/setup-workflows.sh")
    base = Path(args.output)
    base.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="studio-mask-") as scratch:
            _, size, green, matte = find_person(args, Path(scratch))
            outputs = [str(base.parent / f"{base.name}_mask.mp4"), str(base.parent / f"{base.name}_matte.mp4"),
                       str(base.parent / f"{base.name}_plate.png")]
            size["plate_from_clip"] = round(plate(Path(scratch) / "source.mp4", matte, size, Path(outputs[2])), 3)
            green.replace(outputs[0]); matte.replace(outputs[1])
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
        sys.exit(f"{SAM} is not installed: bash spark/setup-workflows.sh")
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
            say(f"engine: {sg.ENGINE} (ComfyUI, MiniMax H3 Ref2VA bf16 with the Person Remover LoRA at {lora[1]:g}), "
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
                trimmed.replace(target)
                report["sound"] = "none"
            keep = Path(args.keep) if args.keep else None
            if keep:
                keep.parent.mkdir(parents=True, exist_ok=True)
                green.replace(keep.parent / f"{keep.name}_mask.mp4")
                clean.replace(keep.parent / f"{keep.name}_clean.png")
                report["kept"] = [str(keep.parent / f"{keep.name}_mask.mp4"), str(keep.parent / f"{keep.name}_clean.png")]
    finally:
        sg.forget_uploads()
    say(json.dumps({"output": str(target), "engine": sg.ENGINE, "action": "remove", "windows": len(starts), "window": window,
                    "forwards": args.steps, "total_s": round(seconds, 1), **size, **report}))


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
    args = parser.parse_args()
    try:
        {"turnaround": turnaround, "upscale": upscale, "mask": mask, "remove": remove}[args.action](args)
    finally:
        sg.mac_release()


if __name__ == "__main__":
    main()
