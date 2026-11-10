"""Stable Audio 3 Small SFX files and admission for the Spark's guarded ComfyUI."""
import os
import shutil
from pathlib import Path

from windows_audio3 import MODEL, TITLE, RECIPE, SECONDS, parameters
import model_catalog

SNAPSHOT = model_catalog.model(MODEL)["spark_download_snapshot"]
REPO, REVISION = SNAPSHOT["repo"], SNAPSHOT["revision"]
SIZES = model_catalog.catalogue()["file_sizes"][REPO + "@" + REVISION]["files"]
FILES = {
    name: (SIZES[name], SNAPSHOT["sha256"][name]) for name in SNAPSHOT["files"]
}
SETUP = "bash oneshot-setup-spark.sh --sound"


def prefix() -> Path:
    return Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))


def folder() -> Path:
    return Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models")) / "Sound-Comfy"


def prerequisites() -> str:
    if not shutil.which("ffmpeg"):
        return "Small SFX output levelling requires ffmpeg: install it and restart Studio."
    if not (prefix() / "venv/bin/python").is_file():
        return "Install the Spark's ComfyUI environment: " + SETUP
    comfy = prefix() / "ComfyUI"
    supported = comfy / "comfy/supported_models.py"
    if (not supported.is_file() or "class StableAudio3(" not in supported.read_text(encoding="utf-8")
            or not (comfy / "comfy/text_encoders/sa3.py").is_file()):
        return "The installed ComfyUI lacks native Stable Audio 3 support: " + SETUP
    if any(not (folder() / name).is_file() or (folder() / name).stat().st_size != size
           for name, (size, _) in FILES.items()):
        return "Stable Audio 3 Small SFX and T5Gemma are not completely installed: " + SETUP
    return ""


def status() -> dict:
    import memory_guard
    why = prerequisites()
    installed = not why
    if not why:
        found = memory_guard.sound_check(MODEL, SECONDS[0])
        if not found["fits"]:
            why = found["reason"]
    return {"installed": installed, "available": not why, "why": why,
            "state": "available" if not why else "needs_measurement" if installed else "not_installed",
            "setup": SETUP}
