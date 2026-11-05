"""The Library actions. Most are built on a LoRA from the hub that is not for making a clip but has a job of its own.

    Turnaround sheet   one picture in, five views of its subject out (matlod's Turnaround LoRA, MATLOWAI's nodes)
    Upscale            a finished clip made larger (dntpi's Video Upscaler LoRA and H3 Video Upsampler node)
    Remove a person    a person taken out of a clip (Akatz Labs' Person Remover LoRA and H3 Relay node)
    Find a person      the first half of the removal alone: the mask, to look at before the long render
    HDR finish         a finished clip graded and written as BT.2020 / HLG with an SDR copy (hlg.py: ffmpeg and numpy, no model)

They run as jobs in the Studio's queue (kinds turnaround, upscale, mask, remove, hlg). On a DGX Spark each goes through its
author's own ComfyUI nodes (spark/spark_workflows.py, installed by spark/setup-workflows.sh). On a Mac the turnaround
sheet is the MLX engine's own (h3_generate.py --sheet); Upscale, Find and Remove a person run the same
spark/spark_workflows.py graphs on a ComfyUI of the Mac's own, on PyTorch's Metal device (mac/comfy.sh, installed by
scripts/setup-comfy.sh, `oneshot-setup.sh --upscale` and `--remove`), reading MiniMax H3 from the Mac's own files.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

import hlg
import windows_cuda  # noqa: E402  (the native Windows CUDA tier, beside this file)

KINDS = ("turnaround", "upscale", "mask", "remove", "hlg")
PRESETS = Path(__file__).with_name("lora-presets.json")
# the LoRA each action is built on (its id in the hub), and what the action is called on the page
LORA = {"turnaround": "turnaround", "upscale": "video-upscaler", "remove": "person-remover", "mask": None, "hlg": None}
TITLES = {"turnaround": "Turnaround sheet", "upscale": "Upscale", "remove": "Remove a person", "mask": "Find a person", "hlg": "keys-HDR LORA"}
SIZES = (512, 1024, 2048)                 # a turnaround view's side: 512 is what the LoRA was trained at
MEGAPIXELS = (1.0, 2.0, 3.0, 4.0)         # an upscale's area: 2 is about 1920x1080
WINDOWS = (22, 39, 56)                    # the remover's window: 22 is the author's proven default
# On a Mac: the node pack and the files under $MAC_COMFY_MODELS each action needs, and the option that installs them
X265 = {}                                 # whether this machine's ffmpeg has libx265: asked once, with a time limit
MAC_NODES = {"upscale": "ComfyUI-H3-Video-Upsampler", "remove": "h3-relay", "mask": "h3-relay"}
MAC_FILES = {"upscale": ["latent_upscale_models/minimax_h3_latent_upscaler_3d_bf16.safetensors",
                         "vae/minimax_h3_audio_vae_fp32.safetensors"],
             "remove": ["checkpoints/sam3.1_multiplex_fp16.safetensors", "vae/minimax_h3_audio_vae_fp32.safetensors"],
             "mask": ["checkpoints/sam3.1_multiplex_fp16.safetensors"]}
MAC_OPTION = {"upscale": "--upscale", "remove": "--remove", "mask": "--remove"}
# MiniMax's own files the Mac's ComfyUI reads in their place (mac/studio_files): Ref2VA, its text encoder and decoder
MAC_H3 = ("Ref2VA/transformer", "FL2VA/text_encoder", "FL2VA/video_vae")


def attach(home: Path, pack: Path, spark: bool, inside, relative, windows: bool | Callable[[], bool] = False):
    def bounded_windows() -> bool:
        return bool(windows() if callable(windows) else windows)
    router = APIRouter()
    prefix = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))
    models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models"))

    def recipe(lora_id: str) -> dict:
        return next((r for r in json.loads(PRESETS.read_text()) if r["id"] == lora_id), {})

    def lora_file(lora_id: str, comfy: bool = False) -> Path | None:
        """The LoRA's own file: installed in the hub (on a Mac, the copy rewritten for the MLX engine, unless the
        action runs on the Mac's ComfyUI, which reads the author's file as a Spark does), or still in a LoRA bundle on
        this machine."""

        comfy = comfy or spark
        hub = home / "loras" / (f"{lora_id}.safetensors" if comfy else f"{lora_id}.mlx.safetensors")
        if not comfy:
            return hub if hub.is_file() else None
        if hub.is_file():
            return hub
        name = recipe(lora_id).get("url", "").rsplit("/", 1)[-1]
        where = Path(os.environ.get("LORA_BUNDLE") or home / "lora-bundle").expanduser()
        if name and where.is_dir():
            return next(iter(where.glob(f"*/{lora_id}/{name}")), None)
        return None

    def missing(kind: str) -> str:
        """Why an action cannot run here, or '' when it can."""

        if kind == "hlg":
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                return "The HDR finish needs ffmpeg and ffprobe, which are not installed on this machine."
            if "ok" not in X265:
                try:
                    X265["ok"] = "libx265" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=hlg.TIMEOUT).stdout
                except (OSError, subprocess.SubprocessError):
                    return "ffmpeg did not answer: try again."
            return "" if X265["ok"] else "The HDR finish writes HEVC, and this machine's ffmpeg was built without libx265."
        if not spark:
            model = Path(os.environ.get("H3_MODEL_DIR", Path.home() / "h3-models/MiniMax-H3"))
            if not (model / "Ref2VA/transformer").is_dir():
                return "MiniMax H3 Ref2VA is not installed: run `bash oneshot-setup.sh --ref2va`."
            if kind == "turnaround":
                return "" if lora_file("turnaround") else "Install Turnaround in the LoRA hub first."
            files = Path(os.environ.get("MAC_COMFY_MODELS") or prefix / "comfy-models")
            if (not (prefix / "comfy-venv/bin/python").exists() or not (prefix / "ComfyUI/main.py").is_file()
                    or not (prefix / "ComfyUI/custom_nodes" / MAC_NODES[kind]).is_dir()
                    or any(not (files / f).is_file() for f in MAC_FILES[kind])):
                return (f"Not installed yet: run `bash oneshot-setup.sh {MAC_OPTION[kind]}` (ComfyUI in its own environment "
                        "on this Mac, the authors' node pack and their models).")
            if any(not (model / part).is_dir() for part in MAC_H3):
                return "MiniMax H3's text encoder or video decoder is missing: run `bash oneshot-setup.sh --ref2va`."
            if LORA[kind] and lora_file(LORA[kind], comfy=True) is None:
                return f"Install {recipe(LORA[kind]).get('name', LORA[kind])} in the LoRA hub first."
            return ""
        nodes = prefix / "ComfyUI" / "custom_nodes"
        need = {"turnaround": ["ComfyUI-H3-ContactSheet"], "upscale": ["ComfyUI-H3-Video-Upsampler"],
                "remove": ["h3-relay"], "mask": ["h3-relay"]}[kind]
        files = {"upscale": ["latent_upscale_models/minimax_h3_latent_upscaler_3d_bf16.safetensors"],
                 "remove": ["checkpoints/sam3.1_multiplex_fp16.safetensors"],
                 "mask": ["checkpoints/sam3.1_multiplex_fp16.safetensors"]}.get(kind, [])
        native = windows_cuda.active()
        if any(not (nodes / n).is_dir() for n in need) or any(not (models / "Workflows-Comfy" / f).is_file() for f in files):
            if native:
                return (f"Not installed yet: run `{windows_cuda.setup_hint('workflows')}` in the Studio's folder (three node "
                        "packs and 2.5 GB of models); the next render restarts ComfyUI with them.")
            return "Not installed yet: run `bash spark/setup-workflows.sh` in the pack's folder (three node packs and 2.5 GB of models), then restart ComfyUI."
        if kind != "mask" and not any((models / "MiniMax-H3-Comfy/diffusion_models" / f"minimax_h3_ref2va_pruned_{one}.safetensors").is_file()
                   for one in ("bf16", "int8_convrot")):  # either: Character Swap's install brings only the int8 file; finding a person is SAM's alone
            return ("MiniMax H3 Ref2VA is not installed: run `" + (windows_cuda.setup_hint("library-h3,h3-shared") if native else
                    "bash oneshot-setup-spark.sh --ref2va") + "`.")
        if LORA[kind] and lora_file(LORA[kind]) is None:
            return f"Install {recipe(LORA[kind]).get('name', LORA[kind])} in the LoRA hub first."
        return ""

    @router.get("/api/actions")
    def state() -> dict:
        out = {}
        for kind in KINDS:
            why = f"{TITLES[kind]} needs a verified Windows recipe." if bounded_windows() else missing(kind)
            if bounded_windows() and kind == "hlg":
                import windows_features
                why = windows_features.status(kind)["why"] or missing(kind)
            card = recipe(LORA[kind] or "person-remover") if kind != "hlg" else {}
            out[kind] = {"title": TITLES[kind], "ready": not why, "why": why, "lora": LORA[kind],
                         "lora_name": card.get("name"), "author": card.get("author")}
        out["turnaround"].update(sizes=SIZES, size=1024)
        out["upscale"].update(megapixels=MEGAPIXELS, megapixel=2.0)
        out["remove"].update(windows=WINDOWS, window=22, steps=12)
        out["hlg"].update(strength=1.0)
        if windows_cuda.native_os():
            import windows_library_checks as library
            for kind in library.KINDS:
                if kind in out:
                    found = library.usage_status(kind)
                    out[kind].update(found, ready=found["available"])
            if out["turnaround"].get("qualification_required"):
                out["turnaround"].update(sizes=(512,), size=512)
                out["upscale"].update(megapixels=(1.0,), megapixel=1.0)
        return out

    def media(params: dict, key: str, suffixes: tuple) -> Path:
        given = params.get(key)
        if not given:
            raise HTTPException(400, f"choose a {'clip' if key == 'video' else 'picture'} from the Library first")
        path = inside(given)
        if not path.is_file() or path.suffix.lower() not in suffixes:
            raise HTTPException(400, f"{given} is not a {'clip' if key == 'video' else 'picture'} in the studio folder")
        return path

    def number(params: dict, key: str, default, kind=float):
        try:
            return kind(params.get(key) if params.get(key) not in (None, "") else default)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} is a number")

    def build(kind: str, params: dict, job_id: str) -> tuple[list[str], dict, list[str]]:
        """(command, environment, output paths) for one action's job; 400 with the reason when it cannot run."""

        why = missing(kind)
        if why:
            raise HTTPException(400, why)
        if kind == "hlg":
            clip = media(params, "video", (".mp4", ".mov"))
            if bounded_windows():
                import graphics
                import windows_features
                try:
                    windows_features.preflight(kind)
                    windows_features.check_media(graphics.probe(clip))
                except (OSError, RuntimeError, ValueError) as error:
                    raise HTTPException(400, str(error)) from None
            strength = number(params, "strength", 1.0)
            if not 0.1 <= strength <= 1:
                raise HTTPException(400, "the strength is between 0.1 and 1")
            refused = hlg.refusal(clip)
            if refused:
                raise HTTPException(400, refused)
            target, outputs = home / "renders" / f"{job_id}-hlg.mp4", []
            command = [sys.executable, str(pack / "app/hlg.py"), "run", "--video", str(clip), "-o", str(target), "--strength", f"{strength:g}"]
            outputs.append(str(target))
            if params.get("sdr", True):
                plain = home / "renders" / f"{job_id}-sdr.mp4"
                command += ["--sdr", str(plain)]
                outputs.append(str(plain))
            return command, {}, outputs
        comfy = spark or kind != "turnaround"     # on a Mac the turnaround sheet is the MLX engine's
        python = str(prefix / ("venv/bin/python" if spark else "comfy-venv/bin/python"))
        script = [python, str(pack / "spark/spark_workflows.py"), kind]
        env = {"PREFIX": str(prefix)} if comfy else {}
        lora = lora_file(LORA[kind], comfy=comfy) if LORA[kind] else None
        if kind == "turnaround":
            picture = media(params, "image", (".png", ".jpg", ".jpeg", ".webp"))
            size = number(params, "size", 1024, int)
            if size not in SIZES:
                raise HTTPException(400, "a view is 512, 1024 or 2048 on a side")
            strength = number(params, "strength", 1.0)
            if not 0.3 <= strength <= 1.5:
                raise HTTPException(400, "the strength is between 0.3 and 1.5 (1.0 turns furthest, about 0.7 keeps the look closest)")
            folder = home / "scouts" / job_id
            # the same script on both machines: the MLX engine on a Mac, the author's ComfyUI nodes on a Spark
            env.update(SIZE=size, STRENGTH=f"{strength:g}", SEED=number(params, "seed", 0, int), TURNAROUND_LORA=str(lora))
            if (params.get("prompt") or "").strip():
                text = params["prompt"].strip()
                if "<Picture 1>" not in text:
                    raise HTTPException(400, "the instruction names the picture as <Picture 1>")
                env["PROMPT"] = text
            if windows_cuda.native_os():
                import windows_runtime
                if windows_runtime.mode()["enabled"]:
                    import spark_workflows
                    env.update(STEPS=28, PROMPT=spark_workflows.TURN_PROMPT)
            return (["bash", str(pack / "scripts/turnaround.sh"), str(picture), str(folder / "turn")], env,
                    [str(folder / "turn_sheet.png")] + [str(folder / f"turn_view{i}.png") for i in range(1, 6)])
        clip = media(params, "video", (".mp4", ".mov"))
        if kind == "upscale":
            area = number(params, "megapixels", 2.0)
            if area not in MEGAPIXELS:
                raise HTTPException(400, "the size is 1, 2, 3 or 4 megapixels")
            redraw = number(params, "denoise", 0.1)
            if not 0 <= redraw <= 0.4:
                raise HTTPException(400, "the re-draw is between 0 (only enlarge) and 0.4; 0.1 is the tested setting")
            target = home / "renders" / f"{job_id}.mp4"
            return script + ["--video", str(clip), "--lora", str(lora), "--megapixels", f"{area:g}", "--denoise", f"{redraw:g}",
                             "-o", str(target)], env, [str(target)]
        person = " ".join((params.get("person") or "").split())
        if not person:
            raise HTTPException(400, "say who to remove in a few words: man in a gray shirt")
        if len(person) > 200:
            raise HTTPException(400, "describe the person in a few words")
        folder = home / "masks" / job_id
        if kind == "mask":
            return script + ["--video", str(clip), "--person", person, "-o", str(folder / "person")], env, \
                [str(folder / "person_mask.mp4"), str(folder / "person_matte.mp4"), str(folder / "person_plate.png")]
        window, steps = number(params, "window", 22, int), number(params, "steps", 12, int)
        if window not in WINDOWS or not 4 <= steps <= 30:
            raise HTTPException(400, "the window is 22, 39 or 56 frames and the passes are between 4 and 30")
        target = home / "renders" / f"{job_id}.mp4"
        command = script + ["--video", str(clip), "--person", person, "--lora", str(lora), "--window", str(window),
                            "--steps", str(steps), "--seed", str(number(params, "seed", 904234, int)),
                            "--keep", str(folder / "person"), "-o", str(target)]
        if params.get("clean_frame"):
            command += ["--clean-frame", str(media(params, "clean_frame", (".png", ".jpg", ".jpeg", ".webp")))]
        if params.get("silent"):
            command.append("--silent")
        return command, env, [str(target), str(folder / "person_mask.mp4"), str(folder / "person_clean.png")]

    def title(kind: str, params: dict) -> str:
        name = Path(str(params.get("image") or params.get("video") or "")).name
        return f"{TITLES[kind]}: {name}" if name else TITLES[kind]

    def source_of(kind: str, params: dict) -> str | None:
        """The Library file an action's result came from, as the page shows it beside the result."""

        path = params.get("image") if kind == "turnaround" else params.get("video")
        return relative(inside(path)) if path else None

    build.lora_file = lora_file
    build.prerequisites = missing
    return router, build, title, source_of
