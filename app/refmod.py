"""Create a RefMod, a Library action: pictures and clips of one subject (a character sheet, a few stills, a move in a
clip) saved as a MiniMax H3 reference, a .safetensors file in $STUDIO_HOME/refmods that a clip can load again without
them. One job of the studio's queue (kind ``refmod``) runs spark/spark_refmod.py on the studio's ComfyUI with Luisa
(luisacaotica)'s ComfyUI-MiniMaxH3Mod (MIT, spark/setup-workflows.sh): the H3 video VAE encodes the references; no
diffusion model is loaded, so it is a light job (seconds to a minute).

Full Reference stores the encode itself (a face, an outfit); Compressed Reference pools it to a small grid (a concept or
a motion in a few tokens, not a face). A second refmods folder named in refmods/share.txt (another ComfyUI's
models/refmods) gets a hard link to each new file, so that ComfyUI's RefMod loaders list it too.
"""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

from fastapi import APIRouter, HTTPException

KINDS = ("refmod",)
TITLE = "Create a RefMod"
NODE_PACK = "ComfyUI-MiniMaxH3Mod"
VAE = "FastH3-Comfy/vae/minimax_h3_video_vae_fp16.safetensors"
MODES = ("full", "compressed")
CONCEPTS = ("identity", "pose_motion", "clothing", "background", "style", "generic")
PICTURES = (".png", ".jpg", ".jpeg", ".webp")
CLIPS = (".mp4", ".mov")
MOST_IMAGES, MOST_VIDEOS = 16, 8
MOST_IN_CLIP = 8  # Load H3 RefMods' slots: the RefMods one clip loads (spark_generate.REFMOD_LIMIT)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
SECONDS = (1.0, 30.0)
DESCRIPTION_MOST = 400
LICENCE = ("RefMod nodes: Luisa (luisacaotica)'s ComfyUI-MiniMaxH3Mod, MIT. The references are encoded with the MiniMax H3 "
           "video VAE (MiniMax H3 Community License).")


def header_of(path: Path) -> dict:
    """A RefMod's own metadata (the safetensors header's __metadata__), without reading its tensors."""

    try:
        with open(path, "rb") as f:
            size = struct.unpack("<Q", f.read(8))[0]
            if size > 16 * 1024 * 1024:
                return {}
            meta = json.loads(f.read(size)).get("__metadata__") or {}
    except (OSError, ValueError, struct.error):
        return {}
    try:  # the pack keeps its own fields as one JSON string (refmod_meta, format 4)
        inner = json.loads(meta.get("refmod_meta") or "{}")
    except ValueError:
        inner = {}
    if isinstance(inner, dict) and inner:
        meta = {**meta, **inner}
        h, w, t = inner.get("latent_h"), inner.get("latent_w"), inner.get("latent_t")
        if all(isinstance(n, int) for n in (h, w, t)):
            meta.setdefault("tokens", (h // 2) * (w // 2) * t)  # the DiT's 2x2 patches (the pack's own count)
    return meta


def attach(home: Path, pack: Path, spark: bool, inside, relative, media_seconds):
    router = APIRouter()
    prefix = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))
    models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models"))
    folder = home / "refmods"

    def share() -> str:
        """The second refmods folder (refmods/share.txt's first line), when it is there."""

        try:
            given = (folder / "share.txt").read_text().splitlines()[0].strip()
        except (OSError, IndexError):
            return ""
        return given if given and Path(given).expanduser().is_dir() else ""

    def missing() -> str:
        if not spark:
            return "Create a RefMod runs on the studio's ComfyUI (a DGX Spark or an NVIDIA card), not on a Mac."
        if not (prefix / "ComfyUI/custom_nodes" / NODE_PACK / "nodes.py").is_file():
            return f"The RefMod nodes ({NODE_PACK}) are not installed: run `bash spark/setup-workflows.sh --no-models`."
        if not (models / VAE).is_file():
            return "The MiniMax H3 video VAE (fp16) is not installed: run `bash oneshot-setup-cuda.sh`."
        return ""

    def saved() -> list[dict]:
        out = []
        for path in sorted(folder.rglob("*.safetensors"), key=lambda p: p.stat().st_mtime, reverse=True):
            meta = header_of(path)
            if meta.get("kind") not in ("image", "video", "audio"):  # another kind of .safetensors in the folder
                continue
            out.append({"name": path.relative_to(folder).with_suffix("").as_posix(), "path": relative(path),
                        "bytes": path.stat().st_size, "made": path.stat().st_mtime, "kind": meta.get("kind", ""),
                        "concept": meta.get("concept_type", ""), "mode": meta.get("mode", ""),
                        "description": meta.get("description", ""), "tokens": meta.get("tokens") or ""})
        return out

    @router.get("/api/refmod")
    def state() -> dict:
        why = missing()
        return {"title": TITLE, "ready": not why, "why": why, "modes": list(MODES), "concepts": list(CONCEPTS),
                "most_images": MOST_IMAGES, "most_videos": MOST_VIDEOS, "seconds": list(SECONDS),
                "folder": str(folder), "share": share(), "saved": saved(), "licence": LICENCE}

    def media(given, suffixes: tuple, what: str) -> Path:
        if not given or not isinstance(given, str):
            raise HTTPException(400, f"choose {what} from the Library")
        path = inside(given)
        if not path.is_file() or path.suffix.lower() not in suffixes:
            raise HTTPException(400, f"{given} is not {what} in the studio folder")
        return path

    def settings(params: dict) -> dict:
        name = " ".join(str(params.get("name") or "").split())
        if not NAME.match(name) or ".." in name:
            raise HTTPException(400, "name the RefMod: 1 to 80 letters, digits, spaces, _ . or -, starting with a letter or a digit")
        images, videos = params.get("images") or [], params.get("videos") or []
        if not isinstance(images, list) or not isinstance(videos, list):
            raise HTTPException(400, "images and videos are lists of Library paths")
        if not images and not videos:
            raise HTTPException(400, "choose at least one picture or clip of the subject")
        if len(images) > MOST_IMAGES or len(videos) > MOST_VIDEOS:
            raise HTTPException(400, f"at most {MOST_IMAGES} pictures and {MOST_VIDEOS} clips in one RefMod")
        pictures = [media(p, PICTURES, "a picture") for p in images]
        clips = [media(v, CLIPS, "a clip") for v in videos]
        starts = params.get("starts") or []
        if not isinstance(starts, list) or len(starts) > len(clips):
            raise HTTPException(400, "starts is one number a clip")
        try:
            starts = [max(0.0, float(s or 0)) for s in starts] + [0.0] * (len(clips) - len(starts))
            seconds = float(params.get("seconds") or 5)
        except (TypeError, ValueError):
            raise HTTPException(400, "a clip's start and length are numbers")
        if not SECONDS[0] <= seconds <= SECONDS[1]:
            raise HTTPException(400, f"a clip's part is {SECONDS[0]:g} to {SECONDS[1]:g} seconds")
        for clip, start in zip(clips, starts):
            length, _ = media_seconds(str(clip))
            if length is not None and start >= length - 0.5:
                raise HTTPException(400, f"{clip.name} is {length:.1f} s long: its part cannot start at {start:g} s")
        mode = params.get("mode") or "full"
        if mode not in MODES:
            raise HTTPException(400, "the mode is full or compressed")
        concept = params.get("concept") or "identity"
        if concept not in CONCEPTS:
            raise HTTPException(400, "the subject is one of " + ", ".join(CONCEPTS))
        motion_only = params.get("motion_only", False)
        if not isinstance(motion_only, bool):
            raise HTTPException(400, "motion_only is true or false")
        if motion_only:
            if not clips:
                raise HTTPException(400, "motion only is for clips: a picture has no motion")
            mode = "compressed"  # the node keeps motion alone only in its pooled (training) mode
        description = " ".join(str(params.get("description") or "").split())
        if len(description) > DESCRIPTION_MOST:
            raise HTTPException(400, f"the description is at most {DESCRIPTION_MOST} characters")
        target = folder / f"{name}.safetensors"
        if target.exists() and params.get("replace") is not True:
            raise HTTPException(400, f"a RefMod named {name} is already saved: choose another name, or replace it")
        return {"name": name, "pictures": pictures, "clips": clips, "starts": starts, "seconds": seconds, "mode": mode,
                "concept": concept, "description": description, "target": target, "motion_only": motion_only}

    def build(kind: str, params: dict, job_id: str) -> tuple[list[str], dict, list[str]]:
        if kind not in KINDS:
            raise HTTPException(400, f"unknown job kind {kind}")
        why = missing()
        if why:
            raise HTTPException(400, why)
        chosen = settings(params)
        command = [str(prefix / "venv/bin/python"), str(pack / "spark/spark_refmod.py"), "--name", chosen["name"],
                   "--mode", chosen["mode"], "--concept", chosen["concept"], "--seconds", f"{chosen['seconds']:g}",
                   "--work", str(home / "renders" / f".refmod-{job_id}"), "-o", str(chosen["target"])]
        for picture in chosen["pictures"]:
            command += ["--image", str(picture)]
        for clip, start in zip(chosen["clips"], chosen["starts"]):
            command += ["--video", str(clip), "--start", f"{start:g}"]
        if chosen["motion_only"]:
            command.append("--motion-only")
        if chosen["description"]:
            command.append("--description=" + chosen["description"])  # one argument, even when the words start with "-"
        if share():
            command += ["--share", share()]
        env = {"PREFIX": str(prefix), "SPARK_MODELS": str(models)}
        return command, env, [str(chosen["target"])]

    def title(params: dict) -> str:
        name = " ".join(str(params.get("name") or "").split())
        return f"{TITLE}: {name}" if name else TITLE

    def source_of(params: dict) -> str | None:
        first = (params.get("images") or params.get("videos") or [None])[0]
        try:
            return relative(inside(first)) if isinstance(first, str) else None
        except HTTPException:
            return None

    return router, build, title, source_of, saved
