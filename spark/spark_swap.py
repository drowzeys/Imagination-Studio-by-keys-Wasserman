#!/usr/bin/env python3
"""Character Swap on a DGX Spark (CUDA): the person in a part of a clip replaced by the character in one picture, with
the scene, the performance and the clip's own sound kept. ComfyUI's own nodes and this file; no node pack.

    spark_swap.py --video clip.mp4 --image character.png --start 2 --seconds 5 --quality 768 -o swapped.mp4
    spark_swap.py ... --no-anchor --direction "she keeps her hands in her pockets" --seed 7 --work folder
    spark_swap.py --video clip.mp4 --start 2 --seconds 5 --who people.png          # number the people, and stop
    spark_swap.py --video clip.mp4 ... --swaps '[{"image": "a.png", "which": 1}, {"image": "b.png", "which": 3}]'

The steps, each said as `[tensorfold] stage i/n ...` for the studio's job card:

1. the part      the 5 to 15 s part is cut out and prepared at 24 frames a second, `--quality` pixels on its short side
                 (480 or 768, multiples of 32, the clip's own shape), with its sound beside it. A part with a hard cut
                 in it is refused: the swap is for one continuous shot (app/shot.py finds the cut).
2. the person    SAM 3.1 looks for "person" in the part's first frame (two refinement passes), or for the few words
                 of `--person` ("man in a blue jacket") when the shot has several people. The largest one it is at
                 least 75% sure of is the one swapped; a first frame with nobody in it is refused (PERSON).
3. following     SAM 3.1 tracks that person through the part (detection 0.5, one object). A frame where the tracker
                 lost the person takes the mask of the nearest frame that has it, and every mask is grown by a
                 margin of the person's own size on that frame (about a third of their width, at most 64 pixels),
                 so hair, hands and the outfit's edges are inside it.
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

Several people (`--swaps`, at most four; `--which` for one picked by number): `--who` follows everyone SAM 3.1 finds
through the whole part (text-prompted tracking, so a person who comes into view later is found too; at most nine)
and writes the frame with the most of them in view, each tinted and numbered in the order SAM 3.1 found them (a
second and third frame under it for people who are not in the first). Each swap names its person by that number (or
by a few words, for someone in the first frame) and has its own picture, direction and seed. Every chosen person is
followed through the clip's own frames first; then the swaps run one after another, each on the result of the one
before. A swap of this kind is drawn in a crop of the frame around its person (one box for the whole part, drawn at
no more pixels than the canvas has: swap_plan.crop) and only the person's grown mask is pasted back, with a soft
edge, so everything else stays the clip's own pixels however many swaps there are. Frames where the
person is not in view are left as they are, and a person who is not in the first frame gets no anchor. The chain is the one of Noncoder's MiniMax H3 Multi Swap workflow;
the crop and the paste are the studio's own. One person with neither `--swaps` nor `--which` is made as before, on
the whole frame.

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
# what installs Ref2VA's int8 weights, the two LoRAs and SAM 3.1: natively on Windows the CUDA tier's setup
SETUP = ("powershell -ExecutionPolicy Bypass -File scripts\\setup-windows-cuda.ps1 -SkipToolchain -Parts swap"
         if sys.platform == "win32" else "bash oneshot-setup-spark.sh --swap")

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
EMPTY = 64  # pixels: a mask smaller than this is a frame where the tracker lost the person
# What counts as a person in the first frame. Measured 2026-10-27 with SAM 3.1 on synthetic 864x480 frames: pictures
# with nobody in them (an empty kitchen, beach, street, forest, a coat on a rack, an ffmpeg gradient, a Mandelbrot,
# one flat colour) scored 0.31 to 0.67, people (a close-up, from behind, sitting, walking, a waiter at the back of a
# cafe, a hiker 20x38 pixels in a desert) 0.93 to 0.99. A gradient and a flat colour came back as one rectangle the
# size of the frame. So: at least PERSON sure, at least SMALLEST of the frame, and not a near-perfect rectangle over
# most of it (FLAT: that share of the frame, that share of its box filled; a close-up's face filled 0.61 of its box).
PERSON, SMALLEST, FLAT = 0.75, 0.0005, (0.9, 0.95)
SOUND_RATE = 48000
# a person picked by number (--which, --swaps): the tints of the who's-who picture and the mask preview, the soft edge
# (pixels) their crop is pasted back with, and how much of the smaller of two masks may be shared before they are
# taken for one person. Choices, not measurements.
COLOURS = ((40, 220, 90), (255, 170, 40), (70, 150, 255), (235, 80, 200), (240, 230, 60), (60, 220, 220), (250, 90, 70),
           (160, 110, 255), (255, 255, 255))
FEATHER, SAME = 4, 0.5
# the margin a numbered person's mask is grown by, on each frame: MARGIN of the square root of the mask's pixels (about
# a third of the person's width), at least LEAST and at most GROW. A person far from the camera keeps a border, not a
# blanket over their neighbours: a fixed 64 pixels was wider than a dancer 60 pixels across in a wide shot, and the
# swap drew over the people beside her. A close-up reaches the 64 either way. Every swap grows its mask this way. WHO_SCALE: the size the who's-who masks are read at (they only place and tint).
MARGIN, LEAST, WHO_SCALE, WHO_FRAMES = 0.3, 12, 0.5, 3
# <Video 1>, the part as the model reads its performance, at half the canvas (448x256 for 864x480, 672x384 for
# 1344x768): its rows ride through every pass. Measured on a DGX Spark, 124 frames: at 864x480 21 to 23 s a pass
# against 31.5 with it at 384 and 35 at the canvas's own size; at 1344x768 65 s against 146. The person's moves and
# timing were followed as closely. SWAP_REF_SIDE sets its short side in pixels instead (0: the canvas's size).
REF_SIDE = os.environ.get("SWAP_REF_SIDE", "half")
# The character's picture as the model reads it (MiniMaxH3ReferenceToVideo's ref_image_size): "match" scales it down
# to the canvas's pixel area, which is what was measured on a DGX Spark; "max" keeps it up to 2048 pixels on its short
# side, ComfyUI's "best identity fidelity" and the setting of Noncoder's Multi Swap workflow. At "match" a large
# reference sheet (a full figure beside a close-up) reaches the model at about the canvas's size, the face a few
# dozen pixels across. "max" adds the picture's rows to every pass: not measured on a Spark, so the app offers it
# only where its guard warns instead of refusing (--ref-size, SWAP_REF_IMAGE).
REF_IMAGE = os.environ.get("SWAP_REF_IMAGE", "match")


def ticking(what: str, frames: list):
    """The frames of a step that asks nothing of ComfyUI (the mask preview, a crop, the paste), said as they are done:
    without it the job's card stands at the step before for as long as 600 frames take."""

    say(what)
    for index, frame in enumerate(frames):
        if index % 24 == 0 or index == len(frames) - 1:
            say(f"frame {index + 1}/{len(frames)}")
        yield index, frame


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
    try:  # a longer part where the app's guard warns (SWAP_LONGEST, app/swap.py), never past what is tried
        high = max(high, min(plan.LONGEST_TRIED, float(os.environ.get("SWAP_LONGEST") or 0)))
    except ValueError:
        pass
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
    # a hand or an arm crossing a still picture measures like a cut on the first two figures (a person talking to a
    # locked camera: 39 of them in two clips); a cut changes most of the frame (shot.CUT_CHANGED, measured there)
    crossed = [one for one in found if one["changed"] < shot.CUT_CHANGED]
    found = [one for one in found if one["changed"] >= shot.CUT_CHANGED]
    if crossed:
        say(f"{len(crossed)} fast move{'s' if len(crossed) > 1 else ''} in the part measured like a cut but left most of the "
            f"picture as it was (at most {max(one['changed'] for one in crossed):.0%} of it changed): taken as one shot")
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


def sam_nodes(words: str = "") -> dict:
    """SAM 3.1 and what it looks for: "person", or the user's few words for one of several people."""

    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SAM}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": words.strip() or "person"}}}


def anchor_edit(words: str = "") -> str:
    """The anchor's edit: the measured wording, naming the person in the user's words when they chose one."""

    words = " ".join(words.split())
    words = words[4:] if words.lower().startswith("the ") else words
    return ANCHOR_EDIT.replace("the person", "the " + words, 1) if words else ANCHOR_EDIT


# ---------------------------------------------------------------- 2. the person

def people(found: list[dict], areas: list[int], width: int, height: int) -> list[tuple[int, int, float]]:
    """(index, mask pixels, score) of the detections that are people (PERSON, SMALLEST, FLAT), largest first.
    ``found`` are SAM 3.1's boxes in its order ({"score", "width", "height", ...}), ``areas`` each one's mask pixels."""

    frame, out = width * height, []
    for index, (box, area) in enumerate(zip(found, areas)):
        score = float(box.get("score") or 0)
        boxed = max(1.0, float(box.get("width") or 0) * float(box.get("height") or 0))
        if score < PERSON or area < max(EMPTY, SMALLEST * frame):
            continue
        if area >= FLAT[0] * frame and area >= FLAT[1] * boxed:
            continue
        out.append((index, area, score))
    return sorted(out, key=lambda one: -one[1])


def said_text(prompt_id: str, node: str) -> str:
    """The text a finished workflow's PreviewAny node showed."""

    record = sg.history_of(prompt_id)
    if record["status"].get("status_str") != "success":
        errors = [m[1] for m in record["status"].get("messages", []) if m[0] == "execution_error"]
        detail = errors[0].get("exception_message", "").strip() if errors else json.dumps(record["status"])[:600]
        sys.exit(f"ComfyUI failed: {detail} (log: {sg.side('log')})")
    text = (record.get("outputs", {}).get(node) or {}).get("text") or [""]
    return text[0] if isinstance(text, list) else str(text)


def detect(part: dict, work: Path, words: str = "") -> list[dict]:
    """The people SAM 3.1 finds in the first frame, largest first; exits when there is none. Each: ``mask`` (a mask
    picture), ``area`` (its pixels), ``score``, and where it is (``x``, the mask's middle, and ``top``, its highest
    row). Its boxes and scores are read first, so a frame with nothing in it never reaches the nodes that cannot take
    an empty mask. With ``words`` it looks for that person ("man in a blue jacket")."""

    import numpy as np
    from PIL import Image

    words = " ".join(words.split())
    nobody = ("no person was found in the part's first frame: Character Swap replaces a person who is in the first frame. "
              "Start the part where the person is in view")
    if words:
        nobody = (f"nobody matching \"{words}\" was found in the part's first frame: describe the person differently "
                  "(clothes, position), or start the part where they are in view")
    w = sam_nodes(words)
    w["3"] = {"class_type": "LoadImage", "inputs": {"image": sg.upload(str(part["frames"][0]))}}
    w["4"] = {"class_type": "SAM3_Detect", "inputs": {"model": ["1", 0], "image": ["3", 0], "conditioning": ["2", 0],
                                                      "threshold": DETECT, "refine_iterations": REFINE, "individual_masks": True}}
    w["7"] = {"class_type": "PreviewAny", "inputs": {"source": ["4", 1]}}
    try:
        found = json.loads(said_text(run_graph(w, "4"), "7") or "[]")
        found = [one for one in (found[0] if found and isinstance(found[0], list) else found) if isinstance(one, dict)]
    except (ValueError, IndexError, TypeError):
        found = []
    if not any(float(one.get("score") or 0) >= PERSON for one in found):
        best = max((float(one.get("score") or 0) for one in found), default=0.0)
        sys.exit(nobody + (f" (SAM 3.1's likeliest guess was {best:.0%} sure, under the {PERSON:.0%} a person needs)" if best else ""))
    w["5"] = {"class_type": "MaskToImage", "inputs": {"mask": ["4", 0]}}
    w["6"] = {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": f"studio/swap_find_{uuid.uuid4().hex[:8]}"}}
    masks = fetch_all(collect(run_graph(w, "4"), "6"), work / f"found_{uuid.uuid4().hex[:6]}")
    on = [np.asarray(Image.open(path).convert("L")) >= 128 for path in masks]
    ranked = people(found, [int(m.sum()) for m in on], part["width"], part["height"])
    if not ranked:
        sys.exit(nobody)
    out = []
    for index, area, score in ranked:
        rows, columns = np.nonzero(on[index])
        out.append({"mask": masks[index], "area": area, "score": score, "x": float(columns.mean()), "top": int(rows.min())})
    return out


def find_person(part: dict, work: Path, words: str = "") -> Path:
    """The largest person SAM 3.1 finds in the first frame, as a mask picture; exits when there is none.
    With ``words`` it looks for that person ("man in a blue jacket") and the largest match is the one."""

    words = " ".join(words.split())
    found = detect(part, work, words)
    say(f"{len(found)} {'person' if len(found) == 1 else 'people'} " + (f"matching \"{words}\" " if words else "")
        + "in the first frame; the largest "
        f"({found[0]['area'] * 100 / (part['width'] * part['height']):.0f}% of the frame, {found[0]['score']:.0%} sure) is swapped")
    chosen = work / "person.png"
    shutil.copy(found[0]["mask"], chosen)
    return chosen


def everyone(part: dict, work: Path, wanted: list[int], scale: float = 1.0) -> dict:
    """The people SAM 3.1 finds and follows through the whole part from the word "person" alone, whenever they come
    into view (at most plan.MOST_PEOPLE, in the order it finds them: object 0 is person 1). Returns, for each object
    of ``wanted``, its mask on every frame (bool arrays, at ``scale`` of the canvas; all empty for an object that
    does not exist). The same part and quality give the same objects: the numbers of --who are the ones a swap reads."""

    import numpy as np
    from PIL import Image

    clip = work / "source.mp4"
    if not clip.is_file():
        lossless(str(work / "source" / "%05d.png"), clip)
    w = sam_nodes()
    w["3"] = {"class_type": "LoadVideo", "inputs": {"file": put(clip)}}
    w["4"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["3", 0]}}
    w["7"] = {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["4", 0], "model": ["1", 0], "conditioning": ["2", 0],
                                                          "detection_threshold": TRACK, "max_objects": plan.MOST_PEOPLE,
                                                          "detect_interval": 1}}
    tag, saved = uuid.uuid4().hex[:8], {}
    for n, index in enumerate(wanted):
        base = 100 + 10 * n
        w[str(base)] = {"class_type": "SAM3_TrackToMask", "inputs": {"track_data": ["7", 0], "object_indices": str(index)}}
        w[str(base + 1)] = {"class_type": "MaskToImage", "inputs": {"mask": [str(base), 0]}}
        last = [str(base + 1), 0]
        if scale != 1.0:
            w[str(base + 2)] = {"class_type": "ImageScaleBy", "inputs": {"image": last, "upscale_method": "nearest-exact", "scale_by": scale}}
            last = [str(base + 2), 0]
        w[str(base + 3)] = {"class_type": "SaveImage", "inputs": {"images": last, "filename_prefix": f"studio/swap_all_{tag}_{index}"}}
        saved[index] = str(base + 3)
    prompt = run_graph(w, "7", {"7": "frame"})
    out = {}
    for index, node in saved.items():
        masks = [np.asarray(Image.open(path).convert("L")) >= 128 for path in fetch_all(collect(prompt, node), work / f"everyone{index}")]
        if not masks:
            sys.exit("SAM 3.1 returned no masks for the part")
        while len(masks) < part["count"]:
            masks.append(masks[-1])
        out[index] = masks[:part["count"]]
        shutil.rmtree(work / f"everyone{index}", ignore_errors=True)
    forget_upload(clip)
    return out


def mend(masks: list, least: int = EMPTY) -> tuple[list, int]:
    """A followed person's masks with the frames the tracker lost them on, between their first and last frame in
    view, given the nearest frame's mask; before they come into view and after they leave, the masks stay empty.
    Returns the masks and how many frames were filled."""

    have = [i for i, m in enumerate(masks) if m.sum() >= least]
    out, filled = [m if i in set(have) else m & False for i, m in enumerate(masks)], 0
    for i in range(have[0], have[-1] + 1) if have else ():
        if masks[i].sum() < least:
            out[i] = masks[min(have, key=lambda j: (abs(j - i), j > i))]
            filled += 1
    return out, filled


def whos_who(part: dict, people: dict, target: Path, scale: float = WHO_SCALE) -> list[int]:
    """The picture a swap's numbers are read from: the frame with the most people in view, each tinted and numbered
    (object 0 is person 1), and under it up to two more frames for people who are not in that one. ``people``: each
    object's masks on every frame (everyone). Returns the numbers shown."""

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    least = max(4, int(EMPTY * scale * scale))
    seen = {index: np.array([m.sum() >= least for m in masks]) for index, masks in people.items()}
    seen = {index: there for index, there in seen.items() if there.any()}
    if not seen:
        sys.exit("no person was found in the part: Character Swap replaces a person. Choose a part with the person in view")
    left, frames = set(seen), []
    while left and len(frames) < WHO_FRAMES:  # the frame with the most people not shown yet, the earliest of equals
        counts = sum(seen[index].astype(int) for index in left)
        frames.append(int(counts.argmax()))
        left -= {index for index in left if seen[index][frames[-1]]}
    size = max(14, part["height"] // 14)
    try:
        font, small = ImageFont.load_default(size), ImageFont.load_default(max(10, size // 2))
    except TypeError:  # an older Pillow: its one small font
        font = small = ImageFont.load_default()
    sheet = Image.new("RGB", (part["width"], part["height"] * len(frames)))
    for row, at in enumerate(frames):
        rgb = np.asarray(Image.open(part["frames"][at]).convert("RGB")).astype(np.float32)
        shown = []
        for index in sorted(seen):
            if not seen[index][at]:
                continue
            on = np.asarray(Image.fromarray(people[index][at]).resize((part["width"], part["height"]), Image.Resampling.NEAREST)).astype(bool)
            tint = COLOURS[index % len(COLOURS)]
            rgb[on] = rgb[on] * 0.45 + np.array(tint, np.float32) * 0.55
            rows, columns = np.nonzero(on)
            shown.append((index, float(columns.mean()), int(rows.min()), tint))
        picture = Image.fromarray(rgb.astype(np.uint8))
        draw = ImageDraw.Draw(picture)
        for index, x, top, tint in shown:
            text, reach = str(index + 1), size * 0.75
            x = min(max(reach, x), part["width"] - reach)
            y = min(max(reach, top + reach), part["height"] - reach)
            draw.ellipse((x - reach, y - reach, x + reach, y + reach), fill=tint, outline=(0, 0, 0), width=2)
            a, b, c, d = draw.textbbox((0, 0), text, font=font)
            draw.text((x - (a + c) / 2, y - (b + d) / 2), text, fill=(0, 0, 0), font=font)
        words = f"{at / plan.FPS:.1f} s into the part"
        a, b, c, d = draw.textbbox((0, 0), words, font=small)
        draw.rectangle((4, part["height"] - (d - b) - 12, c - a + 14, part["height"] - 4), fill=(0, 0, 0))
        draw.text((9 - a, part["height"] - (d - b) - 8 - b), words, fill=(255, 255, 255), font=small)
        sheet.paste(picture, (0, row * part["height"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    return [index + 1 for index in sorted(seen)]


def follow_all(part: dict, work: Path, swaps: list[dict]) -> list[list]:
    """Each swap's person on every frame of the part, as tracked (not grown): the one with its number (``which``, as
    --who numbers them: found and followed through the whole part, in view from the first frame or not), the largest
    match of its words in the first frame (``person``), or the largest person there. Two swaps of the same person
    are refused."""

    numbers = sorted({one["which"] for one in swaps if one["which"]})
    found = everyone(part, work, [n - 1 for n in numbers]) if numbers else {}
    tracks = []
    for index, one in enumerate(swaps, 1):
        if one["which"]:
            masks, filled = mend(found[one["which"] - 1])
            there = [i for i, m in enumerate(masks) if m.any()]
            if not there:
                sys.exit(f"swap {index}: there is no person {one['which']} in this part: number the people again for this part and quality")
            say(f"swap {index}: person {one['which']}, in view from {there[0] / plan.FPS:.1f} s to {there[-1] / plan.FPS:.1f} s of the part"
                + (f"; the tracker lost them on {filled} frames, which take the nearest frame's mask" if filled else ""))
        else:
            first = detect(part, work, one["person"])[0]
            say(f"swap {index}: the largest person" + (f" matching \"{one['person']}\"" if one["person"] else "")
                + f" in the first frame ({first['area'] * 100 / (part['width'] * part['height']):.0f}% of the frame, {first['score']:.0%} sure)")
            chosen = work / f"person{index}.png"
            shutil.copy(first["mask"], chosen)
            masks = track(part, chosen, work, one["person"])
        tracks.append(masks)
    for a in range(len(tracks)):
        for b in range(a + 1, len(tracks)):
            shared = sum(int((m & n).sum()) for m, n in zip(tracks[a][::12], tracks[b][::12]))
            if shared > SAME * min(sum(int(m.sum()) for m in tracks[a][::12]), sum(int(m.sum()) for m in tracks[b][::12])):
                sys.exit(f"swaps {a + 1} and {b + 1} name the same person: each person is swapped once")
    return tracks


# ---------------------------------------------------------------- 3. following

def follow_person(part: dict, chosen: Path, work: Path, words: str = "") -> list:
    """The person's mask on every frame of the part: tracked, gaps filled, grown by a margin of the person's own
    size on that frame (grow_to_size; SWAP_GROW=fixed grows every frame by GROW pixels, as the swap was first
    measured). A list of bool arrays."""

    masks = track(part, chosen, work, words)
    return grow(masks) if os.environ.get("SWAP_GROW") == "fixed" else grow_to_size(masks)


def grow_to_size(masks: list) -> list:
    """Every mask grown by a margin that follows the person's size on that frame (MARGIN, LEAST, at most GROW)."""

    import math

    import numpy as np
    import torch

    out = []
    for mask in masks:
        area = int(mask.sum())
        if not area:
            out.append(mask)
            continue
        reach = int(min(GROW, max(LEAST, round(MARGIN * math.sqrt(area)))))
        one = torch.from_numpy(np.asarray(mask, np.float32))[None, None]
        one = torch.nn.functional.max_pool2d(one, (1, 2 * reach + 1), stride=1, padding=(0, reach))
        one = torch.nn.functional.max_pool2d(one, (2 * reach + 1, 1), stride=1, padding=(reach, 0))
        out.append(one[0, 0].numpy() > 0.5)
    return out


def apart(tracks: list[list]) -> list[list]:
    """Each followed person's masks grown by a margin of their own size (grow_to_size), less the other followed
    people themselves: where two stand close, one's swap does not draw over the other."""

    out = []
    for index, own in enumerate(tracks):
        grown = grow_to_size(own)
        for other in (t for n, t in enumerate(tracks) if n != index):
            grown = [g & ~o for g, o in zip(grown, other)]
        out.append(grown)
    return out


def track(part: dict, chosen: Path, work: Path, words: str = "") -> list:
    """The person's mask on every frame of the part as SAM 3.1 tracked it, gaps filled. A list of bool arrays."""

    import numpy as np
    from PIL import Image

    clip = work / "source.mp4"
    if not clip.is_file():
        lossless(str(work / "source" / "%05d.png"), clip)
    w = sam_nodes(words)
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
    return masks


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


def preview(part: dict, masks: list, target: Path, others: tuple = ()) -> None:
    """The part with the person to be swapped tinted green: what the job card shows beside the result. ``others``:
    the masks of the further people of a job with several swaps, each in its own tint (COLOURS)."""

    import numpy as np
    from PIL import Image

    folder = target.parent / (target.stem + "_frames")
    folder.mkdir(parents=True, exist_ok=True)
    for index, frame in ticking("writing the picture of who is swapped", part["frames"]):
        rgb = np.asarray(Image.open(frame).convert("RGB")).astype(np.float32)
        for tint, track_ in zip(COLOURS, (masks, *others)):
            mask = track_[index]
            rgb[mask] = rgb[mask] * 0.45 + np.array(tint, np.float32) * 0.55
        Image.fromarray(rgb.astype(np.uint8)).save(folder / f"{index + 1:05d}.png", compress_level=1)
    ffmpeg("-framerate", str(plan.FPS), "-i", str(folder / "%05d.png"), "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target), what="write the mask preview")
    shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- a person picked by number: the crop and the paste

def crop_part(part: dict, now: list[Path], masks: list, work: Path) -> tuple[dict, list, tuple]:
    """The part as one swap draws it: ``now`` (the part's frames as they stand) cut to the box that holds the
    person's masks on every frame and scaled to the size swap_plan.crop gives. Returns the part of that size, the
    masks on it, and the box (x, y, w, h) on the canvas."""

    import numpy as np
    from PIL import Image

    everywhere = np.zeros((part["height"], part["width"]), bool)
    for mask in masks:
        everywhere |= mask
    rows, columns = np.nonzero(everywhere)
    found = plan.crop(int(columns.min()), int(rows.min()), int(columns.max()) + 1, int(rows.max()) + 1, part["width"], part["height"])
    (x, y, w, h), size = found["box"], found["size"]
    if (w, h) == (part["width"], part["height"]) and size == (w, h):  # the person fills the frame: nothing to cut
        return {**part, "frames": list(now)}, list(masks), found["box"]
    folder, frames, small = work / "source", [], []
    folder.mkdir(parents=True, exist_ok=True)
    for index, (frame, mask) in ticking("cutting the crop around the person", list(zip(now, masks))):
        frames.append(folder / f"{index + 1:05d}.png")
        with Image.open(frame) as picture:
            picture.convert("RGB").crop((x, y, x + w, y + h)).resize(size, Image.Resampling.LANCZOS).save(frames[-1], compress_level=1)
        small.append(np.asarray(Image.fromarray(mask[y:y + h, x:x + w]).resize(size, Image.Resampling.NEAREST)).astype(bool))
    return {**part, "width": size[0], "height": size[1], "frames": frames}, small, found["box"]


def paste(now: list[Path], made: list[Path], masks: list, box: tuple, work: Path) -> list[Path]:
    """The frames of the part with one swap in them: ``made`` (the crop, drawn) scaled back to its box and laid on
    ``now`` through the person's mask with a soft edge (FEATHER). Outside the mask a frame is as it was."""

    import numpy as np
    from PIL import Image, ImageFilter

    x, y, w, h = box
    folder, out = work / "pasted", []
    folder.mkdir(parents=True, exist_ok=True)
    for index, (frame, drawn, mask) in ticking("laying the person back into the part's frames", list(zip(now, made, masks))):
        with Image.open(frame) as picture:
            base = picture.convert("RGB")
        with Image.open(drawn) as picture:
            new = picture.convert("RGB")
        if new.size != (w, h):
            new = new.resize((w, h), Image.Resampling.LANCZOS)
        edge = Image.fromarray((mask[y:y + h, x:x + w] * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(FEATHER))
        base.paste(new, (x, y), edge)
        out.append(folder / f"{index + 1:05d}.png")
        base.save(out[-1], compress_level=1)
    return out


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
                   args.image, "--prompt", anchor_edit(getattr(args, "person", "")), "--seed", str(args.seed), "-o", str(target)]
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
    own = os.environ.get("SPARK_TRANSFORMER", "").strip()
    if own not in ("", "regular"):
        # another build of MiniMax H3 Ref2VA of the user's own choosing (a 4-bit one, a hybrid): not measured here
        found = next((one for one in (own, f"H3/{own}") if one in have), None)
        if found is None:
            sys.exit(f"ComfyUI does not list the video model {own}")
        say(f"video model: {found} (a file of your own choosing: not measured here)")
        return found
    asked = os.environ.get("SWAP_WEIGHTS", "int8")
    order = [asked] + [w for w in ("int8", "bf16") if w != asked]
    for one in order:
        if sg.REF2VA.get(one) in have:
            return sg.REF2VA[one]
    sys.exit("MiniMax H3 Ref2VA is not installed: " + SETUP)


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
            sg.link_file(Path(path), f / f"{i + 1:05d}.png")
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


def text_encoder() -> str:
    """The H3 text encoder a window loads: the regular file, or, on an install that kept another build in its place
    (a small card's NVFP4 one, docs/small-cards.md), the one a clip uses by default there. ComfyUI turns a graph away
    for a file it does not list."""

    try:
        have = sg.call("/object_info/CLIPLoader")["CLIPLoader"]["input"]["required"]["clip_name"][0]
        other = sg.default_text_encoder()
    except (AttributeError, KeyError, IndexError, TypeError):
        return sg.H3_TEXT
    if sg.H3_TEXT in have:
        return sg.H3_TEXT
    return next((one for one in (other, f"H3/{other}") if other != "regular" and one in have), sg.H3_TEXT)


def window_graph(one: dict, files: dict, part: dict, args, unet: str, loras: list[tuple[str, float]], character: str,
                 blocks: str | None = None) -> dict:
    prompt = PROMPT + (" " + ANCHOR_SAYS if files["anchor"] else "") + (" " + args.direction.strip() if args.direction.strip() else "")
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["1", 0], "shift_video": SHIFT_VIDEO, "shift_audio": SHIFT_AUDIO}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder(), "type": "minimax", "device": "default"}},
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
                          "height": part["height"], "length": one["length"], "ref_image_size": getattr(args, "ref_size", "") or REF_IMAGE,
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
        sg.link_file(Path(frame), folder / f"{index + 1:05d}.png")
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
        if len(other) == 2 and other[1] == sg.comfy_pid():
            sg.fresh_server("an action's node pack changed this server's H3 layout")
    sg.ensure_server(kind)


def need(nodes: tuple[str, ...]) -> None:
    have = sg.call("/object_info")
    missing = [n for n in nodes if n not in have]
    if missing:
        sys.exit(f"this ComfyUI has no {', '.join(missing)} node: run `bash oneshot-setup-spark.sh --upgrade` (ComfyUI's pinned version has them)")


def asked(args) -> list[dict]:
    """The swaps of this run, in the order they are made: one from --image (--which, --person, --direction, --seed),
    or the list of --swaps. Each: image, which (0: not by number), person, direction, seed."""

    if args.swaps:
        try:
            given = json.loads(args.swaps)
        except ValueError:
            sys.exit("--swaps is a JSON list, one entry a person")
        if not isinstance(given, list) or not all(isinstance(one, dict) for one in given) or not 1 <= len(given) <= plan.MOST_SWAPS:
            sys.exit(f"--swaps is a list of 1 to {plan.MOST_SWAPS} people")
    else:
        given = [{"image": args.image, "which": args.which, "person": args.person, "direction": args.direction}]
    out = []
    for index, one in enumerate(given, 1):
        try:
            which = int(one.get("which") or 0)
            seed = int(args.seed if one.get("seed") is None else one["seed"])
        except (TypeError, ValueError):
            sys.exit(f"swap {index}: the person's number and the seed are whole numbers")
        if not 0 <= which <= plan.MOST_PEOPLE:
            sys.exit(f"swap {index}: a person's number is 1 to {plan.MOST_PEOPLE}")
        image = str(one.get("image") or "")
        if not Path(image).is_file():
            sys.exit(f"no character picture at {image}" if image else f"swap {index} has no character picture")
        out.append({"image": image, "which": which, "person": "" if which else " ".join(str(one.get("person") or "").split()),
                    "direction": " ".join(str(one.get("direction") or "").split()), "seed": seed})
    return out


def main() -> None:
    # a cancelled job is stopped with SIGTERM (server.cancel): leave through the cleanup below, so the work folder's
    # frames and the pictures handed to ComfyUI do not stay behind
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--video", required=True)
    parser.add_argument("--image", default="", help="the character, outfit included")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--quality", type=int, choices=plan.QUALITIES, default=768)
    parser.add_argument("--no-anchor", dest="anchor", action="store_false")
    parser.add_argument("--direction", default="")
    parser.add_argument("--person", default="", help="a few words for the person to swap when the shot has several "
                                                     "(man in a blue jacket); default: the largest person")
    parser.add_argument("--which", type=int, default=0, help="the person to swap by their number in the --who picture; "
                                                             "drawn in a crop around them")
    parser.add_argument("--swaps", default="", help="several people, one after another: a JSON list of at most four "
                                                    "{image, which or person, direction, seed}, in place of --image")
    parser.add_argument("--who", default="", help="write the part's first frame with every person numbered here, and stop")
    parser.add_argument("--seed", type=int, default=904239)
    parser.add_argument("--ref-size", choices=("match", "max"), default=REF_IMAGE if REF_IMAGE in ("match", "max") else "match",
                        help="the character's picture as the model reads it: scaled down to the canvas's pixel area "
                             "(match), or up to 2048 pixels on its short side (max: best likeness, more rows a pass)")
    parser.add_argument("--swap-lora", default="", help="Akatz Labs' Character Swap LoRA (h3_character_swap_v1.5)")
    parser.add_argument("--turbo-lora", default="", help="LightX2V's 8-step Turbo LoRA for MiniMax H3")
    parser.add_argument("--work", default="", help="a folder for the frames (default: beside the output, removed at the end)")
    parser.add_argument("--keep", action="store_true", help="keep the work folder")
    parser.add_argument("--mask-preview", default="", help="also write the part with the person tinted green here")
    parser.add_argument("--anchor-out", default="", help="also keep the anchor picture here")
    parser.add_argument("--masks-only", action="store_true", help="find and follow the person, write --mask-preview, and stop (a quick look)")
    parser.add_argument("-o", "--output", default="")
    args = parser.parse_args()
    if not Path(args.video).is_file():
        sys.exit(f"no clip at {args.video}")
    if args.who:
        return who(args)
    if not args.output:
        sys.exit("-o names the swapped part")
    if args.swaps and (args.image or args.which or args.person or args.direction):
        sys.exit("--swaps takes the place of --image, --which, --person and --direction")
    swaps = asked(args)
    for path, what in ((args.swap_lora, "Character Swap LoRA"), (args.turbo_lora, "Turbo LoRA")):
        if not Path(path).is_file():
            sys.exit(f"no {what} at {path}")
    # a person picked by number, or several people: each drawn in a crop around them and pasted back by their mask
    cropped = bool(args.swaps or args.which)
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
    several = len(swaps) > 1
    names = ["the part", "the people" if several else "the person", "following"]
    for index in range(1, len(swaps) + 1):
        who_ = f"person {index} of {len(swaps)}: " if several else ""
        names += ([who_ + "the anchor"] if args.anchor else []) + [f"{who_}window {w['index']} of {len(windows)}" for w in windows]
    stages = Stages(names + ["assembly"])
    try:
        stages.next("cutting the part out and checking it is one shot")
        part = prepare(args, work)
        refuse_cuts(part)
        say(f"the part: {count} frames ({count / plan.FPS:.2f} s) at {part['width']}x{part['height']}, "
            f"{len(windows)} window{'s' if len(windows) > 1 else ''}" + ("" if part["sound"] else ", no sound of its own")
            + (f", {len(swaps)} people, one after another" if several else ""))

        stages.next("finding the people in the first frame" if several else "finding the person in the first frame")
        server("swap")
        need(("SAM3_Detect", "SAM3_VideoTrack", "SAM3_TrackToMask", "PreviewAny", "MiniMaxH3ReferenceToVideo", "MiniMaxH3AddGuide",
              "LTXVConcatAVLatent", "LTXVSeparateAVLatent", "SetLatentNoiseMask"))
        if SAM not in sg.call("/object_info/CheckpointLoaderSimple")["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]:
            sys.exit("SAM 3.1 is not installed: " + SETUP)
        chosen = None if cropped else find_person(part, work, args.person)

        stages.next("following the people through the part" if several else "following the person through the part")
        # every person is followed on the clip's own frames, before any of them is changed
        if cropped:
            tracks = apart(follow_all(part, work, swaps))
        else:
            tracks = [follow_person(part, chosen, work, args.person)]
        if args.mask_preview:
            preview(part, tracks[0], Path(args.mask_preview), tuple(tracks[1:]))
        if args.masks_only:
            stages.done()
            say(json.dumps({"output": args.mask_preview, "masks_only": True, "stages": dict(stages.took), "total_s": round(time.time() - started, 1)}))
            return

        now: list = list(part["frames"])  # the part's frames as they stand: each swap is made on the one before
        unet = blocks = loras = None
        anchors = 0
        for index, (one, masks) in enumerate(zip(swaps, tracks), 1):
            who_ = f"person {index} of {len(swaps)}: " if several else ""
            mine = argparse.Namespace(image=one["image"], person=one["person"], direction=one["direction"], seed=one["seed"],
                                      ref_size=args.ref_size)
            folder = work / f"swap{index}" if cropped else work
            folder.mkdir(parents=True, exist_ok=True)
            if cropped:
                own, small, box = crop_part(part, now, masks, folder)
                say(f"{who_}drawn in a crop of {box[2]}x{box[3]} at {box[0]},{box[1]}, at {own['width']}x{own['height']}"
                    if own["frames"] != now else f"{who_}drawn on the whole frame: the person's box fills it")
            else:
                own, small, box = part, masks, None

            anchored = None
            if args.anchor:
                stages.next(who_ + "putting the character into the first frame")
                if cropped and not small[0].any():  # the picture model would have nobody to replace
                    say(f"{who_}not in view in the first frame: no anchor, the swap starts where they come into view")
                else:
                    anchored = anchor(mine, own, folder, small[0])
                if anchored is not None:
                    anchors += 1
                    if args.anchor_out and index == 1:
                        shutil.copy(anchored, args.anchor_out)

            # SAM and MiniMax H3 share one server kind: a swap after a swap finds H3 still loaded (only the anchor's
            # picture model, a kind of its own, unloads it)
            server("swap")
            if loras is None:
                unet, blocks = weights(), engine_blocks()
                loras = [sg.hub_loras([f"{args.turbo_lora}:1"])[0], sg.hub_loras([f"{args.swap_lora}:1"])[0]]
                for name, _ in loras:
                    if not sg.installed("lora", name):
                        sys.exit(f"ComfyUI does not list the LoRA {name}")
                say(f"engine: cuda ({'TensorFold int8 blocks in ComfyUI, the LoRAs merged into them' if blocks else 'ComfyUI'}, MiniMax H3 Ref2VA "
                    f"{(blocks or unet).split('pruned_')[1].split('.')[0] if 'pruned_' in (blocks or unet) else (blocks or unet)}), the Character Swap and "
                    f"8-step Turbo LoRAs at 1, {STEPS} passes of {SAMPLER}, CFG {CFG:g}, {own['width']}x{own['height']}"
                    + (", the character's picture at full size" if args.ref_size == "max" else ""))
            character = sg.upload(one["image"])
            made: list[Path | None] = [None] * count
            for window_ in windows:
                stages.next(f"{who_}window {window_['index']} of {len(windows)}")
                drawn = range(window_["start"] + window_["held"], min(count, window_["start"] + window_["length"]))
                if cropped and not any(small[i].any() for i in drawn):
                    # nothing to draw: the frames stay as they are, and the model is not run for them
                    say(f"{who_}window {window_['index']} of {len(windows)}: the person is not in view, its frames stay as they are")
                    for source in range(window_["first"], window_["last"] + 1):
                        made[source] = own["frames"][source]
                    continue
                files = window_inputs(window_, own, small, made, folder, anchored)
                frames = window(window_, files, own, mine, unet, loras, character, folder, blocks)
                for i in range(window_["skip"], window_["length"]):
                    source = window_["start"] + i
                    if window_["first"] <= source <= window_["last"]:
                        made[source] = frames[i]
                say(f"{who_}window {window_['index']} of {len(windows)}: frames {window_['first']} to {window_['last']} made")
            if any(f is None for f in made):
                sys.exit("a frame of the part was not made")
            if cropped:
                now = paste(now, made, masks, box, folder)
                for spent in [folder / "source", *folder.glob("window*")]:  # this swap's frames are in the pasted ones now
                    shutil.rmtree(spent, ignore_errors=True)
                if index > 1:
                    shutil.rmtree(work / f"swap{index - 1}", ignore_errors=True)
            else:
                now = made

        stages.next("joining the windows and laying the part's own sound under them")
        assemble(now, part, target, work)
        stages.done()
        say(json.dumps({"output": str(target), "engine": "cuda", "model": "ref2va-swap", "frames": count, "size": [part["width"], part["height"]],
                        "windows": len(windows), "anchor": anchors > 0, "seed": swaps[0]["seed"], "weights": unet, "swaps": len(swaps),
                        "cropped": cropped, "stages": dict(stages.took), "total_s": round(time.time() - started, 1)}))
    finally:
        for name in sg.UPLOADED:
            (input_root() / name).unlink(missing_ok=True)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


def who(args) -> None:
    """--who: everyone SAM 3.1 finds and follows through the part, tinted and numbered on the frame with the most of
    them in view, and nothing else. The numbers are the ones --which and --swaps take, for the same part and quality."""

    target = Path(args.who)
    work = Path(args.work) if args.work else target.parent / f".swap-{target.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    started = time.time()
    stages = Stages(["the part", "the people"])
    try:
        stages.next("cutting the part out")
        part = prepare(args, work)
        stages.next("finding and following the people through the part")
        server("swap")
        need(("SAM3_VideoTrack", "SAM3_TrackToMask", "ImageScaleBy"))
        if SAM not in sg.call("/object_info/CheckpointLoaderSimple")["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]:
            sys.exit("SAM 3.1 is not installed: " + SETUP)
        shown = whos_who(part, everyone(part, work, list(range(plan.MOST_PEOPLE)), WHO_SCALE), target)
        stages.done()
        say(f"{len(shown)} {'person' if len(shown) == 1 else 'people'} followed through the part"
            + (" (the most it numbers: there may be more)" if len(shown) == plan.MOST_PEOPLE else ""))
        say(json.dumps({"output": str(target), "who": True, "people": len(shown), "size": [part["width"], part["height"]],
                        "stages": dict(stages.took), "total_s": round(time.time() - started, 1)}))
    finally:
        for name in sg.UPLOADED:
            (input_root() / name).unlink(missing_ok=True)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import spark_generate as sg  # noqa: E402  (ComfyUI's server, uploads, progress; imported here so swap_plan's tests need none of it)
    main()
