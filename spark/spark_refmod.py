#!/usr/bin/env python3
"""Create a RefMod: pictures and clips of one subject saved as a MiniMax H3 reference (a .safetensors file) that a clip
can load again without the pictures. ComfyUI's own nodes and Luisa (luisacaotica)'s ComfyUI-MiniMaxH3Mod (MIT): its
node "Create H3 RefMod" encodes the references with the H3 video VAE alone; no diffusion model is loaded.

    spark_refmod.py --name hero --image sheet.png --image face.png -o refmods/hero.safetensors
    spark_refmod.py --name kick --video fight.mp4 --start 9.8 --seconds 5 --concept pose_motion -o refmods/kick.safetensors

Full Reference (the default) stores the VAE encode itself: the identity of a face or an outfit comes through, at a few
hundred kB to a few MB a reference. Compressed Reference pools it to a small grid and refines it (``--steps``): a few
tokens, the concept or the motion, not a face. A clip is cut to its part and brought down to ``--resolution`` on its
short side with ffmpeg first, so ComfyUI never holds a long clip at full size.

The file is written by the node into the first `refmods` folder of the studio's ComfyUI ($STUDIO_HOME/refmods, set in
its extra_model_paths.yaml) and checked; with ``--share DIR`` it is also hard-linked there (one file on the disk), so
another ComfyUI's RefMod loaders list it too.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VAE = "minimax_h3_video_vae_fp16.safetensors"  # the H3 video VAE (FastH3-Comfy/vae): fp16, not the int8 one, for the encode
NODE = "MiniMaxH3RefModExtract"
PACK = "ComfyUI-MiniMaxH3Mod"
MODES = {"full": "Full Reference", "compressed": "Compressed Reference"}
CONCEPTS = ("identity", "pose_motion", "clothing", "background", "style", "generic")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
MOST_IMAGES, MOST_VIDEOS = 16, 8  # the node's own slots
CLIP_FPS = 24


def say(text: str) -> None:
    print("[tensorfold] " + text, flush=True)


def ffmpeg(*args: str, what: str) -> None:
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"could not {what}: {done.stderr.strip()[-400:]}")


def part_of(video: str, start: float, seconds: float, short: int, work: Path, index: int) -> Path:
    """The part of a clip a RefMod is made from: from ``start``, at most ``seconds`` long, its short side at most
    ``short`` (never enlarged), 24 frames a second, no sound."""

    out = work / f"clip{index}.mp4"
    scale = f"scale='if(lt(iw,ih),min({short},iw),-2)':'if(lt(iw,ih),-2,min({short},ih))':flags=lanczos"
    ffmpeg("-ss", f"{start:.3f}", "-i", video, "-t", f"{seconds:.3f}", "-an", "-vf", f"fps={CLIP_FPS},{scale},setsar=1",
           "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(out), what=f"cut the part out of {Path(video).name}")
    return out


def workflow(args, pictures: list[str], clips: list[str]) -> dict:
    """The graph: the H3 video VAE, each picture and clip loaded, Create H3 RefMod, and its report shown."""

    w: dict = {"1": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}}
    create = {"name": args.name, "mode": MODES[args.mode], "concept_type": args.concept, "vae": ["1", 0],
              "ref_resolution": args.resolution, "pool_h": args.grid, "pool_w": args.grid,
              "latent_frames": args.frames, "identity": args.steps, "merge": args.merge, "motion_only": args.motion_only,
              "multiplier": 1, "max_tokens": args.max_tokens, "extraction_preset": "manual", "subfolder": args.subfolder,
              "description": args.description, "save": True, "budget_policy": "truncate", "background_retention": 0.0}
    for i, name in enumerate(pictures, 1):
        w[f"img{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        create[f"refs_image.ref_image_{i}"] = [f"img{i}", 0]
    for i, name in enumerate(clips, 1):
        w[f"vid{i}"] = {"class_type": "LoadVideo", "inputs": {"file": name}}
        w[f"frames{i}"] = {"class_type": "GetVideoComponents", "inputs": {"video": [f"vid{i}", 0]}}
        create[f"refs_video.ref_video_{i}"] = [f"frames{i}", 0]
    w["9"] = {"class_type": NODE, "inputs": create}
    w["10"] = {"class_type": "PreviewAny", "inputs": {"source": ["9", 1]}}
    return w


def main() -> None:
    # a cancelled job is stopped with SIGTERM (server.cancel): leave through the cleanup below
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", required=True, help="the RefMod's name: letters, digits, space, _ . -")
    parser.add_argument("--image", action="append", default=[], help=f"a picture of the subject (up to {MOST_IMAGES})")
    parser.add_argument("--video", action="append", default=[], help=f"a clip of the subject (up to {MOST_VIDEOS})")
    parser.add_argument("--start", type=float, action="append", default=[], help="where each clip's part starts, in seconds")
    parser.add_argument("--seconds", type=float, default=5.0, help="the longest part of a clip used, in seconds")
    parser.add_argument("--mode", choices=tuple(MODES), default="full")
    parser.add_argument("--concept", choices=CONCEPTS, default="identity")
    parser.add_argument("--description", default="")
    parser.add_argument("--subfolder", default="")
    parser.add_argument("--resolution", type=int, default=0, help="short side the references are encoded at (256 to 2048); "
                                                                   "0: 1024 for pictures alone, 512 with a clip")
    parser.add_argument("--frames", type=int, default=0, help="the node's latent_frames: the most frames of a clip kept; "
                                                               "0: every frame of the part, so the token cap (--max-tokens) "
                                                               "spreads what it keeps over the whole move, not 13 frames "
                                                               "sampled first")
    parser.add_argument("--grid", type=int, default=16, help="Compressed Reference: the pooled grid's longer side")
    parser.add_argument("--steps", type=int, default=500, help="Compressed Reference: refinement steps (0: pooling alone)")
    parser.add_argument("--merge", action="store_true", help="Compressed Reference: one shared grid for all the references")
    parser.add_argument("--motion-only", action="store_true", help="Compressed Reference, clips (experimental): the "
                        "differences between frames are encoded, so the RefMod carries how things move, not what they look like")
    parser.add_argument("--max-tokens", type=int, default=5120, help="the most reference tokens the RefMod injects (0: no cap)")
    parser.add_argument("--work", required=True, help="a folder of the job's own, removed at the end")
    parser.add_argument("--share", default="", help="a second refmods folder the file is hard-linked into")
    parser.add_argument("-o", "--output", required=True, help="where the node writes the file (checked)")
    args = parser.parse_args()

    if not NAME.match(args.name) or ".." in args.name:
        sys.exit("the name is 1 to 80 letters, digits, spaces, _ . or -, starting with a letter or a digit")
    if not args.image and not args.video:
        sys.exit("a RefMod needs at least one picture or clip")
    if len(args.image) > MOST_IMAGES or len(args.video) > MOST_VIDEOS:
        sys.exit(f"at most {MOST_IMAGES} pictures and {MOST_VIDEOS} clips")
    if args.subfolder and not re.match(r"^[A-Za-z0-9_-]{1,40}$", args.subfolder):
        sys.exit("the subfolder is one folder name: letters, digits, _ or -")
    if not args.resolution:
        args.resolution = 512 if args.video else 1024
    if args.motion_only and (args.mode != "compressed" or not args.video):
        sys.exit("motion only is for clips, in Compressed Reference (the node's training mode)")
    if not args.frames:
        args.frames = int(args.seconds * CLIP_FPS) + 1 if args.video else 1
    if not 256 <= args.resolution <= 2048 or args.resolution % 64:
        sys.exit("the resolution is a multiple of 64 from 256 to 2048")
    if not 1 <= args.seconds <= 30:
        sys.exit("a clip's part is 1 to 30 seconds")
    for given in args.image + args.video:
        if not Path(given).is_file():
            sys.exit(f"no such file: {given}")
    target = Path(args.output)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    import spark_generate as sg

    started = time.time()
    try:
        say(f"stage 1/3 preparing {len(args.image)} picture(s) and {len(args.video)} clip(s)")
        starts = args.start + [0.0] * (len(args.video) - len(args.start))
        clips = [part_of(v, starts[i], args.seconds, args.resolution, work, i) for i, v in enumerate(args.video)]
        sg.ensure_server("refmod")
        info = sg.call("/object_info/" + NODE)
        if NODE not in info:
            sys.exit(f"the studio's ComfyUI has no {PACK} node pack: run `bash oneshot-setup-cuda.sh --refmod`")
        pictures = [sg.upload(p) for p in args.image]
        uploaded = [sg.upload(str(c)) for c in clips]
        say(f"stage 2/3 encoding the references with the H3 video VAE ({MODES[args.mode]}, {args.resolution} px short side)")
        before = target.stat().st_mtime if target.exists() else 0.0
        prompt_id = asyncio.run(sg.run(workflow(args, pictures, uploaded), "9"))
        record = sg.history_of(prompt_id)
        if record["status"].get("status_str") != "success":
            errors = [m[1] for m in record["status"].get("messages", []) if m[0] == "execution_error"]
            detail = errors[0].get("exception_message", "").strip() if errors else json.dumps(record["status"])[:600]
            sys.exit(f"ComfyUI failed: {detail} (log: {sg.side('log')})")
        text = (record.get("outputs", {}).get("10") or {}).get("text") or [""]
        details = text[0] if isinstance(text, list) else str(text)
        for line in details.splitlines():
            if line.strip():
                say("refmod: " + line.strip())
        say("stage 3/3 checking the saved file")
        if not target.is_file() or target.stat().st_mtime <= before:
            sys.exit(f"the node did not write {target}: is $STUDIO_HOME/refmods the first refmods folder in the studio's "
                     "ComfyUI extra_model_paths.yaml?")
        shared = ""
        if args.share:
            folder = Path(args.share).expanduser() / args.subfolder if args.subfolder else Path(args.share).expanduser()
            try:
                folder.mkdir(parents=True, exist_ok=True)
                link = folder / target.name
                link.unlink(missing_ok=True)
                os.link(target, link)  # one file on the disk, listed by both ComfyUIs
                shared = str(link)
            except OSError as error:  # another disk: a copy instead
                try:
                    shutil.copy2(target, folder / target.name)
                    shared = str(folder / target.name)
                except OSError:
                    say(f"not shared to {folder}: {error}")
        say(json.dumps({"output": str(target), "refmod": args.name, "mode": args.mode, "motion_only": args.motion_only, "concept": args.concept,
                        "pictures": len(args.image), "clips": len(args.video), "resolution": args.resolution,
                        "bytes": target.stat().st_size, "shared": shared, "total_s": round(time.time() - started, 1)}))
    finally:
        sg.forget_uploads()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
