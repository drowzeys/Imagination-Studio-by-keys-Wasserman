"""Progressive adapters for the existing bounded Windows image, sound and graphics checks."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import graphics
import windows_features as features
import windows_sound
import windows_storyboard

KINDS = ("scout", "sfx", "graphics")
GPU_KINDS = ("scout", "sfx")
SETUP = {"scout": "setup-windows-images.ps1 -Model auto", "sfx": "setup-windows-sfx.ps1",
         "graphics": "setup-windows-graphics.ps1"}


def gpu_python() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "gpu-venv" / "Scripts" / "python.exe"


def receipt_path(kind: str) -> Path:
    if kind == "scout":
        return windows_storyboard.folder() / "qualification.json"
    if kind == "sfx":
        return windows_sound.folder() / "qualification.json"
    if kind == "graphics":
        return graphics.install_dir() / "windows-checked.json"
    raise ValueError("unknown baseline check")


def prerequisites(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("unknown baseline check")
    if why := features.prerequisites("hlg"):
        return why
    if kind == "graphics":
        found = graphics.ready()
        if (not found["node"] or found["hyperframes"] != graphics.HYPERFRAMES or not found["browser"]
                or not found["gsap"] or not found["ffmpeg"] or graphics.windows_probe_error()):
            return found["why"] or "Install the pinned Windows graphics runtime."
        return ""
    if not gpu_python().is_file() or not (gpu_python().parent.parent.parent / "ComfyUI" / "main.py").is_file():
        return "Install the Windows GPU environment and pinned ComfyUI first."
    route = windows_storyboard if kind == "scout" else windows_sound
    files = windows_storyboard.FILES if kind == "scout" else windows_sound.windows_sfx.FILES
    try:
        if any((route.folder() / name).stat().st_size != size for name, (size, _) in files.items()):
            return "The model files are incomplete; rerun its setup."
        if not (route.folder() / "LICENSE.md").is_file():
            return "The model licence file is missing; rerun its setup."
    except FileNotFoundError:
        return "The model files are missing; run its setup."
    return ""


def status(kind: str) -> dict:
    try:
        if why := prerequisites(kind):
            return {"available": False, "qualified": False, "installed": False, "state": "not_installed", "why": why}
        native = (windows_storyboard.status() if kind == "scout" else windows_sound.status() if kind == "sfx"
                  else graphics.ready())
        qualified = native.get("qualified", native["ok"]) if kind == "graphics" else native["available"]
        return {"available": qualified, "qualified": qualified, "installed": True,
                "state": "qualified" if qualified else "not_qualified", "why": native.get("why") or "",
                "runtime_updated": native.get("runtime_updated", False), "hardware": native.get("hardware", {})}
    except (OSError, ValueError, RuntimeError) as error:
        return {"available": False, "qualified": False, "installed": False, "state": "check_unavailable", "why": str(error)}


def evidence(kind: str, qualified: bool) -> dict:
    path = receipt_path(kind)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"matches": False, "results": []}
    native = json.loads(raw)
    if not isinstance(native, dict):
        raise ValueError("The saved baseline check is not an object.")
    if kind == "graphics":
        results = [{"case": row["template"], "elapsed_s": row["elapsed_s"],
                    "peak_job_commit_mb": row["peak_commit_mb"]} for row in native["results"]]
    else:
        result = native["result"]
        calibration = result["calibration"]
        results = [{"case": f"{calibration['count']} 512x512 storyboard drafts" if kind == "scout" else "Five-second sound effect",
                    "peak_job_commit_mb": result["job_commitment"]["peak_job_commit_bytes"] / 2 ** 20,
                    "peak_process_commit_mb": result["job_commitment"]["peak_process_commit_bytes"] / 2 ** 20,
                    "elapsed_s": calibration.get("request_to_files_seconds"),
                    "rendered": result.get("ok") is True,
                    "measurements": result.get("memory_guard", {})}]
    digest = hashlib.sha256(raw).hexdigest()
    saved = features.saved(kind, "")
    matching = saved.get("native_receipt_sha256") == digest
    return {"matches": qualified, "ok": native.get("result", {}).get("ok") is True if kind != "graphics" else native.get("ok") is True,
            "results": results, "native_receipt_sha256": digest,
            "hardware": native.get("hardware", {}),
            "checked_at": saved["checked_at"] if matching else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "time_basis": "check" if matching else "receipt_mtime"}


def command(kind: str, output: Path) -> list[str]:
    if kind == "graphics":
        return [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-graphics-check.py")]
    python = str(gpu_python())
    if kind == "scout":
        return [python, "-X", "utf8", str(features.PACK / "app" / "windows_storyboard.py"),
                "--qualify", "--count", "6", "--output", str(output)]
    if kind == "sfx":
        return [python, "-X", "utf8", str(features.PACK / "app" / "windows_sound.py"),
                "--qualify", "--output", str(output)]
    raise ValueError("unknown baseline check")
