"""Explicit native H3 video and Bolt recipe checks; installation is not verification.

With qualification gating on, a native recipe runs only after its own check passed here. H3 video and the Bolt brain
had no check, so the gate could only refuse them. These are theirs, on the pattern of windows_audio_checks.py: one
small fixed recipe, run through the production path under the memory observer, and a receipt that names the engine
files, the weights, the hardware and the recipe it covers. Nothing here runs by itself: a person asks for a check."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from feature_checks import matches_qualification
import windows_audio_checks as audio
import windows_cuda
import windows_features as features
import windows_image_checks as native
import windows_runtime
from windows_qualification import hardware_profile, verify_model_updates, version_family

KINDS = ("video", "bolt")
TITLES = {"video": "H3 / FastH3 video", "bolt": "NVIDIA Nemotron 3.5 Bolt"}
CONTRACT = 1
TIMEOUT_S = 1800
VIDEO_PROMPT = ("A middle-aged baker in a cream shirt and green apron stands behind a wooden counter in a small bakery at "
                "dawn, warm window light from the left. He lifts a loaf with both hands and sets it down on the counter.")
BOLT_QUESTION = "In one short sentence, what is a clapperboard used for?"
# the plain clip the check renders, as server.build makes it for {"preset": "small", "quality": "fh8", "frames": 124}
VIDEO_ENV = {"SEED": "7", "FRAMES": "124", "WIDTH": "864", "HEIGHT": "480", "ENGINE": "fasth3", "STEPS": "8"}


def recipe(kind: str) -> dict:
    if kind == "video":
        return {"mode": "text to video", "engine": "fasth3", "width": 864, "height": 480, "steps": 8, "frames": 124,
                "fps": 24, "image": False, "last_frame": False, "references": False, "loras": False}
    if kind == "bolt":
        return {"question": BOLT_QUESTION, "max_tokens": 48, "streams": 1, "tools": False, "vision": False}
    raise ValueError("Choose H3 video or Bolt.")


def scope_label(kind: str) -> str:
    recipe(kind)
    if kind == "video":
        return ("One FastH3 clip from words: 864x480, 8 passes, up to 5 seconds (124 frames) at 24 fps, with sound. "
                "No starting picture, last frame, references or LoRAs. This checks execution, not the picture's quality.")
    return ("The engine loads, answers one short question and unloads. This checks execution, not the quality of an "
            "answer, long contexts, tools or pictures.")


def bolt_engine():
    import bolt
    one = bolt.engine()
    if isinstance(one, bolt.External):
        raise ValueError("Bolt is served by another program here: there is no Studio-managed engine to check.")
    return one


def files(kind: str) -> tuple[list[Path], Path, list[Path]]:
    """(the engine's own files, the model folder, the weights it loads) for this recipe."""
    paths = windows_cuda.paths()
    root = windows_runtime.model_root()
    if kind == "video":
        import spark_generate as generation
        shared = root / "FastH3-Comfy"
        transformers = [shared / "diffusion_models" / name for name in generation.FASTH3.values()
                        if (shared / "diffusion_models" / name).is_file()]
        weights = [shared / "text_encoders" / generation.H3_TEXT, shared / "vae" / generation.H3_VAE,
                   shared / "vae" / generation.H3_AUDIO_VAE,
                   *(transformers or [shared / "diffusion_models" / generation.FASTH3["bf16"]])]
        return [Path(paths["tf_h3"])], shared, weights
    if kind == "bolt":
        one = bolt_engine()
        folders = [one.model] + ([one.draft] if one.drafts else [])
        weights = [path for folder in folders for path in sorted(Path(folder).glob("*"))
                   if path.is_file() and path.suffix in (".safetensors", ".json")]
        return [Path(one.bin), Path(one.lib) / "bolt.dll"], Path(one.model), weights or [Path(one.model) / "config.json"]
    raise ValueError("Choose H3 video or Bolt.")


def prerequisites(kind: str) -> str:
    recipe(kind)
    if why := windows_cuda.configuration_error():
        return why
    if kind == "video":
        if why := windows_cuda.h3_why_not():
            return why
        if missing := windows_runtime.video_missing({}):
            return "H3 model files are missing: " + ", ".join(missing)
        if not (windows_cuda.root() / "ComfyUI" / "extra_model_paths.yaml").is_file():
            return "Run the native setup to configure ComfyUI's model folders."
    else:
        try:
            if why := bolt_engine().present():
                return why
        except ValueError as error:
            return str(error)
    engine, _, weights = files(kind)
    missing = [path.name for path in engine + weights if not path.is_file() or path.stat().st_size == 0]
    if missing:
        return f"Missing native {TITLES[kind]} files: " + ", ".join(missing)
    if kind == "video":
        import shutil
        import windows_media
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool, path=windows_media.search_path(os.environ.get("PATH", ""))):
                return "Install the native media tools with scripts\\setup-windows-ffmpeg.ps1."
    return ""


def fingerprint(kind: str) -> dict:
    engine, model, weights = files(kind)
    mapping = {str(path.resolve()): {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} for path in weights}
    sources = (("app/windows_engine_checks.py", "spark/spark_generate.py", "spark/windows_scripts.py") if kind == "video"
               else ("app/windows_engine_checks.py", "app/bolt.py"))
    guards = (("SPARK_COMFY_ARGS", "SPARK_VAE", "SPARK_MEMORY_FLOOR_MB", "PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_ALLOC_CONF") if kind == "video"
              else ("BOLT_GUARD_GB", "BOLT_PARALLEL", "BOLT_CONTEXT", "BOLT_DRAFTS", "BOLT_VISION", "BOLT_THINKING"))
    return {"contract": CONTRACT, "kind": kind, "recipe": recipe(kind),
            "hardware": {**hardware_profile(), "device": hashlib.sha256(native.device_uuid().encode()).hexdigest()},
            "python_abi": version_family(sys.version, 2), "models": mapping,
            "engine": {path.name: audio.digest(path.resolve(), path.stat().st_size, path.stat().st_mtime_ns) for path in engine},
            "model_root": hashlib.sha256(str(model.resolve()).encode()).hexdigest(),
            "sources": {name: hashlib.sha256(features.PACK.joinpath(*name.split("/")).read_bytes()).hexdigest() for name in sources},
            "guard": {key: os.environ.get(key, "") for key in guards}}


def compatibility(value: dict) -> dict:
    return {**{key: value[key] for key in ("contract", "kind", "recipe", "hardware", "python_abi", "engine", "model_root", "guard")},
            "model_sizes": {name: row["size"] for name, row in value["models"].items()}}


def status(kind: str) -> dict:
    base = {"installed": False, "qualified": False, "available": False, "check_available": True,
            "state": "not_installed", "verified_recipe": recipe(kind)}
    try:
        if why := prerequisites(kind):
            return {**base, "why": why}
        base.update(installed=True, state="needs_verification")
        receipt = features.saved(kind, "")
        if receipt.get("ok") is not True:
            return {**base, "why": f"Run the {TITLES[kind]} check in Machine > Feature checks."}
        current = fingerprint(kind)
        recorded = receipt["fingerprint"]
        if not matches_qualification(recorded, current, compatibility(recorded), compatibility(current)):
            return {**base, "why": "The native engine, weights, hardware or recipe changed. Check this recipe again."}
        verify_model_updates(Path(), {name: (row["size"], receipt["model_sha256"][name])
                                      for name, row in recorded["models"].items()},
                             {name: row["mtime_ns"] for name, row in recorded["models"].items()})
        return {**base, "available": True, "qualified": True, "state": "qualified", "why": "",
                "runtime_updated": recorded != current, "hardware": recorded["hardware"]}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {**base, "state": "qualification_error", "why": f"Native {TITLES[kind]} verification could not be checked: {error}"}


def usage_status(kind: str, selected_mode=None) -> dict:
    """What the page and a job are told: with gating off an installed recipe runs as before; with it on, only a checked one."""
    found = status(kind)
    mode = selected_mode or windows_runtime.mode()
    available = found["installed"] and (found["qualified"] or not mode["enabled"])
    return {**found, "available": available, "why": "" if available else found["why"],
            "qualification_required": mode["enabled"], "scope": scope_label(kind)}


def require_scope(kind: str, params: dict) -> None:
    """With gating on, a job stays inside what the check ran (the video recipe; Bolt's check has no job settings)."""
    if kind != "video":
        return
    settings = recipe(kind)
    plain = not any(params.get(key) for key in ("image", "last_frame", "references", "reference_videos", "reference_audios", "loras"))
    frames = params.get("frames")
    seconds = float(params.get("seconds") or 5)
    short = int(frames) <= settings["frames"] if frames else seconds <= settings["frames"] / settings["fps"] + 0.2
    if (params.get("preset") != "small" or params.get("quality") != "fh8" or params.get("steps") not in (None, "", 8, "8")
            or not plain or not short):
        raise ValueError("Verified video scope: a clip from words at Small 864x480, FastH3 8 passes, up to 5 seconds; "
                         "no starting picture, last frame, references or LoRAs. Other settings have no check yet.")


def check_video(path: Path) -> None:
    import graphics
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError("The video check did not write its clip.")
    found = graphics.probe(path)
    settings = recipe("video")
    wanted = settings["frames"] / settings["fps"]
    if ((found["width"], found["height"]) != (settings["width"], settings["height"]) or found["seconds"] is None
            or not math.isclose(found["fps"], settings["fps"], abs_tol=0.01) or abs(found["seconds"] - wanted) > 0.25):
        raise RuntimeError("The video check's clip does not have its checked canvas, frame rate or length.")
    if not found["audio"]:
        raise RuntimeError("The video check's clip has no sound.")


def video_command(target: Path) -> tuple[list[str], dict]:
    command = windows_cuda.translate(["bash", str(features.PACK / "scripts" / "video.sh"), VIDEO_PROMPT, str(target)])
    return command, dict(VIDEO_ENV)


def worker(kind: str, output: Path) -> None:
    """The production path with an isolated output folder: the render script the app runs, or the app's own Bolt engine."""
    output.mkdir(parents=True, exist_ok=True)
    if kind == "video":
        target = output / "video" / "check.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        command, env = video_command(target)
        done = subprocess.run(command, env={**os.environ, **env}, stdin=subprocess.DEVNULL, timeout=TIMEOUT_S,
                              creationflags=0x08000000 if sys.platform == "win32" else 0)
        if done.returncode:
            raise RuntimeError(f"The production video render failed ({done.returncode}).")
        return
    import urllib.request
    settings = recipe(kind)
    one = bolt_engine()
    try:
        if why := one.check():
            raise RuntimeError(why)
        with one.request() as base:  # the engine's own request: it loads, and is counted in flight
            body = json.dumps({"model": "bolt", "max_tokens": settings["max_tokens"], "temperature": 0,
                               "messages": [{"role": "user", "content": settings["question"]}]}).encode()
            call = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(call, timeout=300) as reply:
                said = json.load(reply)
        text = str(said["choices"][0]["message"].get("content") or "").strip()
        if len(text.split()) < 3:
            raise RuntimeError("Bolt answered with fewer than three words.")
        (output / "bolt").mkdir(parents=True, exist_ok=True)
        (output / "bolt" / "answer.json").write_text(json.dumps({"question": settings["question"], "answer": text,
                                                                 "usage": said.get("usage")}), encoding="utf-8")
    finally:
        process = one.process
        one.release("Bolt qualification finished", 30)
        if process is not None and process.poll() is None:
            raise RuntimeError("The owned Bolt engine did not drain; no qualification can be saved.")


def check_bolt(output: Path) -> None:
    found = json.loads((output / "bolt" / "answer.json").read_text(encoding="utf-8"))
    if len(str(found.get("answer", "")).split()) < 3:
        raise RuntimeError("The Bolt check did not record an answer.")


def qualify(kind: str, output: Path, job_id: str) -> dict:
    attempt = {"feature": kind, "job_id": job_id, "ok": False, "checked_at": datetime.now(timezone.utc).isoformat()}
    observations = {"case": "FastH3: 5-second 864x480 clip from words" if kind == "video" else "Bolt: load, one short answer, unload",
                    "rendered": False}
    features.save_attempt(kind, attempt)
    try:
        if why := prerequisites(kind):
            raise RuntimeError(why)
        if output.exists():
            raise RuntimeError("The engine check needs a fresh output directory.")
        before = fingerprint(kind)
        command = [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-native-engine-check.py"),
                   "--feature", kind, "--output", str(output), "--worker"]
        validate = (lambda: check_video(output / "video" / "check.mp4")) if kind == "video" else (lambda: check_bolt(output))
        native.observe_native(command, output, validate, observations, TIMEOUT_S + 60)
        digests = {name: audio.digest(Path(name), row["size"], row["mtime_ns"]) for name, row in before["models"].items()}
        current = fingerprint(kind)
        if (before["models"] != current["models"]
                or not matches_qualification(before, current, compatibility(before), compatibility(current))):
            raise RuntimeError("The engine, weights or execution contract changed during qualification.")
        receipt = {**attempt, "ok": True, "fingerprint": before, "model_sha256": digests, "results": [observations]}
        features.save_pass(kind, receipt)
        features.save_attempt(kind, receipt)
        return receipt
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        features.save_attempt(kind, {**attempt, "error": str(error), "observations": observations})
        raise
