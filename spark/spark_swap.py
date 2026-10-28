#!/usr/bin/env python3
"""Character Swap on a DGX Spark (CUDA): the person in a part of a clip replaced by the character in one picture, with
the scene, the performance and the clip's own sound kept. ComfyUI's own nodes and this file; no node pack.

    spark_swap.py --video clip.mp4 --image character.png --start 2 --seconds 5 --quality 768 -o swapped.mp4
    spark_swap.py ... --no-anchor --direction "she keeps her hands in her pockets" --seed 7 --work folder

The steps, each said as `[tensorfold] stage i/n ...` for the studio's job card:

1. the part      the 5 to 15 s part is cut out and prepared at 24 frames a second, `--quality` pixels on its short side
                 (480 or 768, multiples of 32, the clip's own shape), with its sound beside it. A part with a hard cut
                 in it is refused: the swap is for one continuous shot (app/shot.py finds the cut).
2. the person    SAM 3.1 looks for "person" in the part's first frame (threshold 0.35, two refinement passes). The
                 largest one found is the one swapped; a first frame with nobody in it is refused.
3. following     SAM 3.1 tracks that person through the part (detection 0.5, one object). A frame where the tracker
                 lost the person takes the mask of the nearest frame that has it, and every mask is grown by 64
                 pixels, so hair, hands and the outfit's edges are inside it.
4. the anchor    (on by default) the picture model puts the character, outfit included, into the part's first frame
                 (FLUX3-K Fast, or Qwen-Image-2.1 where FLUX3-K is not installed). The swap starts from that frame.
5. the windows   MiniMax H3 Ref2VA with Akatz Labs' Character Swap LoRA and LightX2V's 8-step Turbo LoRA (both at
                 strength 1): 8 passes of er_sde, normal schedule, CFG 1, sigma shift 12 for the picture and 3 for the
                 sound. The part is <Video 1> and its own sound a reference, the character <Picture 1> (the anchor
                 <Picture 2> and the guide of the first frame). Only the grown mask is drawn again: the rest of each
                 frame is the clip's own. A part over 124 frames is made in windows of at most 124, each starting
                 with the last 18 frames of the one before, those included (spark/swap_plan.py).
6. assembly      the windows' frames joined, the part's own sound laid under them, H.264 at 24 frames a second.

The models stay loaded from one window to the next: only the first window loads them.

The settings are the ones Akatz Labs published for the LoRA and Comfy Org's Openjutsu app uses (both MIT); the code is
the studio's own.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import swap_plan as plan  # noqa: E402

PROMPT = ("Replace the selected person in <Video 1> with the character in <Picture 1>. "
          "Preserve source performance, scene and audio timing.")
ANCHOR_SAYS = "Start from the first frame in <Picture 2>."
# measured on Qwen-Image-2.1 (the edit every install has): "put the character of image 2 in place of the person in
# image 1" gave the first frame back unchanged; this wording put the character in, outfit and all
ANCHOR_EDIT = ("Image 1 with the person replaced by the character of image 2, same pose, same framing, same background; "
               "the character wears the outfit of image 2.")
UNCHANGED = 6.0  # grey levels: an anchor whose person region differs less than this from the clip's frame is not used
SAM = "sam3.1_multiplex_fp16.safetensors"
SWAP_LORA = "h3_character_swap_v1.5.safetensors"
TURBO_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
STEPS, CFG, SAMPLER, SCHEDULER = 8, 1.0, "er_sde", "normal"
SHIFT_VIDEO, SHIFT_AUDIO = 12.0, 3.0
DETECT, REFINE, TRACK, GROW = 0.35, 2, 0.5, 64
# what counts as a person in the first frame: SAM 3.1's score for "person" and the box's share of the frame. Measured
# 2026-10-27 on a DGX Spark: people (real and drawn, 13 to 58% of the frame) 0.94 to 0.98; a frame with nobody in it
# 0.31 (a flat colour), 0.40 (a Mandelbrot), 0.44 to 0.49 (an empty car park) and 0.54 (a gradient, 56% of the frame)
PERSON_SCORE, PERSON_AREA = 0.7, 0.02
EMPTY = 64  # pixels: a mask smaller than this is a frame where the tracker lost the person
SOUND_RATE = 48000
# <Video 1>, the part as the model reads its performance, at half the canvas (448x256 for 864x480, 672x384 for
# 1344x768): its rows ride through every pass. Measured on a DGX Spark, 124 frames: at 864x480 21 to 23 s a pass
# against 31.5 with it at 384 and 35 at the canvas's own size; at 1344x768 65 s against 146. The person's moves and
# timing were followed as closely. SWAP_REF_SIDE sets its short side in pixels instead (0: the canvas's size).
REF_SIDE = os.environ.get("SWAP_REF_SIDE", "half")


def say(text: str) -> None:
    print("[tensorfold] " + text, flush=True)


class Stages:
    """`[tensorfold] stage i/n words`: the studio shows the words on the job card, with the passes under them."""

    def __init__(self, names: list[str]):
        self.names, self.at, self.took, self.since = names, 0, [], time.time()

    def next(self, words: str) -> None:
        self.done()
        self.at += 1
        self.since = time.time()
        say(f"stage {self.at}/{len(self.names)} {words}")

    def done(self) -> None:
        if self.at:
            self.took.append((self.names[self.at - 1], round(time.time() - self.since, 1)))


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        sys.exit(f"{name} is not installed on this machine (sudo apt install ffmpeg)")
    return found


def probe(path: Path) -> dict:
    done = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                           "-of", "json", str(path)], capture_output=True, text=True)
    try:
        data = json.loads(done.stdout or "{}")
        video = next(s for s in data.get("streams", []) if s.get("codec_type") == "video")
        return {"width": int(video["width"]), "height": int(video["height"]), "seconds": float(data["format"]["duration"]),
                "sound": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}
    except (StopIteration, KeyError, TypeError, ValueError):
        sys.exit(f"{path.name} could not be read as a video")


def ffmpeg(*args: str, what: str) -> None:
    done = subprocess.run([tool("ffmpeg"), "-v", "error", "-y", *args], capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"ffmpeg could not {what}: {done.stderr.strip()[-300:]}")


def lossless(frames_pattern: str, target: Path, fps: int = plan.FPS) -> None:
    ffmpeg("-framerate", str(fps), "-i", frames_pattern, "-c:v", "libx264rgb", "-qp", "0", "-preset", "veryfast",
           "-pix_fmt", "rgb24", str(target), what=f"write {target.name}")


# ---------------------------------------------------------------- 1. the part

def prepare(args, work: Path) -> dict:
    """The part as frames at the canvas (PNG, lossless), its sound (wav), and what was measured."""

    info = probe(Path(args.video))
    if args.start < 0:
        sys.exit("the part starts at 0 s or later")
    low, high = plan.SECONDS
    if not low - 0.01 <= args.seconds <= high + 0.01:
        sys.exit(f"the part is {low:g} to {high:g} seconds long")
    if args.start + args.seconds > info["seconds"] + 0.05:
        sys.exit(f"the clip is {info['seconds']:.1f} s long: a {args.seconds:g} s part from {args.start:g} s runs past its end")
    width, height = plan.canvas(info["width"], info["height"], args.quality)
    count = plan.part_frames(args.seconds)
    frames = work / "source"
    frames.mkdir(parents=True, exist_ok=True)
    # cover the canvas with the clip's own shape kept, then crop the few pixels the rounding to 32 leaves over
    ffmpeg("-ss", f"{args.start:.3f}", "-i", args.video, "-t", f"{args.seconds:.3f}", "-map", "0:v:0", "-an", "-vf",
           f"fps={plan.FPS},scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},setsar=1",
           "-frames:v", str(count), str(frames / "%05d.png"), what="cut the part out of the clip")
    made = sorted(frames.glob("*.png"))
    if len(made) < count - 2:
        sys.exit(f"the part gave {len(made)} frames where {count} were expected")
    while len(made) < count:  # a clip that ends a frame early: its last frame is held
        shutil.copy(made[-1], frames / f"{len(made) + 1:05d}.png")
        made = sorted(frames.glob("*.png"))
    sound = None
    if info["sound"]:
        sound = work / "source.wav"
        ffmpeg("-ss", f"{args.start:.3f}", "-i", args.video, "-t", f"{count / plan.FPS:.6f}", "-map", "0:a:0", "-vn", "-ac", "2",
               "-ar", str(SOUND_RATE), "-c:a", "pcm_s16le", "-af", f"apad=whole_dur={count / plan.FPS:.6f}", str(sound),
               what="take the part's sound")
    return {"width": width, "height": height, "frames": made[:count], "count": count, "sound": sound, "clip": info}


def refuse_cuts(part: dict) -> None:
    """One continuous shot: a hard cut inside the part is refused before anything is loaded (app/shot.py's measure)."""

    sys.path.insert(0, str(HERE.parent / "app"))
    try:
        import numpy as np
        from PIL import Image
        import shot
    except ImportError:
        say("the part was not checked for a cut (numpy or PIL is missing)")
        return
    small = (shot.WIDTH, max(2, round(shot.WIDTH * part["height"] / part["width"])))
    stack = np.stack([shot.grey(Image.open(f), small) for f in part["frames"]])
    found = shot.cuts(stack)
    if found:
        at = found[0]["seconds"]
        sys.exit(f"the part has a cut {at:.2f} s in: Character Swap works on one continuous shot. Choose a part that ends before "
                 "the cut or starts after it")


# ---------------------------------------------------------------- ComfyUI

def input_root() -> Path:
    return Path(os.environ.get("SPARK_COMFY_INPUT") or sg.PREFIX / "ComfyUI" / "input")


def put(path: Path) -> str:
    """A file the server reads, linked into its input folder: the server is on this machine, and the lossless frames
    of a part are larger than its upload takes (413 over 100 MB). Returns the name its loader wants."""


    name = f"studio_{uuid.uuid4().hex[:10]}{Path(path).suffix.lower()}"
    input_root().mkdir(parents=True, exist_ok=True)
    try:  # a hard link: the loaders refuse a symbolic link that leads out of the folder
        os.link(Path(path).resolve(), input_root() / name)
    except OSError:  # another file system
        shutil.copy(path, input_root() / name)
    sg.UPLOADED.append(name)
    return name


def collect(prompt_id: str, node: str) -> list[dict]:
    """The files a finished workflow's node saved, in order."""

    record = sg.history_of(prompt_id)
    if record["status"].get("status_str") != "success":
        errors = [m[1] for m in record["status"].get("messages", []) if m[0] == "execution_error"]
        detail = errors[0].get("exception_message", "").strip() if errors else json.dumps(record["status"])[:600]
        sys.exit(f"ComfyUI failed: {detail} (log: {sg.side('log')})")
    items = (record.get("outputs", {}).get(node) or {}).get("images") or []
    return [item for item in items if isinstance(item, dict) and item.get("filename")]


def run_graph(workflow: dict, sampler: str, named: dict | None = None, stages: list | None = None) -> str:
    import urllib.error

    try:
        return asyncio.run(sg.run(workflow, sampler, named, stages))
    except urllib.error.HTTPError as error:  # the server refused the workflow: its own words say which node and why
        try:
            said = json.loads(error.read() or b"{}")
            nodes = [f"{workflow.get(n, {}).get('class_type', n)}: " + "; ".join(e.get("details") or e.get("message", "") for e in v.get("errors", []))
                     for n, v in (said.get("node_errors") or {}).items()]
            detail = "; ".join(nodes) or (said.get("error") or {}).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        sys.exit(f"ComfyUI refused the workflow ({error.code}): {detail or error.reason}")
    except KeyboardInterrupt:
        sg.call("/interrupt", {})
        raise


def fetch_all(items: list[dict], folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for index, item in enumerate(items):
        target = folder / f"{index + 1:05d}.png"
        sg.fetch(item, target)
        out.append(target)
    forget_outputs(items)
    return out


def forget_outputs(items: list[dict]) -> None:
    """The frames ComfyUI saved are this run's own: taken out of its output folder once they are here."""

    root = Path(os.environ.get("SPARK_COMFY_OUTPUT") or sg.PREFIX / "ComfyUI" / "output")
    for item in items:
        (root / item.get("subfolder", "") / item["filename"]).unlink(missing_ok=True)


def sam_nodes() -> dict:
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "person"}}}


# ---------------------------------------------------------------- 2. the person

def preview_text(prompt_id: str, node: str) -> list:
    """What a finished workflow's PreviewAny node showed, read back as JSON (an empty list when it showed nothing)."""

    record = sg.history_of(prompt_id)
    if record["status"].get("status_str") != "success":
        collect(prompt_id, node)  # says why and exits
    shown = (record.get("outputs", {}).get(node) or {}).get("text") or []
    if not shown or not str(shown[0]).strip():
        return []
    try:
        return json.loads(shown[0])
    except (TypeError, ValueError):  # not "nobody there": what it showed is said, so a changed node is seen at once
        sys.exit(f"SAM 3.1's boxes could not be read (ComfyUI showed: {str(shown[0])[:200]})")


def people_in(frame: str, width: int, height: int) -> list[dict]:
    """SAM 3.1's guesses at a person in an uploaded frame, best first: {"score", "share"} with the box's share of the
    frame. A frame with nobody in it still gives guesses (a gradient, an empty room): PERSON_SCORE and PERSON_AREA
    tell a person from them."""

    w = sam_nodes()
    w["3"] = {"class_type": "LoadImage", "inputs": {"image": frame}}
    w["4"] = {"class_type": "SAM3_Detect", "inputs": {"model": ["1", 0], "image": ["3", 0], "conditioning": ["2", 0],
                                                      "threshold": DETECT, "refine_iterations": 0, "individual_masks": False}}
    w["5"] = {"class_type": "PreviewAny", "inputs": {"source": ["4", 1]}}
    shown = preview_text(run_graph(w, "4"), "5")
    boxes = shown[0] if shown and isinstance(shown[0], list) else shown  # one list of boxes for each frame given
    out = []
    for box in boxes if isinstance(boxes, list) else []:
        try:
            share = float(box["width"]) * float(box["height"]) / float(width * height)
            out.append({"score": float(box["score"]), "share": share})
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    if boxes and not out:  # boxes of another shape: said, never read as nobody in the frame
        sys.exit(f"SAM 3.1's boxes could not be read (ComfyUI showed: {json.dumps(boxes)[:200]})")
    return sorted(out, key=lambda one: -one["score"])


def find_person(part: dict, work: Path) -> Path:
    """The largest person SAM 3.1 finds in the first frame, as a mask picture; exits when there is none."""

    import numpy as np
    from PIL import Image

    nobody = ("no person was found in the part's first frame: Character Swap replaces a person who is in the first frame. "
              "Start the part where the person is in view")
    frame = sg.upload(str(part["frames"][0]))
    guesses = people_in(frame, part["width"], part["height"])
    people = [one for one in guesses if one["score"] >= PERSON_SCORE and one["share"] >= PERSON_AREA]
    if not people:
        best = (f" (SAM 3.1's best guess scored {guesses[0]['score']:.2f} over {guesses[0]['share'] * 100:.0f}% of the frame; "
                f"a person scores {PERSON_SCORE:g} or more over {PERSON_AREA * 100:g}% or more)") if guesses else ""
        sys.exit(nobody + best)
    w = sam_nodes()
    w["3"] = {"class_type": "LoadImage", "inputs": {"image": frame}}
    w["4"] = {"class_type": "SAM3_Detect", "inputs": {"model": ["1", 0], "image": ["3", 0], "conditioning": ["2", 0],
                                                      "threshold": PERSON_SCORE, "refine_iterations": REFINE, "individual_masks": True}}
    w["5"] = {"class_type": "MaskToImage", "inputs": {"mask": ["4", 0]}}
    w["6"] = {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": f"studio/swap_find_{uuid.uuid4().hex[:8]}"}}
    found = fetch_all(collect(run_graph(w, "4"), "6"), work / "found")
    areas = []
    for path in found:
        mask = np.asarray(Image.open(path).convert("L")) >= 128
        areas.append((int(mask.sum()), path))
    areas = [one for one in areas if one[0] >= EMPTY]
    if not areas:
        sys.exit(nobody)
    areas.sort(key=lambda one: -one[0])
    say(f"{len(areas)} {'person' if len(areas) == 1 else 'people'} in the first frame; the largest ({areas[0][0] * 100 / (part['width'] * part['height']):.0f}% of the frame) is swapped")
    chosen = work / "person.png"
    shutil.copy(areas[0][1], chosen)
    return chosen


# ---------------------------------------------------------------- 3. following

def follow_person(part: dict, chosen: Path, work: Path) -> list:
    """The person's mask on every frame of the part: tracked, gaps filled, grown by GROW pixels. A list of bool arrays."""

    import numpy as np
    from PIL import Image

    clip = work / "source.mp4"
    if not clip.is_file():
        lossless(str(work / "source" / "%05d.png"), clip)
    w = sam_nodes()
    w["3"] = {"class_type": "LoadVideo", "inputs": {"file": put(clip)}}
    w["4"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["3", 0]}}
    w["5"] = {"class_type": "LoadImage", "inputs": {"image": sg.upload(str(chosen))}}
    w["6"] = {"class_type": "ImageToMask", "inputs": {"image": ["5", 0], "channel": "red"}}
    w["7"] = {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["4", 0], "model": ["1", 0], "initial_mask": ["6", 0],
                                                          "conditioning": ["2", 0], "detection_threshold": TRACK, "max_objects": 1,
                                                          "detect_interval": 1}}
    w["8"] = {"class_type": "SAM3_TrackToMask", "inputs": {"track_data": ["7", 0], "object_indices": ""}}
    w["9"] = {"class_type": "MaskToImage", "inputs": {"mask": ["8", 0]}}
    w["10"] = {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": f"studio/swap_track_{uuid.uuid4().hex[:8]}"}}
    tracked = fetch_all(collect(run_graph(w, "7", {"7": "frame"}), "10"), work / "tracked")
    forget_upload(clip)
    masks = [np.asarray(Image.open(p).convert("L")) >= 128 for p in tracked]
    first = np.asarray(Image.open(chosen).convert("L")) >= 128
    if not masks:
        sys.exit("SAM 3.1 returned no masks for the part")
    while len(masks) < part["count"]:
        masks.append(masks[-1])
    masks = masks[:part["count"]]
    if masks[0].sum() < EMPTY:
        masks[0] = first
    have = [i for i, m in enumerate(masks) if m.sum() >= EMPTY]
    filled = 0
    for i, m in enumerate(masks):
        if m.sum() < EMPTY:
            nearest = min(have, key=lambda j: (abs(j - i), j > i))
            masks[i] = masks[nearest]
            filled += 1
    if filled:
        say(f"the tracker lost the person on {filled} of {len(masks)} frames: those take the nearest frame's mask")
    return grow(masks)


def grow(masks: list) -> list:
    """Every mask grown by GROW pixels (a square reach), a few frames at a time."""

    import numpy as np
    import torch

    reach, out = 2 * GROW + 1, []
    for at in range(0, len(masks), 24):
        chunk = torch.from_numpy(np.stack(masks[at:at + 24]).astype(np.float32))[:, None]
        chunk = torch.nn.functional.max_pool2d(chunk, (1, reach), stride=1, padding=(0, GROW))
        chunk = torch.nn.functional.max_pool2d(chunk, (reach, 1), stride=1, padding=(GROW, 0))
        out.extend(m for m in (chunk[:, 0].numpy() > 0.5))
    return out


def forget_upload(path: Path) -> None:
    """The videos and sounds put in the server's input folder so far: taken out again (the pictures are small and stay
    until the run ends)."""

    for name in list(sg.UPLOADED):
        if name.endswith((".mp4", ".wav")):
            (input_root() / name).unlink(missing_ok=True)
            sg.UPLOADED.remove(name)


def preview(part: dict, masks: list, target: Path) -> None:
    """The part with the person to be swapped tinted green: what the job card shows beside the result."""

    import numpy as np
    from PIL import Image

    folder = target.parent / (target.stem + "_frames")
    folder.mkdir(parents=True, exist_ok=True)
    for index, (frame, mask) in enumerate(zip(part["frames"], masks)):
        rgb = np.asarray(Image.open(frame).convert("RGB")).astype(np.float32)
        rgb[mask] = rgb[mask] * 0.45 + np.array([40, 220, 90], np.float32) * 0.55
        Image.fromarray(rgb.astype(np.uint8)).save(folder / f"{index + 1:05d}.png", compress_level=1)
    ffmpeg("-framerate", str(plan.FPS), "-i", str(folder / "%05d.png"), "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target), what="write the mask preview")
    shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- 4. the anchor

def anchor(args, part: dict, work: Path, mask=None) -> Path | None:
    """The part's first frame with the character in it, by the studio's picture edit (spark/spark_edit.py): FLUX3-K Fast
    where it is installed, else (or when it fails) Qwen-Image-2.1."""

    have = sg.call("/object_info/UNETLoader")["UNETLoader"]["input"]["required"]["unet_name"][0]
    models = (["flux3k-fast"] if sg.flux3_unet("flux3k-fast") in have else []) + ["qwen"]
    target, said = work / "anchor.png", []
    for model in models:
        say("the anchor: " + ("FLUX3-K Fast" if model != "qwen" else "Qwen-Image-2.1") + " puts the character into the first frame")
        command = [sys.executable, str(HERE / "spark_edit.py"), "--model", model, "--image", str(part["frames"][0]), "--image",
                   args.image, "--prompt", ANCHOR_EDIT, "--seed", str(args.seed), "-o", str(target)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        said = []
        for line in process.stdout:
            said.append(line.rstrip())
            print(line, end="", flush=True)
        if not process.wait() and target.is_file():
            break
        say(f"the anchor: {model} could not make it ({said[-1][-200:] if said else 'no reason given'})")
    else:
        # the swap does not need it: it runs without, as Openjutsu does when its first frame fails
        say("the anchor picture could not be made: the swap runs without it")
        return None
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(target) as made:  # exactly the canvas, cropped from the centre
        ImageOps.fit(made.convert("RGB"), (part["width"], part["height"]), Image.Resampling.LANCZOS).save(target)
    if mask is not None and mask.any():
        # a picture model that gave the frame back as it was would lock the clip's own person into the first frame
        new = np.asarray(Image.open(target).convert("L"), np.float32)
        old = np.asarray(Image.open(part["frames"][0]).convert("L"), np.float32)
        changed = float(np.abs(new - old)[mask].mean())
        if changed < UNCHANGED:
            say(f"the anchor: the picture model left the person as they were ({changed:.1f} grey levels): the swap runs without it")
            return None
        say(f"the anchor: the person's region changed by {changed:.1f} grey levels")
    return target


# ---------------------------------------------------------------- 5. the windows

def engine_blocks() -> str | None:
    """The bf16 Ref2VA checkpoint for TensorFold's int8 blocks, with SWAP_ENGINE=tensorfold where this server has the
    node and the file; None (the default) runs ComfyUI's own blocks."""

    # measured on a DGX Spark (5 s at 1344x768, 54.5K rows a pass, dense): 72 s a pass on the engine against 67 s on
    # ComfyUI's own int8 blocks. The engine's speed is FastH3's sparse attention, which needs gate weights Ref2VA has
    # not; and it takes Ref2VA only with a build that allows a checkpoint without them. So it is asked for by name.
    if os.environ.get("SWAP_ENGINE", "comfy") != "tensorfold" or "TensorFoldH3Blocks" not in sg.call("/object_info/TensorFoldH3Blocks"):
        return None
    have = sg.call("/object_info/UNETLoader")["UNETLoader"]["input"]["required"]["unet_name"][0]
    return sg.REF2VA["bf16"] if sg.REF2VA["bf16"] in have else None


def weights() -> str:
    have = sg.call("/object_info/UNETLoader")["UNETLoader"]["input"]["required"]["unet_name"][0]
    asked = os.environ.get("SWAP_WEIGHTS", "int8")
    order = [asked] + [w for w in ("int8", "bf16") if w != asked]
    for one in order:
        if sg.REF2VA.get(one) in have:
            return sg.REF2VA[one]
    sys.exit("MiniMax H3 Ref2VA is not installed: bash oneshot-setup-spark.sh --ref2va")


def window_inputs(one: dict, part: dict, masks: list, made: list[Path], work: Path, anchored: Path | None) -> dict:
    """The files one window reads: the frames the model starts from (the previous window's at the start), the part
    as <Video 1>, the mask on the latent grid, and the sound."""

    import numpy as np
    from PIL import Image

    folder = work / f"window{one['index']}"
    pixels, refs = folder / "pixels", folder / "ref"
    for f in (pixels, refs):
        f.mkdir(parents=True, exist_ok=True)
    count, last = part["count"], part["count"] - 1
    for i in range(one["length"]):
        source = min(one["start"] + i, last)
        own = part["frames"][source]
        held = made[source] if i < one["held"] else None
        for f, path in ((pixels, held or own), (refs, own)):
            link = f / f"{i + 1:05d}.png"
            link.unlink(missing_ok=True)
            link.symlink_to(path)
    lossless(str(pixels / "%05d.png"), folder / "pixels.mp4")
    scale = 0.5 if REF_SIDE == "half" else min(1.0, int(REF_SIDE) / min(part["width"], part["height"])) if int(REF_SIDE or 0) else 1.0
    same = one["held"] == 0 and scale == 1.0
    if not same:
        if scale == 1.0:
            lossless(str(refs / "%05d.png"), folder / "ref.mp4")
        else:  # <Video 1> smaller than the canvas: fewer rows in every pass
            size = [max(32, round(v * scale / 32) * 32) for v in (part["width"], part["height"])]
            ffmpeg("-framerate", str(plan.FPS), "-i", str(refs / "%05d.png"), "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos",
                   "-c:v", "libx264rgb", "-qp", "0", "-preset", "veryfast", "-pix_fmt", "rgb24", str(folder / "ref.mp4"),
                   what="write the window's reference")
    # the mask on the latent grid: a latent frame is drawn again where any of its pixel frames has the person in a
    # 16 x 16 block; the frames carried from the window before are held as they are
    h, w = part["height"] // 16, part["width"] // 16
    grid = []
    hold = plan.held_latents(one["held"])
    for k, (a, b) in enumerate(plan.latent_spans(one["length"])):
        if k < hold:
            grid.append(np.zeros((h, w), bool))
            continue
        span = np.zeros((part["height"], part["width"]), bool)
        for i in range(a, b + 1):
            span |= masks[min(one["start"] + i, last)]
        grid.append(span.reshape(h, 16, w, 16).any(axis=(1, 3)))
    cells = folder / "mask"
    cells.mkdir(exist_ok=True)
    for k, g in enumerate(grid):
        Image.fromarray((g * 255).astype(np.uint8)).convert("RGB").save(cells / f"{k + 1:05d}.png")
    lossless(str(cells / "%05d.png"), folder / "mask.mp4")
    sound = None
    if part["sound"]:
        sound = folder / "sound.wav"
        seconds = one["length"] / plan.FPS
        ffmpeg("-ss", f"{one['start'] / plan.FPS:.6f}", "-i", str(part["sound"]), "-t", f"{seconds:.6f}", "-af",
               f"apad=whole_dur={seconds:.6f}", "-ac", "2", "-ar", str(SOUND_RATE), "-c:a", "pcm_s16le", str(sound),
               what="cut the window's sound")
    return {"pixels": folder / "pixels.mp4", "ref": folder / ("pixels.mp4" if same else "ref.mp4"), "mask": folder / "mask.mp4",
            "sound": sound, "cells": len(grid), "anchor": anchored if one["index"] == 1 else None}


def window_graph(one: dict, files: dict, part: dict, args, unet: str, loras: list[tuple[str, float]], character: str,
                 blocks: str | None = None) -> dict:
    prompt = PROMPT + (" " + ANCHOR_SAYS if files["anchor"] else "") + (" " + args.direction.strip() if args.direction.strip() else "")
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["1", 0], "shift_video": SHIFT_VIDEO, "shift_audio": SHIFT_AUDIO}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": sg.H3_TEXT, "type": "minimax", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": sg.H3_VAE}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": sg.H3_AUDIO_VAE}},
        "10": {"class_type": "LoadImage", "inputs": {"image": character}},
        "11": {"class_type": "LoadVideo", "inputs": {"file": put(files["ref"])}},
        "12": {"class_type": "GetVideoComponents", "inputs": {"video": ["11", 0]}},
        "16": {"class_type": "LoadVideo", "inputs": {"file": put(files["mask"])}},
        "17": {"class_type": "GetVideoComponents", "inputs": {"video": ["16", 0]}},
        "18": {"class_type": "ImageToMask", "inputs": {"image": ["17", 0], "channel": "red"}},
        "20": {"class_type": "MiniMaxH3ReferenceToVideo",
               "inputs": {"clip": ["5", 0], "vae": ["6", 0], "audio_vae": ["7", 0], "prompt": prompt, "width": part["width"],
                          "height": part["height"], "length": one["length"], "ref_image_size": "match",
                          "ref_images.ref_image_0": ["10", 0], "ref_videos.ref_video_0": ["12", 0]}},
        # the frames the window starts from, and the mask of what is drawn again: the rest is kept as it is
        "21": {"class_type": "VAEEncode", "inputs": {"pixels": ["15", 0], "vae": ["6", 0]}},
        "22": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["21", 0], "mask": ["18", 0]}},
        "23": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["20", 1]}},
        "24": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["22", 0], "audio_latent": ["23", 1]}},
        "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": args.seed + one["index"] - 1}},
        "31": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": SAMPLER}},
        "34": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["30", 0], "guider": ["33", 0], "sampler": ["31", 0],
                                                                 "sigmas": ["32", 0], "latent_image": ["24", 0]}},
        "35": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["34", 0]}},
        "36": {"class_type": "VAEDecode", "inputs": {"samples": ["35", 0], "vae": ["6", 0]}},
        "37": {"class_type": "SaveImage", "inputs": {"images": ["36", 0], "filename_prefix": f"studio/swap_w{one['index']}_{uuid.uuid4().hex[:8]}"}},
    }
    if files["pixels"] == files["ref"]:
        w["15"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["11", 0]}}
    else:
        w["14"] = {"class_type": "LoadVideo", "inputs": {"file": put(files["pixels"])}}
        w["15"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["14", 0]}}
    model = ["2", 0]
    for index, (name, strength) in enumerate(loras):
        w[str(3 + index if index < 2 else 80 + index)] = {"class_type": "LoraLoaderModelOnly",
                                                         "inputs": {"model": model, "lora_name": name, "strength_model": strength}}
        model = [str(3 + index if index < 2 else 80 + index), 0]
    if blocks:
        # the 50 blocks of every pass on TensorFold's CUDA engine, int8 on the tensor cores, dense attention: the LoRAs'
        # block projections are merged into the engine's weights (from the bf16 checkpoint) once, ComfyUI keeps the rest
        w["8"] = {"class_type": "TensorFoldH3Blocks", "inputs": {"model": model, "unet_name": blocks, "sparsity": 0.0}}
        model = ["8", 0]
    positive = ["20", 0]
    if files["sound"]:
        # the part's own sound: a reference beside <Video 1>, and the guide of the window's sound from its first frame
        w["13"] = {"class_type": "LoadAudio", "inputs": {"audio": put(files["sound"])}}
        w["20"]["inputs"]["ref_audios.ref_audio_0"] = ["13", 0]
        w["25"] = {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": positive, "audio_vae": ["7", 0], "latent": ["24", 0],
                                                                "audio": ["13", 0], "frame_idx": 0}}
        positive = ["25", 0]
    if files["anchor"]:
        w["19"] = {"class_type": "LoadImage", "inputs": {"image": sg.upload(str(files["anchor"]))}}
        w["20"]["inputs"]["ref_images.ref_image_1"] = ["19", 0]
        w["26"] = {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": positive, "vae": ["6", 0], "latent": ["24", 0],
                                                                "image": ["19", 0], "frame_idx": 0}}
        positive = ["26", 0]
    w["32"] = {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": SCHEDULER, "steps": STEPS, "denoise": 1.0}}
    w["33"] = ({"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": positive}} if CFG == 1.0 else
               {"class_type": "CFGGuider", "inputs": {"model": model, "positive": positive, "negative": positive, "cfg": CFG}})
    return w


def window(one: dict, files: dict, part: dict, args, unet, loras, character, work: Path, blocks: str | None = None) -> list[Path]:
    w = window_graph(one, files, part, args, unet, loras, character, blocks)
    took: list = []
    frames = fetch_all(collect(run_graph(w, "34", stages=took), "37"), work / f"window{one['index']}" / "made")
    spent: dict = {}
    for node, seconds in took:
        spent[node] = spent.get(node, 0.0) + seconds
    loads = sum(v for k, v in spent.items() if k in ("UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "TensorFoldH3Blocks", "MiniMaxH3SigmaShift"))
    passes = spent.get("SamplerCustomAdvanced", 0.0)
    say(f"window {one['index']}: loading {loads:.1f} s, the prompt and references encoded {spent.get('MiniMaxH3ReferenceToVideo', 0.0):.1f} s, "
        f"frames encoded {spent.get('VAEEncode', 0.0):.1f} s, {STEPS} passes {passes:.1f} s ({passes / STEPS:.1f} s a pass), "
        f"decoded {spent.get('VAEDecode', 0.0):.1f} s")
    forget_upload(files["pixels"])
    if len(frames) < one["length"] - 2:
        sys.exit(f"window {one['index']} gave {len(frames)} frames of {one['length']}")
    if len(frames) < one["length"]:  # the decoder can give one frame short at the end: the last one is held
        say(f"window {one['index']}: the decoder gave {len(frames)} frames of {one['length']}; the last is held")
        while len(frames) < one["length"]:
            frames.append(frames[-1])
    return frames


# ---------------------------------------------------------------- 6. assembly

def assemble(made: list[Path], part: dict, target: Path, work: Path) -> None:
    folder = work / "final"
    folder.mkdir(exist_ok=True)
    for index, frame in enumerate(made):
        link = folder / f"{index + 1:05d}.png"
        link.unlink(missing_ok=True)
        link.symlink_to(frame)
    seconds = f"{part['count'] / plan.FPS:.6f}"
    sound = ["-i", str(part["sound"]), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "256k", "-ar", str(SOUND_RATE)] if part["sound"] else []
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-framerate", str(plan.FPS), "-i", str(folder / "%05d.png"), *sound, "-t", seconds, "-c:v", "libx264", "-crf", "16",
           "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(plan.FPS), "-movflags", "+faststart", str(target),
           what="write the swapped part")


# ---------------------------------------------------------------- the run

def server(kind: str) -> None:
    """ComfyUI up for this step: a server the upsampler's or the remover's node pack patched starts again (their packs
    change H3's sequence layout in the process they ran in, spark/spark_workflows.py), and a change of models unloads
    the ones before (spark_generate.ensure_server)."""

    patched = sg.PREFIX / "comfy.patched"
    if sg.up() and patched.exists():
        other = patched.read_text().split()
        port = sg.URL.rsplit(":", 1)[-1]
        pid = subprocess.run(["pgrep", "-f", f"[m]ain.py --listen 127.0.0.1 --port {port}"], capture_output=True, text=True).stdout.split("\n")[0].strip()
        if len(other) == 2 and other[1] == pid:
            sg.fresh_server("an action's node pack changed this server's H3 layout")
    sg.ensure_server(kind)


def need(nodes: tuple[str, ...]) -> None:
    have = sg.call("/object_info")
    missing = [n for n in nodes if n not in have]
    if missing:
        sys.exit(f"this ComfyUI has no {', '.join(missing)} node: run `bash oneshot-setup-spark.sh --upgrade` (ComfyUI's pinned version has them)")


def main() -> None:
    # a cancelled job is stopped with SIGTERM (server.cancel): leave through the cleanup below, so the work folder's
    # frames and the pictures handed to ComfyUI do not stay behind
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--video", required=True)
    parser.add_argument("--image", required=True, help="the character, outfit included")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--quality", type=int, choices=plan.QUALITIES, default=768)
    parser.add_argument("--no-anchor", dest="anchor", action="store_false")
    parser.add_argument("--direction", default="")
    parser.add_argument("--seed", type=int, default=904239)
    parser.add_argument("--swap-lora", required=True, help="Akatz Labs' Character Swap LoRA (h3_character_swap_v1.5)")
    parser.add_argument("--turbo-lora", required=True, help="LightX2V's 8-step Turbo LoRA for MiniMax H3")
    parser.add_argument("--work", default="", help="a folder for the frames (default: beside the output, removed at the end)")
    parser.add_argument("--keep", action="store_true", help="keep the work folder")
    parser.add_argument("--mask-preview", default="", help="also write the part with the person tinted green here")
    parser.add_argument("--anchor-out", default="", help="also keep the anchor picture here")
    parser.add_argument("--masks-only", action="store_true", help="find and follow the person, write --mask-preview, and stop (a quick look)")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    for path, what in ((args.video, "clip"), (args.image, "character picture"), (args.swap_lora, "Character Swap LoRA"),
                       (args.turbo_lora, "Turbo LoRA")):
        if not Path(path).is_file():
            sys.exit(f"no {what} at {path}")
    target = Path(args.output)
    for path in (args.mask_preview, args.anchor_out):  # their folders are made here, when the job runs (app/swap.py)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    work = Path(args.work) if args.work else target.parent / f".swap-{target.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    started = time.time()
    count = plan.part_frames(args.seconds)
    windows = plan.windows(count)
    names = ["the part", "the person", "following"] + (["the anchor"] if args.anchor else []) + \
        [f"window {w['index']} of {len(windows)}" for w in windows] + ["assembly"]
    stages = Stages(names)
    try:
        stages.next("cutting the part out and checking it is one shot")
        part = prepare(args, work)
        refuse_cuts(part)
        say(f"the part: {count} frames ({count / plan.FPS:.2f} s) at {part['width']}x{part['height']}, "
            f"{len(windows)} window{'s' if len(windows) > 1 else ''}" + ("" if part["sound"] else ", no sound of its own"))

        stages.next("finding the person in the first frame")
        server("swap")
        need(("SAM3_Detect", "PreviewAny", "SAM3_VideoTrack", "SAM3_TrackToMask", "MiniMaxH3ReferenceToVideo", "MiniMaxH3AddGuide",
              "LTXVConcatAVLatent", "LTXVSeparateAVLatent", "SetLatentNoiseMask"))
        if SAM not in sg.call("/object_info/CheckpointLoaderSimple")["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]:
            sys.exit("SAM 3.1 is not installed: bash oneshot-setup-spark.sh --swap")
        chosen = find_person(part, work)

        stages.next("following the person through the part")
        masks = follow_person(part, chosen, work)
        if args.mask_preview:
            preview(part, masks, Path(args.mask_preview))
        if args.masks_only:
            stages.done()
            say(json.dumps({"output": args.mask_preview, "masks_only": True, "stages": dict(stages.took), "total_s": round(time.time() - started, 1)}))
            return

        anchored = None
        if args.anchor:
            stages.next("putting the character into the first frame")
            anchored = anchor(args, part, work, masks[0])
            if anchored is not None and args.anchor_out:
                shutil.copy(anchored, args.anchor_out)

        # SAM and MiniMax H3 share one server kind: a swap after a swap finds H3 still loaded (only the anchor's picture
        # model, a kind of its own, unloads it)
        server("swap")
        unet, blocks = weights(), engine_blocks()
        loras = [sg.hub_loras([f"{args.turbo_lora}:1"])[0], sg.hub_loras([f"{args.swap_lora}:1"])[0]]
        for name, _ in loras:
            if not sg.installed("lora", name):
                sys.exit(f"ComfyUI does not list the LoRA {name}")
        say(f"engine: cuda ({'TensorFold int8 blocks in ComfyUI, the LoRAs merged into them' if blocks else 'ComfyUI'}, MiniMax H3 Ref2VA "
            f"{(blocks or unet).split('pruned_')[1].split('.')[0]}), the Character Swap and "
            f"8-step Turbo LoRAs at 1, {STEPS} passes of {SAMPLER}, CFG {CFG:g}, {part['width']}x{part['height']}")
        character = sg.upload(args.image)
        made: list[Path | None] = [None] * count
        for one in windows:
            stages.next(f"window {one['index']} of {len(windows)}")
            files = window_inputs(one, part, masks, made, work, anchored)
            frames = window(one, files, part, args, unet, loras, character, work, blocks)
            for i in range(one["skip"], one["length"]):
                source = one["start"] + i
                if one["first"] <= source <= one["last"]:
                    made[source] = frames[i]
            say(f"window {one['index']} of {len(windows)}: frames {one['first']} to {one['last']} made")
        if any(f is None for f in made):
            sys.exit("a frame of the part was not made")

        stages.next("joining the windows and laying the part's own sound under them")
        assemble([f for f in made if f is not None], part, target, work)
        stages.done()
        say(json.dumps({"output": str(target), "engine": "cuda", "model": "ref2va-swap", "frames": count, "size": [part["width"], part["height"]],
                        "windows": len(windows), "anchor": anchored is not None, "seed": args.seed, "weights": unet,
                        "stages": dict(stages.took), "total_s": round(time.time() - started, 1)}))
    finally:
        for name in sg.UPLOADED:
            (input_root() / name).unlink(missing_ok=True)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import spark_generate as sg  # noqa: E402  (ComfyUI's server, uploads, progress; imported here so swap_plan's tests need none of it)
    main()
