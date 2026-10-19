"""TensorFold Studio on a DGX Spark (CUDA): FastH3 video with sound, Qwen-Image-2.1 pictures, MiniMax Music 3 songs, sound effects and foley through ComfyUI.

    spark_generate.py video --prompt "..." -o clip.mp4 [--first-frame photo.jpg] [--last-frame end.jpg] [--steps 8] [--frames 124]
    spark_generate.py video --prompt "Use <Picture 1> as the person. ..." --ref face.png -o clip.mp4
    spark_generate.py video --prompt "<Picture 1> moves as in <Video 1>. ..." --ref face.png --ref-video move.mp4 -o clip.mp4
    spark_generate.py image --prompt "..." -o picture.png [--seeds 1,2,3] [--base --steps 40]
    spark_generate.py music --caption-file style.txt --lyrics-file lyrics.txt --seconds 60 --seed 7 -o song.wav
    spark_generate.py sfx --prompt "rain on a tin roof" --seconds 8 -o rain.wav [--take rain.take.json --home STUDIO]
    spark_generate.py foley --video clip.mp4 [--prompt "footsteps on gravel"] -o clip.wav --clip-out clip.foley.mp4

It talks to a ComfyUI server that it starts on first use (spark/comfy.sh) and leaves running with the models
loaded, so the next render skips the load. `bash spark/comfy.sh stop` frees the memory.

Credit: FastVideo (Hao AI Lab) for FastH3 and its sparse attention, MiniMax for H3 and MiniMax-Music3, the Qwen
team for Qwen-Image-2.1-Turbo, Stability AI for Stable Audio Open, Ho Kei Cheng et al. for MMAudio and kijai for its
ComfyUI nodes, ComfyUI for the runtime.
"""
import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import memory_guard  # noqa: E402  (what a clip needs and whether this machine has it, beside this file)
PREFIX = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))
URL = os.environ.get("SPARK_COMFY_URL", "http://127.0.0.1:" + os.environ.get("SPARK_COMFY_PORT", "8190"))  # the port spark/comfy.sh starts it on
STATE = PREFIX / "comfy.kind"
ENGINE_LIB = PREFIX / "tf-h3" / "libtf_h3.so"
MODELS = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models"))

FASTH3 = {"bf16": "fastvideo_fasth3_8step_v2_pruned_bf16.safetensors",
          "int8": "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"}
# MiniMax H3 Ref2VA: reference pictures hold a face or a subject through the clip. The adapter (LightX2V's Ref2VA Turbo,
# 4 passes) is merged at run time, which only works on the bf16 file: on the 8-bit one the weights are rotated and an
# adapter added to them silently spoils the picture. Without the adapter the 8-bit file runs the full 20 steps.
REF2VA = {"bf16": "minimax_h3_ref2va_pruned_bf16.safetensors",
          "int8": "minimax_h3_ref2va_pruned_int8_convrot.safetensors"}
REF2VA_TURBO = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
# MiniMax H3's own first-and-last-frame transformer (not FastH3): an optional part of the install, for LoRAs that were
# trained on it and for clips that should run on the undistilled model (--base h3)
H3_FL2VA = "minimax_h3_fl2va_pruned_bf16.safetensors"
H3_TEXT = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
TEXT_TAG = ""
# ComfyUI's 8-bit video decoder: half the decode time of the fp16 one on a GB10 (14 s against 26 s for 124 frames of
# 864x480), the same picture to 42 to 44 dB. SPARK_VAE=minimax_h3_video_vae_fp16.safetensors selects the other.
H3_VAE = os.environ.get("SPARK_VAE", "minimax_h3_video_vae_int8_convrot.safetensors")
H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
QWEN = "qwen_image_2.1_turbo_bf16.safetensors"   # Qwen-Image-2.1-Turbo, the 8-step checkpoint
QWEN_BASE = "qwen_image_2.1_bf16.safetensors"    # the 40-step base model, only when it has been downloaded
QWEN_TEXT = "qwen3vl_8b_int8_convrot.safetensors"
QWEN_VAE = "qwen_image_2.1_vae_bf16.safetensors"
# the schedule saved with the Turbo checkpoint (model_index.json, sample_sigmas), then zero
TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0"
# FLUX3-K (Black Forest Labs' FLUX 3 Image Klein, ComfyUI's own FLUX 3 nodes): Fast is the distilled checkpoint (16 steps,
# no guidance), Full the base one (50 steps at CFG 3.5), the settings of BFL's model cards. Both take full scene captions:
# BFL's prompt upsampler (Qwen3.5 9B, 8-bit) writes one from the prompt inside the same workflow, with the system prompt
# of their system_prompts.json. FLUX Kommunity License.
FLUX3_DIR = "FLUX3-K-Comfy"
FLUX3 = {"flux3k-fast": ("flux3-image-klein-bf16.safetensors", 16, 1.0),
         "flux3k-full": ("flux3-image-klein-base-bf16.safetensors", 50, 3.5)}
# BFL's names until 2026-10-17, the same files (same SHA-256): an install made before then has these until the setup renames them
FLUX3_OLD = {"flux3-image-klein-bf16.safetensors": "flux3_image_klein_sd_bf16.safetensors",
             "flux3-image-klein-base-bf16.safetensors": "flux3_image_klein_base_bf16.safetensors"}
FLUX3_TEXT = "qwen3vl_4b_bf16.safetensors"
FLUX3_UPSAMPLER = "qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors"
FLUX3_VAE = "flux2-vae.safetensors"
# MOSS-Image (OpenMOSS, Apache 2.0) on ComfyUI's own MOSS-Image support (v0.42.0): the SFT checkpoint, its Qwen3.5 9B
# text encoder and the FLUX.2 small decoder, Comfy-Org/MOSS-Image's files. The settings of ComfyUI's template
# "image_moss_image_t2i": 25 steps of er_sde at CFG 2.5 against its negative prompt, shift 5 (the model's own), no
# skip-layer guidance. Measured on a DGX Spark (2026-10-19, bf16): 45 s for 1344x768 with the model loaded, 26 GB taken
# from the machine starting from nothing loaded; OpenMOSS's own 50 Euler steps at CFG 3 with layer 22 skipped took 87 s
# for a picture of the same quality.
MOSS_DIR = "MOSS-Image-Comfy"
MOSS = {"moss-image": ("moss_image_sft_bf16.safetensors", 25, 2.5)}
MOSS_TEXT = "qwen3.5_9b_bf16.safetensors"
MOSS_VAE = "flux2-vae_small.safetensors"
MOSS_NEGATIVE = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"


def call(path, data=None):
    request = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as reply:
        body = reply.read()
    return json.loads(body) if body else {}


def up() -> bool:
    try:
        call("/system_stats")
        return True
    except OSError:
        return False


def ensure_server(kind: str) -> None:
    """Start ComfyUI if it is not running; unload the other family's models when the job kind changes."""

    if not up():
        print("[tensorfold] starting ComfyUI (first render after a start loads the models)", flush=True)
        subprocess.run(["bash", str(HERE / "comfy.sh"), "start"], check=True)
        for _ in range(180):
            if up():
                break
            time.sleep(2)
        else:
            sys.exit(f"ComfyUI did not answer at {URL}: see {PREFIX}/comfy.log")
    kind += TEXT_TAG
    last = STATE.read_text().strip() if STATE.exists() else ""
    if last and last != kind:
        # the picture and video models together would not leave room for the render itself in 128 GB
        call("/free", {"unload_models": True, "free_memory": True})
    STATE.write_text(kind)


def fresh_server(why: str) -> None:
    """Stop the ComfyUI server and wait until its memory is back: the next render starts with nothing loaded."""

    print(f"[tensorfold] restarting ComfyUI: {why}", flush=True)
    subprocess.run(["bash", str(HERE / "comfy.sh"), "stop"], check=False)
    for _ in range(60):
        if not up():
            break
        time.sleep(1)
    before = -1
    for _ in range(90):  # the memory comes back a little after the process is gone
        free = memory_guard.meminfo().get("MemAvailable", 0)
        if free - before < 200:
            break
        before = free
        time.sleep(1)


def fits_or_exit(args, lora: bool = False, references: bool = False, base: bool = False, pictures: int = 0,
                 reference_frames: int = 0) -> None:
    """The memory check before anything is loaded (memory_guard.py): a clip whose estimated peak would leave this
    machine under its floor is refused here, with the nearest size and length that fit, whoever started the render."""

    found = memory_guard.check(args.width, args.height, args.frames, lora, references, base, args.engine == "tensorfold",
                               pictures, reference_frames)
    if found.get("route") is None:
        return
    print(f"[tensorfold] memory: about {found['need_mb'] / 1000:.0f} GB at the peak (give or take {found['error_mb'] / 1000:g}) of "
          f"{found['available_mb'] / 1000:.0f} GB, floor {found['floor_mb'] / 1000:.0f} GB", flush=True)
    if not found["fits"]:
        sys.exit("refused before anything was loaded: " + found["reason"])


def upload(image: str) -> str:
    """Put an image (or a video or a sound file) in ComfyUI's input folder; returns the name its loader wants."""

    path = Path(image)
    name = f"studio_{uuid.uuid4().hex[:10]}{path.suffix.lower() or '.png'}"
    UPLOADED.append(name)
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
    request = urllib.request.Request(URL + "/upload/image", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=120) as reply:
        return json.loads(reply.read())["name"]


def gate() -> None:
    """On a machine with a workload governor (``SPARK_GOVERNOR``): the start gate before every submission
    (``start_gate.py``: the server's own reservation, memory, pressure, a cool start). Without one, nothing."""

    if os.environ.get("SPARK_GOVERNOR"):
        done = subprocess.run(["python3", str(HERE / "start_gate.py"), "comfy"], timeout=240)
        if done.returncode:
            sys.exit("the job was not submitted: the machine's start checks refused it (above)")


async def run(workflow: dict, sampler: str, named: dict | None = None, stages: list | None = None) -> str:
    """Queue a workflow and print the sampler's progress as `[tensorfold] step i/n` until it ends; returns its id.
    ``named`` maps nodes to the word their progress is printed with instead (`[tensorfold] dit i/n`); ``stages``
    receives (node class, seconds) for each node that ran.

    The progress socket is opened before the job is queued, so a job that ends at once (ComfyUI had it cached) is
    not missed.
    """

    gate()
    import aiohttp

    client = uuid.uuid4().hex
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(URL.replace("http", "ws", 1) + f"/ws?clientId={client}", max_msg_size=0) as ws:
            prompt_id = call("/prompt", {"prompt": workflow, "client_id": client})["prompt_id"]
            print(f"[tensorfold] comfy prompt {prompt_id}", flush=True)  # what the app's Cancel stops (server.stop_comfy)
            stages, running, since, said = [] if stages is None else stages, None, time.time(), {}
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                event = json.loads(message.data)
                data = event.get("data", {})
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if event["type"] == "progress" and named and str(data.get("node")) in named:
                    # at most fifty lines a stage: the autoregressive stage reports every frame (25 a second of song)
                    node, share = str(data.get("node")), data["value"] / max(data["max"], 1)
                    if share >= said.get(node, -1.0) + 0.02 or data["value"] >= data["max"]:
                        said[node] = share
                        print(f"[tensorfold] {named[node]} {data['value']}/{data['max']}", flush=True)
                elif event["type"] == "progress" and str(data.get("node")) == sampler:
                    print(f"[tensorfold] step {data['value']}/{data['max']}", flush=True)
                elif event["type"] == "executing":
                    # SPARK_TIMING=1: how long each node of the workflow ran
                    if running is not None:
                        stages.append((workflow[running]["class_type"], time.time() - since))
                    running, since = (str(data["node"]) if data.get("node") is not None else None), time.time()
                    if running is None:
                        break
                elif event["type"] in ("execution_error", "execution_interrupted", "execution_success"):
                    break
    if os.environ.get("SPARK_TIMING") == "1":
        print("[tensorfold] stages: " + ", ".join(f"{name} {took:.1f} s" for name, took in stages if took >= 0.05), flush=True)
    return prompt_id


def render(workflow: dict, sampler: str, named: dict | None = None, stages: list | None = None) -> tuple[dict, float]:
    """Run a workflow; returns (the saved file's record, seconds)."""

    started = time.time()
    try:
        prompt_id = asyncio.run(run(workflow, sampler, named, stages))
    except KeyboardInterrupt:
        call("/interrupt", {})
        raise
    while True:
        history = call(f"/history/{prompt_id}")
        if prompt_id in history and history[prompt_id].get("status", {}).get("completed") is not None:
            break
        time.sleep(1)
    record = history[prompt_id]
    if record["status"].get("status_str") != "success":
        errors = [m[1] for m in record["status"].get("messages", []) if m[0] == "execution_error"]
        detail = errors[0].get("exception_message", "").strip() if errors else json.dumps(record["status"])[:600]
        sys.exit(f"ComfyUI failed: {detail} (log: {PREFIX}/comfy.log)")
    # the files the save nodes wrote: a loader of a reference video or sound lists its input file among the outputs too
    found = [item for output in record["outputs"].values() for items in output.values()
             for item in (items if isinstance(items, list) else []) if isinstance(item, dict) and item.get("filename")
             and item.get("type", "output") == "output"]
    # the clip or the picture first, then the lossless sound track (save_sound) when the workflow has one
    found.sort(key=lambda item: item["filename"].endswith(".flac"))
    if found and found[0]["filename"].endswith(".flac") and any(node.get("class_type") in SOUND_ONLY for node in workflow.values()):
        return Saved(found[0]), time.time() - started  # a song or a sound effect: the sound file is the output
    if not found or found[0]["filename"].endswith(".flac"):
        sys.exit("ComfyUI finished without writing a file")
    return Saved(found[0], next((item for item in found if item["filename"].endswith(".flac")), None)), time.time() - started


class Saved(dict):
    """The record of the clip or picture a workflow saved; ``sound`` is the record of its lossless sound track."""

    def __init__(self, item: dict, sound: dict | None = None):
        super().__init__(item)
        self.sound = sound


# ComfyUI levels the audio decoder's output to an RMS of -14 dBFS at most and leaves the peaks alone: speech comes out
# touching full scale (measured: -0.6 to +0.2 dBFS between the samples), and the AAC track ComfyUI writes at about
# 130 kb/s then overshoots it (31 samples over full scale in one test clip), which a player clips. So the sound also
# leaves the workflow as a lossless track, HEADROOM_DB down so that nothing is cut off on the way, and the clip's
# sound is made again from that track: brought back up, with a limiter when a peak would pass PEAK_DBFS, at the bit
# rate the Mac build uses. AUDIO_EQ=off keeps ComfyUI's own track.
HEADROOM_DB, PEAK_DBFS, SOUND_BITRATE = 12, -1.0, "256k"


def save_sound(workflow: dict, audio: list, prefix: str) -> None:
    """Add the lossless copy of the decoded sound to a workflow (nodes 17 and 18)."""

    if os.environ.get("AUDIO_EQ", "on") == "off":
        return
    workflow["17"] = {"class_type": "AudioAdjustVolume", "inputs": {"audio": audio, "volume": -HEADROOM_DB}}
    workflow["18"] = {"class_type": "SaveAudio", "inputs": {"audio": ["17", 0], "filename_prefix": prefix}}


def finish_sound(item, target: Path, frames: int) -> None:
    """Make the clip's sound again from the lossless track: full level, no peak over PEAK_DBFS, cut to the picture's
    length. The picture is not encoded again. Says what it found."""

    sound = getattr(item, "sound", None)
    if sound is None:
        return
    if shutil.which("ffmpeg") is None:
        # not silently: the track ComfyUI wrote can overshoot full scale (see above)
        print("[tensorfold] sound: left as ComfyUI wrote it, at about 130 kb/s and without the limiter: ffmpeg is not "
              "installed on this machine (sudo apt install ffmpeg)", flush=True)
        (PREFIX / "ComfyUI" / "output" / sound.get("subfolder", "") / sound["filename"]).unlink(missing_ok=True)
        return
    import numpy as np

    track = target.with_suffix(".sound.flac")
    fetch(sound, track)
    try:
        # the peak between the samples too: measured at four times the rate
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(track), "-ar", str(4 * AUDIO_RATE), "-f", "f32le", "pipe:1"],
                             capture_output=True).stdout
        wave = np.frombuffer(raw, np.float32)
        if not len(wave) or not float(np.abs(wave).max()):
            print("[tensorfold] sound: the decoded track is silent", flush=True)
            return
        peak = 20 * math.log10(float(np.abs(wave).max())) + HEADROOM_DB
        chain = f"volume={HEADROOM_DB}dB"
        if peak > PEAK_DBFS:
            chain += f",alimiter=limit={10 ** (PEAK_DBFS / 20):.4f}:level=disabled:attack=2:release=40,aresample={AUDIO_RATE}"
        out = target.with_suffix(".sound.mp4")
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(target), "-i", str(track), "-map", "0:v:0", "-map", "1:a:0",
                               "-c:v", "copy", "-af", chain, "-c:a", "aac", "-b:a", SOUND_BITRATE, "-ar", str(AUDIO_RATE),
                               "-t", f"{frames / FPS:.6f}", "-movflags", "+faststart", str(out)], capture_output=True, text=True)
        if done.returncode or not out.is_file() or not out.stat().st_size:
            out.unlink(missing_ok=True)
            print(f"[tensorfold] sound: left as ComfyUI wrote it ({done.stderr.strip()[-200:]})", flush=True)
            return
        out.replace(target)
        print(f"[tensorfold] sound: peak {peak:+.1f} dBFS as decoded, " + (f"limited to {PEAK_DBFS:g} dBFS" if peak > PEAK_DBFS
              else "left at its level"), flush=True)
    finally:
        track.unlink(missing_ok=True)
        (PREFIX / "ComfyUI" / "output" / sound.get("subfolder", "") / sound["filename"]).unlink(missing_ok=True)


def crop_frames(workflow: dict, crop: str, width: int, height: int) -> bool:
    """Centre-crop the decoded frames to ``crop`` (WxH) inside the workflow, before the clip is encoded: one encode
    instead of two, and no ffmpeg (the 720p preset generates 1280x736 and crops; a Spark without ffmpeg failed there
    after the whole render). True when the workflow does it; False when this ComfyUI has no crop node, and then
    ``crop_file`` does it afterwards. Exits before anything is rendered when neither can."""

    try:
        wide, tall = (int(side) for side in crop.lower().split("x"))
    except ValueError:
        sys.exit(f"--crop is WIDTHxHEIGHT, got {crop}")
    if wide > width or tall > height or (width - wide) % 2 or (height - tall) % 2:
        sys.exit(f"cannot centre-crop {width}x{height} frames to {wide}x{tall}")
    if "ImageCrop" in call("/object_info/ImageCrop"):
        workflow["19"] = {"class_type": "ImageCrop", "inputs": {"image": ["13", 0], "width": wide, "height": tall,
                                                                "x": (width - wide) // 2, "y": (height - tall) // 2}}
        workflow["15"]["inputs"]["images"] = ["19", 0]
        return True
    if shutil.which("ffmpeg") is None:
        sys.exit(f"the clip is generated at {width}x{height} and cropped to {wide}x{tall}, and this machine can do neither crop: "
                 "its ComfyUI has no ImageCrop node and ffmpeg is not installed (sudo apt install ffmpeg)")
    return False


def crop_file(target: Path, crop: str) -> None:
    """The crop after the render, with ffmpeg: only for a ComfyUI without a crop node (see ``crop_frames``)."""

    wide, tall = crop.lower().split("x")
    cropped = target.with_suffix(".crop.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(target), "-vf", f"crop={wide}:{tall}", "-c:v",
                    "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy",
                    str(cropped)], check=True)
    cropped.replace(target)


def fetch(item: dict, target: Path) -> None:
    query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL + "/view?" + query, timeout=600) as reply:
        target.write_bytes(reply.read())


def installed(kind: str, name: str) -> bool:
    """Whether ComfyUI lists a model file (it reads its folders again on every call)."""

    loader, field = {"unet": ("UNETLoader", "unet_name"), "lora": ("LoraLoaderModelOnly", "lora_name")}[kind]
    return name in call(f"/object_info/{loader}")[loader]["input"]["required"][field][0]


# MiniMax's limits for one Ref2VA request, the same as on the Mac (h3_generate.py)
PICTURE_LIMIT, VIDEO_LIMIT, AUDIO_LIMIT, TOTAL_LIMIT = 9, 3, 3, 12
CLIP_SECONDS, TOTAL_SECONDS, CLIP_TOLERANCE = (2.0, 15.0), 15.0, 0.05
ASPECT_LIMIT = 4.0  # a reference video is between 1:4 and 4:1
MODEL_SHORT_EDGE, MODEL_MAX_PIXELS, REFERENCE_MULTIPLE = 768, 768 * 1344, 32
FPS, AUDIO_RATE = 24, 32000
UPLOADED: list[str] = []  # what this run put in ComfyUI's input folder


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        sys.exit(f"{name} is not on the PATH: reference videos and audio are read with it (sudo apt install ffmpeg)")
    return path


def probe_media(path) -> dict:
    """What a media file holds: ``video`` (its displayed width and height) or None, ``audio`` (its channel count) or
    None, and ``seconds``."""

    done = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                          capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"cannot read {path}: {done.stderr.strip() or 'not a media file'}")
    data = json.loads(done.stdout)
    found = {"video": None, "audio": None, "seconds": 0.0}
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and found["video"] is None and not stream.get("disposition", {}).get("attached_pic"):
            width, height = int(stream["width"]), int(stream["height"])
            num, _, den = str(stream.get("sample_aspect_ratio", "1:1")).partition(":")
            if num.isdigit() and den.isdigit() and int(num) > 0 and int(den) > 0:
                width = round(width * int(num) / int(den))
            turn = [side.get("rotation") for side in stream.get("side_data_list", []) if "rotation" in side]
            turn = float(turn[0]) if turn else float(stream.get("tags", {}).get("rotate", 0) or 0)
            if round(turn / 90.0) % 2:  # ffmpeg shows a rotated clip upright, so its sides swap
                width, height = height, width
            found["video"] = (width, height)
            found["seconds"] = max(found["seconds"], float(stream.get("duration") or 0.0))
        elif kind == "audio" and found["audio"] is None:
            found["audio"] = int(stream.get("channels") or 0)
            found["audio_seconds"] = float(stream.get("duration") or 0.0)
    found["seconds"] = found["seconds"] or float(data.get("format", {}).get("duration") or 0.0)
    found["audio_seconds"] = found.get("audio_seconds") or found["seconds"]
    return found


def check_clip_lengths(kind: str, lengths) -> None:
    """The model card's limits for reference videos or audio clips: each 2 to 15 s, 15 s in all."""

    low, high = CLIP_SECONDS
    for path, seconds in lengths:
        if not low - CLIP_TOLERANCE <= seconds <= high + CLIP_TOLERANCE:
            sys.exit(f"a reference {kind} is {low:g} to {high:g} seconds long, and {path} is {seconds:.1f} s")
    total = sum(seconds for _, seconds in lengths)
    if total > TOTAL_SECONDS + CLIP_TOLERANCE:
        sys.exit(f"the reference {kind}s are at most {TOTAL_SECONDS:g} seconds in all, got {total:.1f} s")


def reference_video_size(width: int, height: int, canvas_width: int, canvas_height: int, mode: str = "match") -> tuple[int, int]:
    """The size a reference video is encoded at, each side a multiple of 32, its own aspect ratio kept: the model's
    canvas for that ratio (768 on the short side, at most 768x1344 pixels), never enlarged, and with ``match`` also
    brought down to the clip's pixel area. Every latent of a reference rides through every pass, so a 5 s reference
    at the full canvas more than triples the rows of an 864x480 clip."""

    ratio = width / height
    if not 1.0 / ASPECT_LIMIT <= ratio <= ASPECT_LIMIT:
        sys.exit(f"a reference video is between 1:4 and 4:1, got {width}x{height}")
    wide, tall = (MODEL_SHORT_EDGE * ratio, float(MODEL_SHORT_EDGE)) if ratio >= 1.0 else (float(MODEL_SHORT_EDGE), MODEL_SHORT_EDGE / ratio)
    if wide * tall > MODEL_MAX_PIXELS:
        scale = math.sqrt(MODEL_MAX_PIXELS / (wide * tall))
        wide, tall = wide * scale, tall * scale
    if width * height < wide * tall:
        wide, tall = float(width), float(height)
    if mode == "match" and wide * tall > canvas_width * canvas_height:
        scale = math.sqrt(canvas_width * canvas_height / (wide * tall))
        wide, tall = wide * scale, tall * scale
    return (max(REFERENCE_MULTIPLE, round(wide / REFERENCE_MULTIPLE) * REFERENCE_MULTIPLE),
            max(REFERENCE_MULTIPLE, round(tall / REFERENCE_MULTIPLE) * REFERENCE_MULTIPLE))


def prepared_video(path, size: tuple[int, int], limit: int, folder: Path) -> tuple[Path, int]:
    """A reference video as ComfyUI's node is to read it: 24 fps, LANCZOS-scaled to ``size``, at most ``limit``
    frames, no sound, stored without loss (RGB H.264). Returns (file, frames). The node itself would take the clip
    at up to 768 pixels on the short side whatever the clip being made; scaling here is what keeps the rows, and
    the memory, in hand."""

    target = folder / f"video_{uuid.uuid4().hex[:8]}.mp4"
    done = subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-i", str(path), "-map", "0:v:0", "-an", "-vf",
                           f"fps={FPS},scale={size[0]}:{size[1]}:flags=lanczos,setsar=1", "-frames:v", str(limit),
                           "-c:v", "libx264rgb", "-qp", "0", "-preset", "veryfast", "-pix_fmt", "rgb24", str(target)],
                          capture_output=True, text=True)
    if done.returncode or not target.is_file():
        sys.exit(f"cannot read frames from {path}: {done.stderr.strip()}")
    count = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.strip()
    frames = int(count) if count.isdigit() else 0
    if frames < 22:
        sys.exit(f"cannot read frames from {path}: {frames} frames at {FPS} a second, and a reference video needs 22")
    return target, frames


def prepared_sound(path, seconds: float, folder: Path) -> Path:
    """The first ``seconds`` of a file's sound as a stereo wav at the audio decoder's rate (mono is repeated)."""

    target = folder / f"sound_{uuid.uuid4().hex[:8]}.wav"
    done = subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-i", str(path), "-map", "0:a:0", "-vn", "-ac", "2", "-ar",
                           str(AUDIO_RATE), "-t", f"{seconds:.6f}", "-c:a", "pcm_f32le", str(target)], capture_output=True, text=True)
    if done.returncode or not target.is_file() or target.stat().st_size < 1024:
        sys.exit(f"cannot read sound from {path}: {done.stderr.strip()}")
    return target


def forget_uploads() -> None:
    """Take this run's reference videos and sounds out of ComfyUI's input folder again (pictures are small and stay,
    as before)."""

    for name in UPLOADED:
        if name.endswith((".mp4", ".wav")):
            (PREFIX / "ComfyUI" / "input" / name).unlink(missing_ok=True)


def hub_loras(specs) -> list[tuple[str, float]]:
    """The --lora files as (name ComfyUI lists, strength). A LoRA is merged at run time into bf16 weights, never the
    8-bit file's (they are rotated): on FastH3 TensorFold's node merges the block projections and the engine
    quantizes them, ComfyUI the rest; on Ref2VA and MiniMax H3's own transformer ComfyUI merges all of it into its
    own blocks. PATH or PATH:STRENGTH; a slider LoRA takes a minus strength."""

    loras = []
    for spec in specs:
        path, _, strength = spec.rpartition(":")
        try:
            strength = float(strength)
        except ValueError:
            path, strength = spec, 1.0
        if not path:
            path, strength = spec, 1.0
        source = Path(path).resolve()
        if not source.is_file():
            sys.exit(f"no LoRA at {path}")
        link = MODELS / "loras" / f"hub_{source.name}"
        if not link.exists() or link.resolve() != source:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.unlink(missing_ok=True)
            link.symlink_to(source)
        loras.append((link.name, strength))
    return loras


def reference(args) -> None:
    """A clip from references (MiniMax H3 Ref2VA): `<Picture 1>` in the prompt is the first --ref, `<Video 1>` the
    first --ref-video, `<Audio 1>` the first --ref-audio (after the videos' own sound with --ref-video-sound)."""

    pictures, videos, audios = args.ref, args.ref_video, args.ref_audio
    for kind, limit, files in (("pictures", PICTURE_LIMIT, pictures), ("videos", VIDEO_LIMIT, videos), ("audio clips", AUDIO_LIMIT, audios)):
        if len(files) > limit:
            sys.exit(f"at most {limit} reference {kind}, got {len(files)}")
    if len(pictures) + len(videos) + len(audios) > TOTAL_LIMIT:
        sys.exit(f"at most {TOTAL_LIMIT} references in all, got {len(pictures) + len(videos) + len(audios)}")
    if audios and not (pictures or videos):
        sys.exit("a reference audio clip needs a reference picture or video beside it: the model does not take sound alone")
    if args.first_frame or args.last_frame:
        sys.exit("references and --first-frame / --last-frame do not combine: the reference model has no keyframe input")
    for kind, files in (("picture", pictures), ("video", videos), ("audio clip", audios)):
        for path in files:
            if not Path(path).is_file() or not Path(path).stat().st_size:
                sys.exit(f"no reference {kind} at {path}")
    # each video and each audio clip is 2 to 15 seconds, and 15 seconds in all per kind: measured before any model loads
    probes = [(path, probe_media(path)) for path in videos]
    for path, probe in probes:
        if probe["video"] is None:
            sys.exit(f"{path} has no picture: a reference video is a video file")
    check_clip_lengths("video", [(path, probe["seconds"]) for path, probe in probes])
    sounds = [(path, probe_media(path)) for path in audios]
    for path, probe in sounds:
        if not probe["audio"]:
            sys.exit(f"{path} has no sound: a reference audio clip is an audio file (or a video with sound)")
    check_clip_lengths("audio clip", [(path, probe["audio_seconds"]) for path, probe in sounds])
    sizes = [reference_video_size(*probe["video"], args.width, args.height, args.ref_video_size) for _, probe in probes]
    loras = hub_loras(args.lora)
    last = STATE.read_text().strip() if STATE.exists() else ""
    if up() and (last.startswith("video") or loras or "lora" in last):
        # A LoRA render, and the render after one, start from a server with nothing loaded, as for FastH3: the bf16
        # transformer (40 GB) with a LoRA merged in and the one ComfyUI still holds do not fit side by side.
        # After a FastH3 clip the server still holds what ComfyUI's own unloading does not give back (TensorFold's
        # engine keeps its int8 weights): measured, a reference render that followed one ran with 14 GB free
        # instead of 38. A server with nothing loaded is the safe start; it costs the load again.
        fresh_server("a reference render starts from a server with nothing loaded")
    fits_or_exit(args, bool(loras) or args.ref_mode == "turbo", True, pictures=len(pictures),
                 reference_frames=sum(min(args.frames, int(round((probe["seconds"] or 0) * FPS))) for _, probe in probes))
    ensure_server("ref2va")
    mode = args.ref_mode
    # a LoRA (the Turbo adapter, or one from the hub) is merged at run time, which needs the bf16 file: merged into
    # the 8-bit file's rotated weights it corrupts the clip without an error
    name = REF2VA["bf16"] if mode == "turbo" or loras or not installed("unet", REF2VA["int8"]) else REF2VA["int8"]
    missing = [f for kind, f in (("unet", name),) + ((("lora", REF2VA_TURBO),) if mode == "turbo" else ()) if not installed(kind, f)]
    if missing:
        sys.exit(f"not installed: {', '.join(missing)} (bash oneshot-setup-spark.sh --ref2va)")
    # the bf16 and the 8-bit transformer never stay loaded together
    ensure_server("ref2va-" + name + ("+lora" if loras else ""))
    steps = args.steps if args.steps is not None else (4 if mode == "turbo" else 20)
    if mode == "turbo" and steps != 4:
        print(f"[tensorfold] the Ref2VA Turbo adapter was trained for 4 passes; running {steps} as asked", flush=True)
    prompt = prompt_of(args)
    if not args.ref_plain and not any(tag in prompt for tag in ("<Picture", "<Video")):
        named = [f"<Picture {i + 1}>" for i in range(len(pictures))] or [f"<Video {i + 1}>" for i in range(len(videos))]
        prompt = "Use " + " and ".join(named) + " as the subject. " + prompt
    model = ["1", 0]
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": name, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": H3_TEXT, "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VAE}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": H3_AUDIO_VAE}},
        "6": {"class_type": "MiniMaxH3ReferenceToVideo",
              "inputs": {"clip": ["3", 0], "vae": ["4", 0], "audio_vae": ["5", 0], "prompt": prompt, "width": args.width,
                         "height": args.height, "length": args.frames, "ref_image_size": args.ref_size}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": args.seed}},
        # the adapter was trained on plain Euler steps; the 20-step model on the multistep sampler ComfyUI ships it with
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler" if mode == "turbo" else "res_multistep"}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["5", 0]}},
        "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "audio": ["14", 0], "fps": 24}},
        "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": f"studio/ref_{uuid.uuid4().hex[:8]}",
                                                     "format": "auto", "codec": "auto"}},
    }
    for i, picture in enumerate(pictures):
        w[str(30 + i)] = {"class_type": "LoadImage", "inputs": {"image": upload(picture)}}
        w["6"]["inputs"][f"ref_images.ref_image_{i}"] = [str(30 + i), 0]
    seconds, said = args.frames / FPS, []
    with tempfile.TemporaryDirectory(prefix="studio-ref-") as scratch:
        # ComfyUI's node numbers them as the Mac build does: pictures, then videos (a video's own sound is the
        # <Audio j> right before it), then the audio clips
        for i, ((path, probe), size) in enumerate(zip(probes, sizes)):
            clip, frames = prepared_video(path, size, args.frames, Path(scratch))
            w[str(40 + i)] = {"class_type": "LoadVideo", "inputs": {"file": upload(str(clip))}}
            w[str(50 + i)] = {"class_type": "GetVideoComponents", "inputs": {"video": [str(40 + i), 0]}}
            w["6"]["inputs"][f"ref_videos.ref_video_{i}"] = [str(50 + i), 0]
            used = frames - (frames - 5) % 17  # the node cuts the count down to 17n + 5
            said.append(f"<Video {i + 1}> {size[0]}x{size[1]}, {used} frames ({used / FPS:.1f} s)")
            if args.ref_video_sound:
                if not probe["audio"]:
                    sys.exit(f"{path} has no sound: leave --ref-video-sound out, or give videos that have sound")
                sound = prepared_sound(path, min(seconds, frames / FPS), Path(scratch))
                w[str(60 + i)] = {"class_type": "LoadAudio", "inputs": {"audio": upload(str(sound))}}
                w["6"]["inputs"][f"ref_video_audios.ref_video_audio_{i}"] = [str(60 + i), 0]
        for i, (path, probe) in enumerate(sounds):
            sound = prepared_sound(path, seconds, Path(scratch))
            w[str(70 + i)] = {"class_type": "LoadAudio", "inputs": {"audio": upload(str(sound))}}
            w["6"]["inputs"][f"ref_audios.ref_audio_{i}"] = [str(70 + i), 0]
    if mode == "turbo":
        w["2"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": REF2VA_TURBO, "strength_model": 1.0}}
        model = ["2", 0]
    for index, (lora, strength) in enumerate(loras):
        if not installed("lora", lora):
            sys.exit(f"ComfyUI does not list the LoRA {lora}")
        w[str(80 + index)] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": lora, "strength_model": strength}}
        model = [str(80 + index), 0]
        print(f"[tensorfold] LoRA {lora} at {strength:g} on the bf16 Ref2VA weights", flush=True)
    w["10"] = {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple", "steps": steps, "denoise": 1.0}}
    w["11"] = {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["6", 0]}}
    guidance = float(os.environ.get("SPARK_REF_CFG") or 1.0)
    if guidance > 1.0:
        # an experiment, not a setting: the checkpoint is guidance-distilled and MiniMax runs it without a negative
        # branch. This runs every pass twice (the prompt, and the same references with an empty prompt).
        w["7"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}}
        w["11"] = {"class_type": "CFGGuider", "inputs": {"model": model, "positive": ["6", 0], "negative": ["7", 0], "cfg": guidance}}
        print(f"[tensorfold] guidance {guidance:g} (experimental: two forwards a pass)", flush=True)
    w["12"] = {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0],
                          "latent_image": ["6", 1]}}
    save_sound(w, ["14", 0], w["16"]["inputs"]["filename_prefix"] + "_sound")
    cropped = crop_frames(w, args.crop, args.width, args.height) if args.crop else True
    kind = "Ref2VA with the Turbo adapter" if mode == "turbo" else "Ref2VA"
    count = (f"{len(pictures)} reference picture(s)" + (f", {len(videos)} video(s)" if videos else "")
             + (f", {len(audios)} audio clip(s)" if audios else "") + (", the videos' own sound" if videos and args.ref_video_sound else ""))
    print(f"[tensorfold] engine: cuda (ComfyUI, MiniMax H3 {kind}, {name.split('pruned_')[1].split('.')[0]}), "
          f"{count}, {steps} passes, generating {args.width}x{args.height}, {args.frames} frames", flush=True)
    for line in said:
        print(f"[tensorfold] reference {line}", flush=True)
    try:
        item, seconds_taken = render(w, "12")
    finally:
        forget_uploads()
    target = Path(args.output)
    fetch(item, target)
    finish_sound(item, target, args.frames)
    if not cropped:
        crop_file(target, args.crop)
    print("[tensorfold] " + json.dumps({"output": str(target), "engine": "cuda", "model": "ref2va", "mode": mode,
                                        "forwards": steps, "total_s": round(seconds_taken, 1), "frames": args.frames,
                                        "size": [args.width, args.height], "references": len(pictures),
                                        "reference_videos": len(videos), "reference_audios": len(audios),
                                        "loras": [[lora, strength] for lora, strength in loras]}), flush=True)


def prompt_of(args) -> str:
    text = Path(args.prompt_file).read_text() if args.prompt_file else args.prompt
    if not text or not text.strip():
        sys.exit("give --prompt or --prompt-file")
    return text.strip()


def video(args) -> None:
    for name, value, step in (("width", args.width, 32), ("height", args.height, 32)):
        if value % step:
            sys.exit(f"--{name} must be a multiple of {step}, got {value}")
    if (args.frames - 5) % 17:
        sys.exit(f"--frames must be 17n + 5 (22, 39, 56, ... 362), got {args.frames}")
    if args.ref or args.ref_video or args.ref_audio:
        return reference(args)
    base = args.base == "h3"
    if args.steps is None:
        args.steps = 25 if base else 8
    loras = hub_loras(args.lora)
    if loras:
        # TensorFold's engine merges a LoRA into its int8 weights itself (the TensorFoldH3Blocks node); ComfyUI merges
        # the LoRA's keys outside the blocks into the bf16 file, never into the 8-bit one (its weights are rotated)
        if args.engine == "tensorfold" and not memory_guard.engine_takes_lora(ENGINE_LIB):
            print("[tensorfold] this engine build cannot take a LoRA (spark/build-engine.sh updates it): "
                  "ComfyUI's own blocks with the bf16 weights", flush=True)
            args.engine = "comfy"
        if args.weights != "bf16":
            print("[tensorfold] a LoRA renders with the bf16 weights", flush=True)
        args.weights = "bf16"
    if base:
        # the undistilled model: ComfyUI's own blocks, dense attention (FastH3's sparse routing is FastH3's), the
        # model's own schedule (video shift 12, audio 3)
        args.engine, args.weights, args.sparsity = "comfy", "bf16", 0.0
    sparsity = args.sparsity
    if sparsity is None and args.engine == "tensorfold":
        sparsity = 0.8    # what FastH3 was trained with; the engine's sparse kernel is the faster one at every size
    if sparsity is None:
        # sparse attention pays off once the video rows dominate. Measured on a GB10, a pass at 864x480 takes
        # 10.5 s dense and 11.1 s sparse; at 1344x768, 33.4 s dense and 29.4 s sparse
        sparsity = 0.8 if args.width * args.height >= 700_000 else 0.0
    if args.engine == "tensorfold" and not ENGINE_LIB.exists():
        sys.exit(f"no TensorFold engine at {ENGINE_LIB}: run spark/build-engine.sh, or use --engine comfy")
    last = STATE.read_text().strip() if STATE.exists() else ""
    on_comfy = bool(loras) and args.engine != "tensorfold"
    if up() and (on_comfy or base or "lora" in last or "h3base" in last):
        # On a Spark the GPU and the system share one pool. With the bf16 weights (42 GB) a text encoder (26 GB) that
        # comes back for a new prompt makes ComfyUI move the model aside and load it again, and the two copies do not
        # fit: measured, free memory fell from 50 GB to 8. A server with nothing loaded keeps about 30 GB free.
        fresh_server("a LoRA render starts from a server with nothing loaded")
    fits_or_exit(args, bool(loras), False, base)
    # a LoRA on the engine needs no server of its own: the node merges it and puts the weights back after
    ensure_server("video" + ("+h3base" if base else "") + ("+lora" if on_comfy else ""))
    if base and not installed("unet", H3_FL2VA):
        sys.exit(f"MiniMax H3's own transformer ({H3_FL2VA}) is not installed: oneshot-setup-spark.sh --h3-base adds it "
                 "(40 GB), or render on FastH3 without --base h3")
    model = ["2", 0]
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": H3_FL2VA if base else FASTH3[args.weights], "weight_dtype": "default"}},
        # FastH3's schedule: video shift 10, audio shift 3 (fastvideo_inference.json); MiniMax H3's own: 12 and 3
        "2": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["1", 0], "shift_video": 12.0 if base else 10.0, "shift_audio": 3.0}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": H3_TEXT, "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VAE}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": H3_AUDIO_VAE}},
        "6": {"class_type": "MiniMaxH3ImageToVideo",
              "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": prompt_of(args), "width": args.width,
                         "height": args.height, "length": args.frames}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": args.seed}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep" if base else "euler"}},
        "12": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0],
                          "latent_image": ["6", 1]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["5", 0]}},
        "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "audio": ["14", 0], "fps": 24}},
        "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": f"studio/clip_{uuid.uuid4().hex[:8]}",
                                                     "format": "auto", "codec": "auto"}},
    }
    if args.first_frame:
        w["20"] = {"class_type": "LoadImage", "inputs": {"image": upload(args.first_frame)}}
        w["6"]["inputs"]["first_frame"] = ["20", 0]
    if args.last_frame:
        # the end keyframe of MiniMax H3's first-and-last-frame mode: the node encodes it as a condition latent at
        # the clip's last latent frame, held at every pass like the first frame (with the first frame it is fitted
        # to the canvas by a centre crop; alone it sets the geometry)
        w["23"] = {"class_type": "LoadImage", "inputs": {"image": upload(args.last_frame)}}
        w["6"]["inputs"]["last_frame"] = ["23", 0]
    for index, (name, strength) in enumerate(loras):
        if not installed("lora", name):
            sys.exit(f"ComfyUI does not list the LoRA {name}")
        node = str(30 + index)
        w[node] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": w["2"]["inputs"]["model"], "lora_name": name, "strength_model": strength}}
        w["2"]["inputs"]["model"] = [node, 0]
    if args.engine == "tensorfold":
        # the 50 blocks of every pass on TensorFold's CUDA family, int8 on the tensor cores, with FastH3's own sparse
        # attention; ComfyUI keeps the text encoder, the sampler and the decoders
        w["22"] = {"class_type": "TensorFoldH3Blocks",
                   "inputs": {"model": model, "unet_name": FASTH3["bf16"], "sparsity": sparsity}}
        model = ["22", 0]
    elif sparsity > 0:
        # ComfyUI's compiled block-sparse kernel with FastVideo's selection: 3D video cubes, the top share kept per
        # query cube, FastH3's own gate weights for the coarse branch; text and audio rows stay exact
        w["21"] = {"class_type": "BlockSparseAttention",
                   "inputs": {"model": model, "selection": "vsa", "selection.keep_percent": round(100 * (1 - sparsity), 1),
                              "start_percent": 0.0, "end_percent": 1.0, "dense_blocks": "", "min_tokens": 0,
                              "extra_tokens": 0, "sink_conditioning": "exact_kv_and_rows", "verbose": False}}
        model = ["21", 0]
    w["10"] = {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple", "steps": args.steps,
                                                          "denoise": 1.0}}
    w["11"] = {"class_type": "CFGGuider", "inputs": {"model": model, "positive": ["6", 0], "negative": ["7", 0],
                                                     "cfg": 1.0}}
    save_sound(w, ["14", 0], w["16"]["inputs"]["filename_prefix"] + "_sound")
    cropped = crop_frames(w, args.crop, args.width, args.height) if args.crop else True
    attention = f"sparse attention {sparsity}" if sparsity > 0 else "dense attention"
    runs = ("TensorFold int8 blocks in ComfyUI" + (", the LoRA merged into them" if loras else "")) if args.engine == "tensorfold" else "ComfyUI, MiniMax H3 bf16" if base else f"ComfyUI, FastH3 {args.weights}"
    print(f"[tensorfold] engine: cuda ({runs}, {attention}), generating "
          f"{args.width}x{args.height}, {args.frames} frames", flush=True)
    item, seconds = render(w, "12")
    target = Path(args.output)
    fetch(item, target)
    finish_sound(item, target, args.frames)
    if not cropped:
        crop_file(target, args.crop)
    print("[tensorfold] " + json.dumps({"output": str(target), "engine": "cuda", "forwards": args.steps,
                                        "total_s": round(seconds, 1), "frames": args.frames,
                                        "size": [args.width, args.height], "sparsity": sparsity,
                                        **({"cropped_to": args.crop} if args.crop else {})}), flush=True)


def flux3_system_prompt(task: str = "t2i") -> str:
    """A system prompt of BFL's prompt upsampler (system_prompts.json, downloaded with the model): t2i for a picture
    from words, i2i for an edit, where it sees the pictures too."""

    path = MODELS / FLUX3_DIR / "system_prompts.json"
    try:
        return json.loads(path.read_text())[task]
    except (OSError, ValueError, KeyError):
        sys.exit(f"FLUX3-K's prompt upsampler has no system prompt at {path}: run oneshot-setup-spark.sh --flux3")


def flux3_unet(model: str) -> str:
    """The file of a FLUX3-K model, under BFL's present name, or the old one where only that is on disk."""

    name = FLUX3[model][0]
    folder = MODELS / FLUX3_DIR / "diffusion_models"
    return FLUX3_OLD[name] if not (folder / name).is_file() and (folder / FLUX3_OLD[name]).is_file() else name


def flux3_workflow(args, text: str, seed: int, caption: str = "") -> dict:
    """FLUX3-K on ComfyUI's own FLUX 3 nodes, the layout of BFL's templates: the upsampler (TextGenerate, their
    sampling settings) writes the caption, which goes to the Qwen3-VL text encoder, then the sampler."""

    _, steps, cfg = FLUX3[args.model]
    unet = flux3_unet(args.model)
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX3_TEXT, "type": "flux3", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX3_VAE}},
        "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": args.width, "height": args.height, "batch_size": 1}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ""}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": args.steps or steps,
                                                    "cfg": cfg, "sampler_name": "euler", "scheduler": "simple",
                                                    "positive": ["5", 0], "negative": ["7", 0], "latent_image": ["6", 0],
                                                    "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": f"studio/image_{uuid.uuid4().hex[:8]}"}},
    }
    if caption:
        w["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": caption}}
    else:
        w["20"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX3_UPSAMPLER, "type": "flux3", "device": "default"}}
        w["21"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["20", 0], "prompt": text, "max_length": 3584, "sampling_mode": "on",
            "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 1, "sampling_mode.top_p": 0.95,
            "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.05, "sampling_mode.seed": 0,
            "sampling_mode.presence_penalty": 1.5, "thinking": False, "use_default_template": True, "mtp": "auto",
            "system_prompt": flux3_system_prompt()}}
        w["22"] = {"class_type": "PreviewAny", "inputs": {"source": ["21", 0]}}
        w["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ["21", 0]}}
    return w


def moss_workflow(args, text: str, seed: int) -> dict:
    """MOSS-Image, the layout of ComfyUI's template: the prompt goes to the model as written."""

    unet, steps, cfg = MOSS[args.model]
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": MOSS_TEXT, "type": "moss_image", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": MOSS_VAE}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": text}},
        "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": args.width, "height": args.height, "batch_size": 1}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": args.negative or MOSS_NEGATIVE}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": args.steps or steps,
                                                    "cfg": cfg, "sampler_name": "er_sde", "scheduler": "simple",
                                                    "positive": ["5", 0], "negative": ["7", 0], "latent_image": ["6", 0],
                                                    "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": f"studio/image_{uuid.uuid4().hex[:8]}"}},
    }


# MiniMax Music 3 (lyrics and a style caption to a song with sung vocals, 44.1 kHz stereo) on ComfyUI's own nodes, the
# layout of ComfyUI's blueprint "Text to Music (MiniMax Music 3)": the text encoder runs MiniMax's autoregressive
# model (the acoustic conditioning, and where the song ends), the DiT fills it in over 30 Euler steps at CFG 1.7 against
# the zeroed condition, the DAV decodes it in tiles. The weights are Comfy-Org/MiniMax-Music-3's single files
# (oneshot-setup-spark.sh --music). MiniMax-Music3 Community License: a commercial product shows "MiniMax-Music3".
MUSIC3_DIR = "MiniMax-Music3-Comfy"
MUSIC3_DIT = "minimax_music3_dit_fp16.safetensors"
MUSIC3_TEXT = "minimax_music3_text_encoder_pruned_bf16.safetensors"
MUSIC3_VAE = "minimax_music3_dav.safetensors"
MUSIC_RATE, MUSIC_SECONDS = 44100, (10.0, 300.0)  # the app's range; the model takes up to 360 s (9000 frames at 25 a second)
MUSIC_STEPS, MUSIC_CFG, MUSIC_AR_CFG, MUSIC_TOP_K = 30, 1.7, 1.5, 50  # the blueprint's sampler; the encoder's own defaults


def music_workflow(caption: str, lyrics: str, seconds: float, seed: int, steps: int = MUSIC_STEPS, cfg: float = MUSIC_CFG,
                   ar_cfg: float = MUSIC_AR_CFG, top_k: int = MUSIC_TOP_K, prefix: str = "") -> dict:
    """The workflow of one song. The encoder's second output is the length the autoregressive model chose (it may end
    the song before ``seconds``): the empty latent takes it, so the DiT never fills silence after the end."""

    prefix = prefix or f"studio/music_{uuid.uuid4().hex[:8]}"
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MUSIC3_DIT, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": MUSIC3_TEXT, "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": MUSIC3_VAE}},
        "5": {"class_type": "MiniMaxMusic3TextEncode", "inputs": {"clip": ["3", 0], "caption": caption, "lyrics": lyrics,
                                                                  "seed": seed, "max_duration": round(seconds, 2),
                                                                  "cfg_scale": ar_cfg, "top_k": top_k}},
        "6": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {"seconds": ["5", 1], "batch_size": 1}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": cfg,
                                                    "sampler_name": "euler", "scheduler": "simple", "positive": ["5", 0],
                                                    "negative": ["7", 0], "latent_image": ["6", 0], "denoise": 1.0}},
        # tiled, as the blueprint's switch has it on: a five-minute song decodes in pieces
        "13": {"class_type": "VAEDecodeAudioTiled", "inputs": {"samples": ["12", 0], "vae": ["4", 0], "tile_size": 1536, "overlap": 64}},
        # HEADROOM_DB down before the lossless file, so nothing is cut off on the way; music_finish brings it back up
        "17": {"class_type": "AudioAdjustVolume", "inputs": {"audio": ["13", 0], "volume": -HEADROOM_DB}},
        "18": {"class_type": "SaveAudio", "inputs": {"audio": ["17", 0], "filename_prefix": prefix}},
    }


def music_finish(track: Path, target: Path, what: str = "song", channels: int = 2) -> dict:
    """The song (or sound effect, or foley) as the app keeps it: a 44.1 kHz 16-bit WAV at full level, no peak over
    PEAK_DBFS (measured between the samples, at four times the rate; a limiter only where a peak would pass it)."""

    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        sys.exit(f"a {what} is levelled with ffmpeg, which is not installed on this machine (sudo apt install ffmpeg)")
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(track), "-ar", str(4 * MUSIC_RATE), "-f", "f32le", "pipe:1"],
                         capture_output=True, timeout=600).stdout
    wave = np.frombuffer(raw, np.float32)
    if not len(wave) or not float(np.abs(wave).max()):
        sys.exit(f"the {what} came out silent")
    peak = 20 * math.log10(float(np.abs(wave).max())) + HEADROOM_DB
    chain = f"volume={HEADROOM_DB}dB"
    if peak > PEAK_DBFS:
        chain += (f",aresample={4 * MUSIC_RATE},alimiter=limit={10 ** ((PEAK_DBFS - 0.3) / 20):.4f}:level=disabled:attack=2:release=40,"
                  f"aresample={MUSIC_RATE}")
    done = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(track), "-af", chain, "-ar", str(MUSIC_RATE), "-ac", str(channels),
                           "-c:a", "pcm_s16le", str(target)], capture_output=True, text=True, timeout=600)
    if done.returncode or not target.is_file():
        sys.exit(f"the {what} could not be written: " + done.stderr.strip()[-300:])
    return {"peak_dbfs_decoded": round(peak, 2), "limited": peak > PEAK_DBFS}


def music(args) -> None:
    caption = (Path(args.caption_file).read_text() if args.caption_file else args.caption).strip()
    lyrics = (Path(args.lyrics_file).read_text() if args.lyrics_file else args.lyrics).strip()
    if not caption:
        sys.exit("give --caption or --caption-file: the style of the song")
    low, high = MUSIC_SECONDS
    if not low <= args.seconds <= high:
        sys.exit(f"--seconds is {low:g} to {high:g}, got {args.seconds:g}")
    found = memory_guard.music_check(args.seconds)
    if found.get("route") is not None:
        print(f"[tensorfold] memory: about {found['need_mb'] / 1000:.0f} GB at the peak (give or take {found['error_mb'] / 1000:g}) of "
              f"{found['available_mb'] / 1000:.0f} GB, floor {found['floor_mb'] / 1000:.0f} GB", flush=True)
        if not found["fits"]:
            sys.exit("refused before anything was loaded: " + found["reason"])
    last = STATE.read_text().strip() if STATE.exists() else ""
    if up() and last != "music":
        # the song loads into a server with nothing else in it: what ComfyUI's own unloading leaves behind (TensorFold's
        # H3 engine keeps its int8 weights) and the 22 GB of MiniMax Music 3 are not measured side by side
        fresh_server("MiniMax Music 3 starts from a server with nothing else loaded")
    ensure_server("music")
    missing = [name for name, loader, field in ((MUSIC3_DIT, "UNETLoader", "unet_name"), (MUSIC3_TEXT, "CLIPLoader", "clip_name"),
                                                (MUSIC3_VAE, "VAELoader", "vae_name"))
               if name not in call(f"/object_info/{loader}")[loader]["input"]["required"][field][0]]
    if missing:
        sys.exit(f"not installed: {', '.join(missing)} (bash oneshot-setup-spark.sh --music; a ComfyUI that was running "
                 "before the install needs bash spark/comfy.sh stop)")
    w = music_workflow(caption, lyrics, args.seconds, args.seed, args.steps, args.cfg, args.ar_cfg, args.top_k)
    print(f"[tensorfold] engine: cuda (ComfyUI, MiniMax Music 3: DiT fp16, text encoder bf16), up to {args.seconds:g} s, "
          f"seed {args.seed}, {args.steps} steps", flush=True)
    stages: list = []
    item, seconds = render(w, "12", {"5": "ar", "12": "dit"}, stages)
    target = Path(args.output)
    with tempfile.TemporaryDirectory(prefix="studio-music-") as scratch:
        track = Path(scratch) / "song.flac"
        fetch(item, track)
        (PREFIX / "ComfyUI" / "output" / item.get("subfolder", "") / item["filename"]).unlink(missing_ok=True)
        level = music_finish(track, target)
    import wave as wavefile

    with wavefile.open(str(target)) as handle:
        audio_s = handle.getnframes() / handle.getframerate()
    took = dict(stages)
    ar, dit = took.get("MiniMaxMusic3TextEncode", 0.0), took.get("KSampler", 0.0)
    dav = took.get("VAEDecodeAudioTiled", 0.0)
    print("[tensorfold] " + json.dumps({"output": str(target), "engine": "cuda", "model": "music3", "audio_s": round(audio_s, 2),
                                        "asked_s": args.seconds, "seed": args.seed, "steps": args.steps,
                                        "ar_s": round(ar, 1), "dit_s": round(dit, 1), "dav_s": round(dav, 1),
                                        "load_s": round(sum(t for name, t in stages if name in ("UNETLoader", "CLIPLoader", "VAELoader")), 1),
                                        "total_s": round(seconds, 1), "eos": audio_s < args.seconds - 0.5, **level}), flush=True)


# Sound effects and foley (app/sound.py's jobs on a Spark; on a Mac sound_generate.py runs them in their own environment).
#   sfx    Stability AI's Stable Audio Open 1.0 on ComfyUI's own Stable Audio nodes, the layout of ComfyUI's audio
#          example: the checkpoint (DiT and VAE), its T5-base encoder, the seconds as conditioning, KSampler with
#          dpmpp_3m_sde_gpu on the exponential schedule. 44.1 kHz stereo, up to 47 s. Stability AI Community License.
#   foley  MMAudio large_44k_v2 (Ho Kei Cheng et al.) on kijai's ComfyUI-MMAudio loaders and the Studio's sampler node
#          (spark/studio_mmaudio: the clip's frames at the times MMAudio reads them). 44.1 kHz mono. Weights CC-BY-NC 4.0.
# The weights are under $SPARK_MODELS/Sound-Comfy (oneshot-setup-spark.sh --sound). Each starts from a ComfyUI server
# with nothing else loaded, as a song does: they are measured on their own (memory_guard.sound_check).
SOUND_DIR = "Sound-Comfy"
SAO_CHECKPOINT = "stable_audio_open_1.0.safetensors"
SAO_TEXT = "stable_audio_open_t5_base.safetensors"
MMAUDIO = {"model": "mmaudio_large_44k_v2_fp16.safetensors", "vae": "mmaudio_vae_44k_fp16.safetensors",
           "synchformer": "mmaudio_synchformer_fp16.safetensors", "clip": "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors"}
SFX_STEPS, SFX_CFG, SFX_SECONDS = 100, 7.0, (1.0, 47.0)        # the app's card (app/sound.py)
FOLEY_STEPS, FOLEY_CFG, FOLEY_SECONDS = 25, 4.5, (1.0, 15.0)   # MMAudio's own; the studio's longest clip
FOLEY_ROWS = 384  # the clip is brought to 384 rows before ComfyUI decodes it: MMAudio looks at 384x384 and 224x224
SOUND_ONLY = {"EmptyMiniMaxMusic3LatentAudio", "EmptyLatentAudio", "StudioMMAudioSampler"}


def sfx_workflow(prompt: str, negative: str, seconds: float, seed: int, steps: int = SFX_STEPS, cfg: float = SFX_CFG,
                 prefix: str = "") -> dict:
    prefix = prefix or f"studio/sfx_{uuid.uuid4().hex[:8]}"
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAO_CHECKPOINT}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": SAO_TEXT, "type": "stable_audio", "device": "default"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "5": {"class_type": "ConditioningStableAudio", "inputs": {"positive": ["3", 0], "negative": ["4", 0],
                                                                  "seconds_start": 0.0, "seconds_total": round(seconds, 2)}},
        "6": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": round(seconds, 2), "batch_size": 1}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": cfg,
                                                    "sampler_name": "dpmpp_3m_sde_gpu", "scheduler": "exponential",
                                                    "positive": ["5", 0], "negative": ["5", 1], "latent_image": ["6", 0],
                                                    "denoise": 1.0}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["1", 2]}},
        # HEADROOM_DB down before the lossless file, as a song; music_finish brings it back up
        "17": {"class_type": "AudioAdjustVolume", "inputs": {"audio": ["13", 0], "volume": -HEADROOM_DB}},
        "18": {"class_type": "SaveAudio", "inputs": {"audio": ["17", 0], "filename_prefix": prefix}},
    }


def foley_workflow(video: str, prompt: str, negative: str, seconds: float, seed: int, steps: int = FOLEY_STEPS,
                   cfg: float = FOLEY_CFG, prefix: str = "") -> dict:
    """``video`` is the name of the prepared clip in ComfyUI's input folder (prepared_clip, upload)."""

    prefix = prefix or f"studio/foley_{uuid.uuid4().hex[:8]}"
    return {
        "1": {"class_type": "MMAudioModelLoader", "inputs": {"mmaudio_model": MMAUDIO["model"], "base_precision": "bf16"}},
        "2": {"class_type": "MMAudioFeatureUtilsLoader", "inputs": {"vae_model": MMAUDIO["vae"], "synchformer_model": MMAUDIO["synchformer"],
                                                                    "clip_model": MMAUDIO["clip"], "mode": "44k", "precision": "bf16"}},
        "3": {"class_type": "LoadVideo", "inputs": {"file": video}},
        "4": {"class_type": "GetVideoComponents", "inputs": {"video": ["3", 0]}},
        "12": {"class_type": "StudioMMAudioSampler", "inputs": {"mmaudio_model": ["1", 0], "feature_utils": ["2", 0],
                                                                "images": ["4", 0], "fps": ["4", 2], "duration": round(seconds, 3),
                                                                "steps": steps, "cfg": cfg, "seed": seed, "prompt": prompt,
                                                                "negative_prompt": negative}},
        "17": {"class_type": "AudioAdjustVolume", "inputs": {"audio": ["12", 0], "volume": -HEADROOM_DB}},
        "18": {"class_type": "SaveAudio", "inputs": {"audio": ["17", 0], "filename_prefix": prefix}},
    }


def prepared_clip(path, seconds: float, folder: Path) -> Path:
    """The clip as ComfyUI is to decode it for foley: FOLEY_ROWS rows, its own frame rate, at most ``seconds``, no
    sound, stored without loss. ComfyUI holds every decoded frame in memory as floats: at the clip's own size a 15 s
    clip at 1376x768 would be 4.6 GB of them, at 384 rows 1.1 GB."""

    target = folder / f"foley_{uuid.uuid4().hex[:8]}.mp4"
    done = subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-i", str(path), "-map", "0:v:0", "-an", "-t", f"{seconds:.3f}",
                           "-vf", f"scale=-2:{FOLEY_ROWS}:flags=lanczos,setsar=1", "-c:v", "libx264rgb", "-qp", "0",
                           "-preset", "veryfast", "-pix_fmt", "rgb24", str(target)], capture_output=True, text=True)
    if done.returncode or not target.is_file():
        sys.exit(f"cannot read frames from {path}: {done.stderr.strip()[-300:]}")
    return target


def sound(args) -> None:
    """A sound effect (sfx) or a clip's foley through ComfyUI, then the WAV, the clip with it (foley) and the take file,
    as sound_generate.py writes them on a Mac."""

    sys.path.insert(0, str(HERE.parent))
    import sound_generate  # the Mac runner's ffmpeg laying and take file; nothing of its models is imported

    started, foley = time.time(), args.kind == "foley"
    args.steps = args.steps or (FOLEY_STEPS if foley else SFX_STEPS)
    args.cfg = args.cfg if args.cfg is not None else (FOLEY_CFG if foley else SFX_CFG)
    if foley:
        if not (args.video and args.clip_out):
            sys.exit("foley takes --video and --clip-out")
        clip = probe_media(args.video)
        if clip["video"] is None:
            sys.exit(f"{args.video} has no picture")
        seconds = min(clip["seconds"], args.max_seconds, FOLEY_SECONDS[1])
        if seconds < FOLEY_SECONDS[0]:
            sys.exit("the clip is shorter than a second")
    else:
        seconds = args.seconds
        if not args.prompt.strip():
            sys.exit("say what is heard: --prompt")
        if not SFX_SECONDS[0] <= seconds <= SFX_SECONDS[1]:
            sys.exit(f"--seconds is {SFX_SECONDS[0]:g} to {SFX_SECONDS[1]:g}, got {seconds:g}")
    found = memory_guard.sound_check(args.kind, seconds)
    if found.get("route") is not None:
        if found.get("need_mb"):
            print(f"[tensorfold] memory: about {found['need_mb'] / 1000:.0f} GB at the peak (give or take {found['error_mb'] / 1000:g}) of "
                  f"{found['available_mb'] / 1000:.0f} GB, floor {found['floor_mb'] / 1000:.0f} GB", flush=True)
        if not found["fits"]:
            sys.exit("refused before anything was loaded: " + found["reason"])
    last = STATE.read_text().strip() if STATE.exists() else ""
    if up() and last != args.kind:
        fresh_server(("Stable Audio Open" if args.kind == "sfx" else "MMAudio") + " starts from a server with nothing else loaded")
    ensure_server(args.kind)
    if foley:
        listed = call("/object_info/MMAudioModelLoader").get("MMAudioModelLoader", {}).get("input", {}).get("required", {})
        names = listed.get("mmaudio_model", [[]])[0]
        missing = [name for name in MMAUDIO.values() if name not in names]
        if not listed or "StudioMMAudioSampler" not in call("/object_info/StudioMMAudioSampler"):
            missing.insert(0, "ComfyUI-MMAudio and the Studio's sampler node")
    else:
        missing = [name for name, loader, field in ((SAO_CHECKPOINT, "CheckpointLoaderSimple", "ckpt_name"), (SAO_TEXT, "CLIPLoader", "clip_name"))
                   if name not in call(f"/object_info/{loader}")[loader]["input"]["required"][field][0]]
    if missing:
        sys.exit(f"not installed: {', '.join(missing)} (bash oneshot-setup-spark.sh --sound; a ComfyUI that was running "
                 "before the install needs bash spark/comfy.sh stop)")
    target = Path(args.output)
    with tempfile.TemporaryDirectory(prefix="studio-sound-") as scratch:
        if foley:
            name = upload(str(prepared_clip(args.video, seconds, Path(scratch))))
            w = foley_workflow(name, args.prompt, args.negative, seconds, args.seed, args.steps, args.cfg)
            print(f"[tensorfold] engine: cuda (ComfyUI, MMAudio large_44k_v2, bf16), {seconds:.2f} s of the clip, seed {args.seed}, "
                  f"{args.steps} steps", flush=True)
        else:
            w = sfx_workflow(args.prompt, args.negative, seconds, args.seed, args.steps, args.cfg)
            print(f"[tensorfold] engine: cuda (ComfyUI, Stable Audio Open 1.0), {seconds:g} s, seed {args.seed}, {args.steps} steps", flush=True)
        try:
            item, took = render(w, "12")
        finally:
            forget_uploads()
        track = Path(scratch) / "sound.flac"
        fetch(item, track)
        (PREFIX / "ComfyUI" / "output" / item.get("subfolder", "") / item["filename"]).unlink(missing_ok=True)
        level = music_finish(track, target, "foley track" if foley else "sound effect", 1 if foley else 2)
    if foley:
        sound_generate.lay(Path(args.video), target, Path(args.clip_out), args.replace, args.foley_db)
    made = {"model": "MMAudio large_44k_v2" if foley else "Stable Audio Open 1.0", "rate": MUSIC_RATE,
            "seconds": round(seconds, 3), "engine": "cuda", "render_s": round(took, 1), **level}
    sound_generate.write_take(args, made, started)
    print("[tensorfold] " + json.dumps({"output": str(target), **made}), flush=True)


def image(args) -> None:
    for name, value in (("width", args.width), ("height", args.height)):
        if value % 16:
            sys.exit(f"--{name} must be a multiple of 16, got {value}")
    flux3 = args.model in FLUX3
    ensure_server("flux3" if flux3 else "moss" if args.model in MOSS else "image")
    text = prompt_of(args)
    target = Path(args.output)
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else [args.seed]
    if args.model in MOSS:
        for seed in seeds:
            item, seconds = render(moss_workflow(args, text, seed), "12")
            out = target.with_name(f"{target.stem}_s{seed}{target.suffix}") if args.seeds else target
            fetch(item, out)
            print("[tensorfold] " + json.dumps({"output": str(out), "engine": "cuda", "model": args.model,
                                                "width": args.width, "height": args.height, "seed": seed,
                                                "steps": args.steps or MOSS[args.model][1], "total_s": round(seconds, 1)}), flush=True)
        return
    if flux3:
        caption = "" if args.upsample else text
        for seed in seeds:
            item, seconds = render(flux3_workflow(args, text, seed, caption), "12")
            out = target.with_name(f"{target.stem}_s{seed}{target.suffix}") if args.seeds else target
            fetch(item, out)
            print("[tensorfold] " + json.dumps({"output": str(out), "engine": "cuda", "model": args.model,
                                                "width": args.width, "height": args.height, "seed": seed,
                                                "steps": args.steps or FLUX3[args.model][1], "upsampled": args.upsample,
                                                "total_s": round(seconds, 1)}), flush=True)
        return
    for seed in seeds:
        w = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QWEN_BASE if args.base else QWEN,
                                                         "weight_dtype": "default"}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_TEXT, "type": "qwen_image", "device": "default"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},
            "5": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["3", 0], "prompt": text,
                                                                    "negative_prompt": args.negative, "resolution": 1024}},
            "6": {"class_type": "EmptyLatentImage", "inputs": {"width": args.width, "height": args.height, "batch_size": 1}},
            "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
            "16": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": f"studio/image_{uuid.uuid4().hex[:8]}"}},
        }
        if args.base:
            w["12"] = {"class_type": "KSampler",
                       "inputs": {"model": ["1", 0], "seed": seed, "steps": args.steps or 40, "cfg": args.cfg,
                                  "sampler_name": "euler", "scheduler": "simple", "positive": ["5", 0],
                                  "negative": ["5", 1], "latent_image": ["6", 0], "denoise": 1.0}}
        else:
            w["8"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
            w["9"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
            w["10"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": TURBO_SIGMAS}}
            w["11"] = {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["5", 0],
                                                             "negative": ["5", 1], "cfg": 1.0}}
            w["12"] = {"class_type": "SamplerCustomAdvanced",
                       "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0],
                                  "latent_image": ["6", 0]}}
        item, seconds = render(w, "12")
        out = target.with_name(f"{target.stem}_s{seed}{target.suffix}") if args.seeds else target
        fetch(item, out)
        print("[tensorfold] " + json.dumps({"output": str(out), "engine": "cuda", "width": args.width,
                                            "height": args.height, "seed": seed, "turbo": not args.base,
                                            "total_s": round(seconds, 1)}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    kinds = parser.add_subparsers(dest="kind", required=True)
    v = kinds.add_parser("video")
    v.add_argument("--prompt", default=""); v.add_argument("--prompt-file", default="")
    v.add_argument("-o", "--output", required=True)
    v.add_argument("--width", type=int, default=864); v.add_argument("--height", type=int, default=480)
    v.add_argument("--frames", type=int, default=124); v.add_argument("--seed", type=int, default=0)
    v.add_argument("--steps", type=int, default=None, help="passes: 8 is what FastH3 was trained for (the default); "
                   "with --ref, 4 with the Turbo adapter and 20 without")
    v.add_argument("--ref", action="append", default=[], help="a reference picture (repeat for up to 9): the clip "
                   "comes from MiniMax H3 Ref2VA and `<Picture 1>` in the prompt is the first one")
    v.add_argument("--ref-video", action="append", default=[], help="a reference video (repeat for up to 3, each 2 to "
                   "15 s, 15 s in all): `<Video 1>`, ... in the prompt; a subject and how it moves")
    v.add_argument("--ref-audio", action="append", default=[], help="a reference audio clip (repeat for up to 3, each 2 "
                   "to 15 s, 15 s in all): `<Audio 1>`, ...; only beside a picture or a video")
    v.add_argument("--ref-video-sound", action="store_true", help="each reference video's own sound is a reference "
                   "too, numbered `<Audio 1>`, ... ahead of the --ref-audio clips")
    v.add_argument("--ref-video-size", choices=("match",), default="match",
                   help="a reference video is brought down to the clip's pixel area. The model's own 768-pixel canvas "
                        "(REF_VIDEO_SIZE=full on the Mac) is several times the rows and the memory and is not offered "
                        "on a Spark")
    v.add_argument("--ref-mode", choices=("turbo", "full"), default="full",
                   help="full: 20 steps; turbo: the bf16 model with LightX2V's 4-pass adapter")
    v.add_argument("--ref-size", choices=("match", "max"), default="match",
                   help="match: references scaled down to the clip's pixel area (what the adapter was trained with); "
                        "max: up to 2048 pixels on the short edge, several times slower")
    v.add_argument("--first-frame", default="", help="an image the clip starts from")
    v.add_argument("--last-frame", default="", help="an image the clip ends on (with or without --first-frame)")
    v.add_argument("--text-encoder", default=os.environ.get("TEXT_ENCODER") or "regular",
                   help="regular, or the file name of another MiniMax H3 text encoder in ComfyUI's text_encoders folder")
    v.add_argument("--crop", default="", help="WxH centre crop of the finished clip")
    v.add_argument("--weights", choices=sorted(FASTH3), default=os.environ.get("SPARK_WEIGHTS", "bf16"))
    v.add_argument("--engine", choices=("tensorfold", "comfy"),
                   default=os.environ.get("SPARK_ENGINE") or ("tensorfold" if ENGINE_LIB.exists() else "comfy"),
                   help="what runs the transformer blocks: TensorFold's CUDA family (the default once "
                        "spark/build-engine.sh has built it) or ComfyUI's own")
    v.add_argument("--base", choices=("fasth3", "h3"), default=os.environ.get("SPARK_BASE") or "fasth3",
                   help="the transformer of a clip: FastH3 (the default), or MiniMax H3's own first-and-last-frame "
                        "model when it is installed (25 passes by default; what keyframe LoRAs were trained on)")
    v.add_argument("--lora", action="append", default=[], help="a style LoRA, PATH or PATH:STRENGTH (repeat to stack)")
    v.add_argument("--ref-plain", action="store_true",
                   help="with references: the prompt as written, without `Use <Video 1> as the subject.` put ahead "
                        "of a prompt that names no reference (a LoRA trained on plain captions, such as line art)")
    v.add_argument("--sparsity", type=float, default=None, help="sparse attention (0 is dense); default by size")
    v.set_defaults(run=video)
    i = kinds.add_parser("image")
    i.add_argument("--prompt", default=""); i.add_argument("--prompt-file", default="")
    i.add_argument("-o", "--output", required=True)
    i.add_argument("--width", type=int, default=1344); i.add_argument("--height", type=int, default=768)
    i.add_argument("--seed", type=int, default=0); i.add_argument("--seeds", default="", help="several pictures: 1,2,3")
    i.add_argument("--model", choices=("qwen", *FLUX3, *MOSS), default=os.environ.get("IMAGE_MODEL") or "qwen",
                   help="qwen: Qwen-Image-2.1-Turbo (the default); flux3k-fast: FLUX3-K distilled, 16 steps; "
                        "flux3k-full: FLUX3-K base, 50 steps at CFG 3.5 (oneshot-setup-spark.sh --flux3); "
                        "moss-image: MOSS-Image SFT, 25 steps at CFG 2.5 (oneshot-setup-spark.sh --moss)")
    i.add_argument("--no-upsample", dest="upsample", action="store_false",
                   help="FLUX3-K: send the prompt as written (a full scene caption) instead of BFL's prompt upsampler's")
    i.add_argument("--base", action="store_true", help="the base model without the turbo adapter")
    i.add_argument("--steps", type=int, default=None, help="Qwen's base model (40), FLUX3-K (16 Fast, 50 Full) or MOSS-Image (25)")
    i.add_argument("--cfg", type=float, default=4.0, help="base model only")
    i.add_argument("--negative", default="", help="Qwen's base model; MOSS-Image (default: its template's)")
    i.set_defaults(run=image)
    m = kinds.add_parser("music", help="MiniMax Music 3: lyrics and a style caption to a song (oneshot-setup-spark.sh --music)")
    m.add_argument("--caption", default=""); m.add_argument("--caption-file", default="")
    m.add_argument("--lyrics", default="", help="with [Verse], [Chorus], [Bridge], [Instrumental] ... section tags; empty for an instrumental")
    m.add_argument("--lyrics-file", default="")
    m.add_argument("--seconds", type=float, default=60.0, help="the longest the song may be (10 to 300); the model may end it sooner")
    m.add_argument("--seed", type=int, default=0)
    m.add_argument("--steps", type=int, default=MUSIC_STEPS); m.add_argument("--cfg", type=float, default=MUSIC_CFG)
    m.add_argument("--ar-cfg", type=float, default=MUSIC_AR_CFG); m.add_argument("--top-k", type=int, default=MUSIC_TOP_K)
    m.add_argument("-o", "--output", required=True, help="the song, a .wav")
    m.set_defaults(run=music)
    for kind, said in (("sfx", "Stable Audio Open 1.0: a sound effect from words (oneshot-setup-spark.sh --sound)"),
                       ("foley", "MMAudio: a clip's sound made for its picture (oneshot-setup-spark.sh --sound)")):
        f = kinds.add_parser(kind, help=said)  # the flags of sound_generate.py, which app/sound.py gives either runner
        f.add_argument("--prompt", default=""); f.add_argument("--negative", default="")
        f.add_argument("--seed", type=int, default=1)
        f.add_argument("--steps", type=int); f.add_argument("--cfg", type=float)
        f.add_argument("--seconds", type=float, default=6.0, help="sfx: 1 to 47")
        f.add_argument("--video", help="foley: the clip"); f.add_argument("--max-seconds", type=float, default=FOLEY_SECONDS[1])
        f.add_argument("--foley-db", type=float, default=-6.0); f.add_argument("--replace", action="store_true")
        f.add_argument("--clip-out"); f.add_argument("--take"); f.add_argument("--home")
        f.add_argument("-o", "--output", required=True, help="the sound, a .wav")
        f.set_defaults(run=sound, kind=kind)
    args = parser.parse_args()
    if getattr(args, "text_encoder", "regular") != "regular":
        global H3_TEXT, TEXT_TAG
        H3_TEXT, TEXT_TAG = args.text_encoder, "+" + args.text_encoder
        ensure_server("video")
        listed = call("/object_info/CLIPLoader")["CLIPLoader"]["input"]["required"]["clip_name"][0]
        if H3_TEXT not in listed and f"H3/{H3_TEXT}" in listed:
            H3_TEXT = f"H3/{H3_TEXT}"  # an install that keeps MiniMax H3's text encoders in their own H3 folder
        if H3_TEXT not in listed:
            sys.exit(f"ComfyUI does not list the text encoder {H3_TEXT}")
    try:
        args.run(args)
    except Exception as error:  # noqa: BLE001 - the server went away under the render: say why, not a traceback
        if not isinstance(error, (OSError, ConnectionError)) and type(error).__module__.split(".")[0] != "aiohttp":
            raise
        sys.exit(stopped() or f"ComfyUI stopped answering during the render: {error} (log: {PREFIX}/comfy.log)")


def stopped() -> str:
    """Why the ComfyUI server ended, when its own log says so: the memory guard beside it (memory_guard.py watch)
    stops it when the machine runs low."""

    try:
        tail = (PREFIX / "comfy.log").read_text(errors="replace")[-4000:].splitlines()
    except OSError:
        return ""
    for line in reversed(tail):
        if "stopping ComfyUI" in line and ("memory floor:" in line or "free memory is" in line):
            return ("the render was stopped to protect the machine: " + line.split("] ", 1)[-1].replace("memory floor: ", "").strip()
                    + ". Use a shorter clip, a shorter or fewer reference videos, or a smaller size; a LoRA clip needs more memory than one without.")
        if "stopping the managed server:" in line:
            return ("the render was stopped by this machine's workload limits: " + line.split("managed server: ", 1)[-1].strip()
                    + ". Wait for the machine to cool or for other work on it to finish, then try again.")
    return ""


if __name__ == "__main__":
    main()
