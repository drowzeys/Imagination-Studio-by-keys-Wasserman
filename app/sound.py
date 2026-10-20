"""Sound effects and foley: two jobs of the studio's queue, run on this machine in an environment of their own.

  sfx    a sound effect from words: Stability AI's Stable Audio Open 1.0 (44.1 kHz stereo, up to 47 s), through
         diffusers' StableAudioPipeline. Stability AI Community License (free under 1 M USD of yearly revenue).
  foley  a clip's sound made for its picture: MMAudio (Ho Kei Cheng et al.), large_44k_v2, which watches the frames
         (CLIP and Synchformer) and the words, so steps land on the footfalls. Its weights are CC-BY-NC 4.0:
         non-commercial. The result is a WAV and the clip with it, laid under the clip's own sound (its speech
         stays; peaks under -1 dBFS) or in place of it.

On a Mac both run in $PREFIX/sound-venv (scripts/setup-sound.sh, `oneshot-setup.sh --sound`), never in the Studio's
venv, as sound_generate.py beside the pack; the weights are in $SOUND_MODELS (default $PREFIX/sound-models).
On a DGX Spark they run through the studio's ComfyUI server (spark/comfy.sh, its memory guard beside it) as
`spark/spark_generate.py sfx|foley`, with the same flags: ComfyUI's own Stable Audio nodes, and kijai's
ComfyUI-MMAudio with the Studio's sampler node (oneshot-setup-spark.sh --sound; weights in
$SPARK_MODELS/Sound-Comfy). A Spark takes a kind only when spark/memory_guard.py has its measured estimate
(sound_check), and asks it before the job is queued and again before anything loads.
The queue's job runner unloads Drama 3 and Music 3 before either starts (server.perform), so they never stand beside
a render.

Takes land in $STUDIO_HOME/sound/sfx and sound/foley with a <job>.take.json beside each, and the Library lists them
under Sound effects.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

KINDS = ("sfx", "foley")
TITLES = {"sfx": "Sound effect", "foley": "Foley"}
SFX_SECONDS = (1.0, 47.0)        # Stable Audio Open's window is 47.55 s
FOLEY_SECONDS = (1.0, 15.0)      # the studio's longest clip; MMAudio was trained on 8 s and keeps going past it
VIDEO = (".mp4", ".mov", ".m4v", ".webm")
SPARK_INSTALL = "bash oneshot-setup-spark.sh --sound"
# what each kind loads on a Spark, under $SPARK_MODELS/Sound-Comfy (spark_generate.py's SAO_* and MMAUDIO)
SPARK_FILES = {"sfx": ("checkpoints/stable_audio_open_1.0.safetensors", "text_encoders/stable_audio_open_t5_base.safetensors"),
               "foley": ("mmaudio/mmaudio_large_44k_v2_fp16.safetensors", "mmaudio/mmaudio_vae_44k_fp16.safetensors",
                         "mmaudio/mmaudio_synchformer_fp16.safetensors", "mmaudio/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
                         "mmaudio/nvidia/bigvgan_v2_44khz_128band_512x/bigvgan_generator.pt")}
LICENCES = {"sfx": "Stable Audio Open 1.0 · Stability AI Community License (free under 1 M USD yearly revenue; a larger business needs Stability's licence)",
            "foley": "MMAudio · its weights are CC-BY-NC 4.0: non-commercial use only"}


def attach(home: Path, pack: Path, spark: bool, inside, relative, room=None, seconds_of=None):
    """``room``: what this machine has for a render with none of the Studio's own engines counted (MB, or None),
    the queue's memory_of as the server keeps it; a Spark's memory check reads it. ``seconds_of``: the server's
    media_seconds (ffprobe with a time limit), so a clip foley cannot take is refused when it is queued."""

    router = APIRouter()
    prefix = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))
    models = Path(os.environ.get("SOUND_MODELS", prefix / "sound-models"))
    spark_models = Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "Sound-Comfy"

    def python() -> Path:
        if spark:
            return Path(os.environ.get("SOUND_PYTHON") or prefix / "venv/bin/python")
        return Path(os.environ.get("SOUND_PYTHON") or prefix / "sound-venv/bin/python")

    def guard(kind: str, seconds: float) -> dict:
        import memory_guard  # spark/, on the server's path
        return memory_guard.sound_check(kind, seconds, available_mb=room() if room else None)

    def spark_missing(kind: str) -> str:
        if not python().is_file() or not (prefix / "ComfyUI" / "main.py").is_file():
            return "The studio's ComfyUI is not installed on this machine: run `bash oneshot-setup-spark.sh`."
        gone = [name for name in SPARK_FILES[kind] if not (spark_models / name).is_file()]
        if kind == "foley" and not (prefix / "ComfyUI" / "custom_nodes" / "ComfyUI-MMAudio" / "nodes.py").is_file():
            gone.insert(0, "the ComfyUI-MMAudio nodes")
        if gone:
            why = (f"Stable Audio Open is not downloaded: its page on Hugging Face is gated, so accept its licence there "
                   f"(huggingface.co/stabilityai/stable-audio-open-1.0), sign in, then run `{SPARK_INSTALL}`." if kind == "sfx"
                   else f"Foley is not installed ({', '.join(Path(g).name for g in gone)}): run `{SPARK_INSTALL}`.")
            return why
        if not shutil.which("ffmpeg", path=os.environ.get("PATH", "") + ":/usr/bin:/usr/local/bin"):
            return "Sound effects are levelled, and foley laid under its clip, with ffmpeg, which is not installed: sudo apt install ffmpeg."
        found = guard(kind, 1.0)
        return found["reason"] if not found["fits"] and found.get("need_mb") is None else ""

    def missing(kind: str) -> str:
        """Why this kind cannot run here ("" when it can)."""

        if spark:
            return spark_missing(kind)
        if not python().is_file():
            return "Sound effects are not installed on this machine: run `bash scripts/setup-sound.sh` (or oneshot-setup.sh --sound)."
        if kind == "sfx" and not (models / "stable-audio-open-1.0" / "model_index.json").is_file():
            return ("Stable Audio Open is not downloaded: its page on Hugging Face is gated, so accept its licence there "
                    "(huggingface.co/stabilityai/stable-audio-open-1.0), then run `bash scripts/setup-sound.sh` again.")
        if kind == "foley" and not (models / "mmaudio" / "weights" / "mmaudio_large_44k_v2.pth").is_file():
            return "MMAudio's weights are not downloaded: run `bash scripts/setup-sound.sh` again."
        if kind == "foley" and not shutil.which("ffmpeg", path=os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"):
            return "Foley reads the clip and lays the sound under it with ffmpeg, which is not installed on this machine."
        return ""

    def number(params: dict, key: str, default, kind=float):
        value = params.get(key, default)
        if value in (None, ""):
            value = default
        if isinstance(value, bool):
            raise HTTPException(400, f"{key} is a number")
        try:
            return kind(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} is a number")

    def words(params: dict, key: str, needed: bool) -> str:
        text = " ".join(str(params.get(key) or "").split())
        if needed and not text:
            raise HTTPException(400, "say what is heard: rain on a tin roof, a door slams in a stone hall")
        if len(text) > 600:
            raise HTTPException(400, f"{key} is a sentence or two (600 characters at most)")
        return text

    def fits(kind: str, seconds: float) -> None:
        """On a Spark, the memory check before the job is queued (spark_generate.py asks again before anything loads)."""

        if spark:
            found = guard(kind, seconds)
            if not found["fits"]:
                raise HTTPException(400, found["reason"])

    def build(kind: str, params: dict, job_id: str) -> tuple[list[str], dict, list[str]]:
        """(command, environment, output paths) for one sound job; 400 with the reason when it cannot run."""

        why = missing(kind)
        if why:
            raise HTTPException(400, why)
        prompt, negative = words(params, "prompt", kind == "sfx"), words(params, "negative", False)
        seed = number(params, "seed", 1, int)
        folder = home / "sound" / kind
        folder.mkdir(parents=True, exist_ok=True)
        runner = [str(pack / "spark" / "spark_generate.py"), kind] if spark else [str(pack / "sound_generate.py"), kind, "--models", str(models)]
        command = [str(python()), *runner, "--seed", str(seed), "--take", str(folder / f"{job_id}.take.json"), "--home", str(home)]
        if prompt:
            command += ["--prompt", prompt]
        if negative:
            command += ["--negative", negative]
        if kind == "sfx":
            seconds, steps, cfg = number(params, "seconds", 6.0), number(params, "steps", 100, int), number(params, "cfg", 7.0)
            if not SFX_SECONDS[0] <= seconds <= SFX_SECONDS[1]:
                raise HTTPException(400, f"a sound effect is {SFX_SECONDS[0]:g} to {SFX_SECONDS[1]:g} seconds long")
            if not 10 <= steps <= 200 or not 1 <= cfg <= 15:
                raise HTTPException(400, "the passes are 10 to 200 (100 is the card's setting) and the guidance 1 to 15")
            fits(kind, seconds)
            target = folder / f"{job_id}.wav"
            return command + ["--seconds", f"{seconds:g}", "--steps", str(steps), "--cfg", f"{cfg:g}", "-o", str(target)], {}, [str(target)]
        clip = inside(str(params.get("video") or ""))
        if not clip.is_file() or clip.suffix.lower() not in VIDEO:
            raise HTTPException(400, "foley is made for a clip: pick one from the Library")
        steps, cfg = number(params, "steps", 25, int), number(params, "cfg", 4.5)
        if not 10 <= steps <= 100 or not 1 <= cfg <= 10:
            raise HTTPException(400, "the passes are 10 to 100 (25 is MMAudio's setting) and the guidance 1 to 10")
        under = params.get("keep_sound", True)
        if not isinstance(under, bool):
            raise HTTPException(400, "keep_sound is true (under the clip's own sound) or false (in place of it)")
        level = number(params, "foley_db", -6.0)
        if not -30 <= level <= 0:
            raise HTTPException(400, "foley_db is between -30 and 0")
        length = seconds_of(str(clip))[0] if seconds_of else None  # None when it cannot be read: the runner measures again
        if length is not None and length < FOLEY_SECONDS[0]:
            # refused here, not by the runner after the job has unloaded Voice or Music and started the sound venv
            raise HTTPException(400, f"the clip is {length:.2f} s and foley needs at least {FOLEY_SECONDS[0]:g} s of picture: pick a longer clip")
        fits(kind, FOLEY_SECONDS[1])  # the clip's length is read by the runner; the longest is asked for here
        sound, film = folder / f"{job_id}.wav", folder / f"{job_id}.mp4"
        command += ["--video", str(clip), "--steps", str(steps), "--cfg", f"{cfg:g}", "--max-seconds", f"{FOLEY_SECONDS[1]:g}",
                    "--foley-db", f"{level:g}", "-o", str(sound), "--clip-out", str(film)] + ([] if under else ["--replace"])
        return command, {}, [str(film), str(sound)]

    def title(kind: str, params: dict) -> str:
        if kind == "foley":
            name = Path(str(params.get("video") or "")).name
            return f"Foley: {name}" if name else "Foley"
        text = " ".join(str(params.get("prompt") or "").split())
        return f"Sound effect: {text[:48]}" if text else "Sound effect"

    def source_of(kind: str, params: dict) -> str | None:
        return relative(inside(params["video"])) if kind == "foley" and params.get("video") else None

    @router.get("/api/sound/state")
    def state() -> dict:
        return {"kinds": {kind: {"ready": not missing(kind), "why": missing(kind) or None, "licence": LICENCES[kind]} for kind in KINDS},
                "seconds": {"sfx": SFX_SECONDS, "foley": FOLEY_SECONDS}, "spark": spark}

    @router.get("/api/sound/takes")
    def takes() -> list[dict]:
        """The sound effects and foley made here, newest first (their take files)."""

        out = []
        for kind in KINDS:
            for file in (home / "sound" / kind).glob("*.take.json"):
                try:
                    take = json.loads(file.read_text())
                except (OSError, ValueError):
                    continue
                if isinstance(take, dict) and (home / str(take.get("path") or "")).is_file():
                    out.append({**take, "kind": kind})
        return sorted(out, key=lambda t: -float(t.get("created") or 0))[:200]

    return router, build, title, source_of
