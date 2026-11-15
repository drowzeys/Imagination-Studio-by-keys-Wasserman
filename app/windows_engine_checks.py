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

# The video check has levels: a canvas and a length each. A passed level opens every clip that is no larger and no
# longer than it (the same model and passes on fewer pixels and frames take less memory and less time), so the largest
# level covers them all, and a machine that cannot pass it still has the smaller ones. "video" is the first level.
VIDEO_LEVELS = {
    "video": ("Small, 5 seconds", 864, 480, 124),
    "video-long": ("Small, 15 seconds", 864, 480, 362),
    "video-large": ("Full size, 5 seconds", 1376, 768, 124),
    "video-sharp": ("Full size, 10 seconds", 1376, 768, 243),
    "video-largest": ("Full size, 15 seconds", 1376, 768, 362),
}
# A long video is a chain of clips: each part opens on the last frame of the one before, and the parts are joined
# (server.long_take). Its check runs that chain once, two short parts: the second starts from a picture, which no size
# level does. A pass opens long videos, and clips from a starting picture, whose parts fit a passed size level.
CHAIN = "video-chain"
CHAIN_PARTS, CHAIN_SECONDS = 2, 10  # two 5-second parts, joined and cut to 10 seconds like a job's
VIDEO_KINDS = (*VIDEO_LEVELS, CHAIN)
KINDS = (*VIDEO_KINDS, "bolt")
TITLES = {**{kind: "H3 / FastH3 video: " + row[0].lower() for kind, row in VIDEO_LEVELS.items()},
          "video": "H3 / FastH3 video", CHAIN: "H3 / FastH3 video: long videos and a starting picture",
          "bolt": "NVIDIA Nemotron 3.5 Bolt"}
# the canvas each size of the app is generated at (server.PRESETS): what a job is measured by
CANVASES = {"small": (864, 480), "p720": (1280, 736), "native": (1344, 768), "sharp": (1376, 768)}
QUALITIES = ("fh8", "fh4")  # FastH3 at the checked 8 passes, or at 4: fewer passes of the same recipe
CONTRACT = 1
TIMEOUT_S = 3600
VIDEO_PROMPT = ("A middle-aged baker in a cream shirt and green apron stands behind a wooden counter in a small bakery at "
                "dawn, warm window light from the left. He lifts a loaf with both hands and sets it down on the counter.")
# what leads a prompt whose clip opens on a given picture (server.LEAD and the render scripts' own)
LEAD = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
BOLT_QUESTION = "In one short sentence, what is a clapperboard used for?"


def video_env(kind: str = "video") -> dict:
    """The plain clip a level renders, as server.build makes it for that size at "fh8" and that many frames."""
    _, width, height, frames = VIDEO_LEVELS[kind]
    return {"SEED": "7", "FRAMES": str(frames), "WIDTH": str(width), "HEIGHT": str(height), "ENGINE": "fasth3", "STEPS": "8"}


VIDEO_ENV = video_env()


def recipe(kind: str) -> dict:
    if kind == CHAIN:
        _, width, height, frames = VIDEO_LEVELS["video"]
        return {"mode": "long video", "engine": "fasth3", "width": width, "height": height, "steps": 8, "frames": frames,
                "fps": 24, "parts": CHAIN_PARTS, "seconds": CHAIN_SECONDS, "image": True, "last_frame": False,
                "references": False, "loras": False}
    if kind in VIDEO_LEVELS:
        _, width, height, frames = VIDEO_LEVELS[kind]
        return {"mode": "text to video", "engine": "fasth3", "width": width, "height": height, "steps": 8, "frames": frames,
                "fps": 24, "image": False, "last_frame": False, "references": False, "loras": False}
    if kind == "bolt":
        return {"question": BOLT_QUESTION, "max_tokens": 48, "streams": 1, "tools": False, "vision": False}
    raise ValueError("Choose H3 video or Bolt.")


def scope_label(kind: str) -> str:
    recipe(kind)
    if kind == CHAIN:
        return (f"One long video as the queue makes it: {CHAIN_PARTS} FastH3 parts of 5 seconds at 864x480, the second opening on the last "
                f"frame of the first, joined into {CHAIN_SECONDS} seconds with sound. A pass opens long videos (up to 30 minutes) and clips "
                "from a starting picture, each part no larger and no longer than a passed size check, at 8 or 4 passes. "
                "No last frame, references or LoRAs. This checks execution, not the picture's quality.")
    if kind in VIDEO_LEVELS:
        _, width, height, frames = VIDEO_LEVELS[kind]
        return (f"One FastH3 clip from words: {width}x{height}, 8 passes, {frames} frames ({frames / 24:.0f} seconds) at 24 fps, with sound. "
                "A pass opens clips from words up to this size and length, at 8 or 4 passes. "
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
    if kind in VIDEO_KINDS:
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
    if kind in VIDEO_KINDS:
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
    if kind in VIDEO_KINDS:
        import shutil
        import windows_media
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool, path=windows_media.search_path(os.environ.get("PATH", ""))):
                return "Install the native media tools with scripts\\setup-windows-ffmpeg.ps1."
    return ""


def fingerprint(kind: str) -> dict:
    engine, model, weights = files(kind)
    mapping = {str(path.resolve()): {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} for path in weights}
    sources = (("app/windows_engine_checks.py", "spark/spark_generate.py", "spark/windows_scripts.py", "app/chain.py") if kind == CHAIN
               else ("app/windows_engine_checks.py", "spark/spark_generate.py", "spark/windows_scripts.py") if kind in VIDEO_LEVELS
               else ("app/windows_engine_checks.py", "app/bolt.py"))
    guards = (("SPARK_COMFY_ARGS", "SPARK_VAE", "SPARK_MEMORY_FLOOR_MB", "PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_ALLOC_CONF") if kind in VIDEO_KINDS
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


def passed_levels() -> list[str]:
    """The video levels with a pass that still stands, smallest first."""
    return [kind for kind in VIDEO_LEVELS if status(kind)["qualified"]]


def video_status() -> dict:
    """Video as the gate sees it: checked when any level passed, and then held to what the passed levels cover."""
    levels = {kind: status(kind) for kind in VIDEO_LEVELS}
    done = [kind for kind, found in levels.items() if found["qualified"]]
    first = levels["video"]
    if not done:
        return {**first, "passed_levels": []}
    widest = max(done, key=lambda kind: (VIDEO_LEVELS[kind][1] * VIDEO_LEVELS[kind][2], VIDEO_LEVELS[kind][3]))
    return {**levels[widest], "installed": True, "qualified": True, "available": True, "state": "qualified", "why": "",
            "passed_levels": done, "verified_recipe": [recipe(kind) for kind in done]}


def long_status() -> dict:
    """Long videos as the gate sees them: a passed size level and the passed long-video check."""
    sized = video_status()
    if not sized["qualified"]:
        return sized
    chained = status(CHAIN)
    return sized if chained["qualified"] else {**chained, "passed_levels": sized["passed_levels"]}


def job_canvas(params: dict) -> tuple[int, int] | None:
    preset = params.get("preset")
    if preset in CANVASES:
        return CANVASES[preset]
    if preset == "custom":
        try:
            return int(params["width"]), int(params["height"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def chain_passed() -> bool:
    return status(CHAIN)["qualified"]


def fits(canvas: tuple[int, int] | None, frames: int | None, done: list[str]) -> bool:
    return bool(canvas and frames and any(canvas[0] * canvas[1] <= VIDEO_LEVELS[level][1] * VIDEO_LEVELS[level][2]
                                          and frames <= VIDEO_LEVELS[level][3] for level in done))


def longest_part(params: dict) -> int | None:
    """The longest part, in seconds, a long video of this size may be made of here: the longest of the app's clip
    lengths that a passed size check covers (None when none does)."""
    import lengths
    done, canvas = passed_levels(), job_canvas(params)
    return max((seconds for seconds, frames in lengths.CLASSIC.items() if fits(canvas, frames, done)), default=None)


def require_scope(kind: str, params: dict) -> None:
    """With gating on, a job stays inside what a passed check ran: a clip no larger and no longer than a passed video
    level, at its 8 passes or at 4; from a starting picture, or as the parts of a long video, once the long-video
    check passed (Bolt's check has no job settings)."""
    if kind not in ("video", "long"):
        return
    import lengths
    done = passed_levels()
    chained = chain_passed() if kind == "long" or params.get("image") else False
    plain = not any(params.get(key) for key in ("last_frame", "last_image", "last_same", "last_frame_from", "references",
                                                "reference_videos", "reference_audios", "loras"))
    canvas = job_canvas(params)
    try:
        if kind == "long":
            frames = lengths.CLASSIC.get(int(params.get("segment_seconds") or max(lengths.CLASSIC)))
        else:
            frames = lengths.of({"frames": params.get("frames"), "seconds": float(params.get("seconds") or 5)})
    except (TypeError, ValueError):
        frames = None
    steps_ok = params.get("steps") in (None, "") or str(params.get("steps")) in ("4", "8")
    have = "; ".join(f"{VIDEO_LEVELS[level][1]}x{VIDEO_LEVELS[level][2]} up to {VIDEO_LEVELS[level][3] / 24:.0f} s" for level in done) or "none"
    if (kind == "long" or params.get("image")) and not chained:
        raise ValueError(("Verified video scope: a long video is a chain of clips, each opening on the last frame of the one before. "
                          if kind == "long" else "Verified video scope: a clip from a starting picture has its own check. ")
                         + f"Run the \"{TITLES[CHAIN]}\" check in Machine > Feature checks.")
    if not (plain and steps_ok and params.get("quality") in QUALITIES and fits(canvas, frames, done)):
        what = "each part of a long video is a clip" if kind == "long" else "a clip"
        raise ValueError(f"Verified video scope: {what}, FastH3 at 8 or 4 passes, no larger and no longer than a "
                         f"passed video check ({have}); no last frame, references or LoRAs. "
                         "Machine > Feature checks has a check for larger sizes and longer clips.")


def check_video(path: Path, kind: str = "video") -> None:
    import graphics
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError("The video check did not write its clip.")
    found = graphics.probe(path)
    settings = recipe(kind)
    wanted = settings["seconds"] if kind == CHAIN else settings["frames"] / settings["fps"]
    if ((found["width"], found["height"]) != (settings["width"], settings["height"]) or found["seconds"] is None
            or not math.isclose(found["fps"], settings["fps"], abs_tol=0.1 if kind == CHAIN else 0.01)  # a join's average rate
            or abs(found["seconds"] - wanted) > 0.25):
        raise RuntimeError("The video check's clip does not have its checked canvas, frame rate or length.")
    if not found["audio"]:
        raise RuntimeError("The video check's clip has no sound.")


def video_command(target: Path, kind: str = "video") -> tuple[list[str], dict]:
    command = windows_cuda.translate(["bash", str(features.PACK / "scripts" / "video.sh"), VIDEO_PROMPT, str(target)])
    return command, video_env(kind)


def chain_commands(folder: Path, index: int, still: Path | None) -> tuple[list[str], dict, Path]:
    """Part ``index`` of the checked long video as server.long_take and server.build make it: the first from words, the
    next from the last frame of the one before (studio.sh with the picture given and the prompt led by it)."""
    target = folder / f"seg{index + 1:03d}.mp4"
    env = {**video_env("video"), "SEED": str(7 + index)}
    if still is None:
        return windows_cuda.translate(["bash", str(features.PACK / "scripts" / "video.sh"), VIDEO_PROMPT, str(target)]), env, target
    command = windows_cuda.translate(["bash", str(features.PACK / "scripts" / "studio.sh"), "", f"{LEAD}\n\n{VIDEO_PROMPT}", str(target)])
    return command, {**env, "IMAGE_FILE": str(still), "RAW_PROMPT": "1"}, target


def chain_worker(output: Path) -> None:
    import chain
    import windows_media
    folder = output / "video"
    folder.mkdir(parents=True, exist_ok=True)
    tools = {**os.environ, "PATH": windows_media.search_path(os.environ.get("PATH", ""))}
    clips, still = [], None
    for index in range(CHAIN_PARTS):
        if index:
            still = folder / f"seg{index:03d}_first.jpg"
            chain.last_frame(clips[-1], still, tools)
        command, env, target = chain_commands(folder, index, still)
        done = subprocess.run(command, env={**os.environ, **env}, stdin=subprocess.DEVNULL, timeout=TIMEOUT_S,
                              creationflags=0x08000000 if sys.platform == "win32" else 0)
        if done.returncode:
            raise RuntimeError(f"Part {index + 1} of the production long video failed ({done.returncode}).")
        if not target.is_file() or not target.stat().st_size:
            raise RuntimeError(f"Part {index + 1} of the long video wrote no clip.")
        clips.append(target)
    chain.join(clips, folder / "check.mp4", CHAIN_SECONDS, recipe(CHAIN)["fps"], folder, tools)


def worker(kind: str, output: Path) -> None:
    """The production path with an isolated output folder: the render script the app runs, or the app's own Bolt engine."""
    output.mkdir(parents=True, exist_ok=True)
    if kind == CHAIN:
        return chain_worker(output)
    if kind in VIDEO_LEVELS:
        target = output / "video" / "check.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        command, env = video_command(target, kind)
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
    observations = {"case": (f"FastH3: a {CHAIN_SECONDS}-second long video of {CHAIN_PARTS} joined parts, the second from the first's last frame"
                             if kind == CHAIN else
                             f"FastH3: {VIDEO_LEVELS[kind][3] / 24:.0f}-second {VIDEO_LEVELS[kind][1]}x{VIDEO_LEVELS[kind][2]} clip from words"
                             if kind in VIDEO_LEVELS else "Bolt: load, one short answer, unload"), "rendered": False}
    features.save_attempt(kind, attempt)
    try:
        if why := prerequisites(kind):
            raise RuntimeError(why)
        if output.exists():
            raise RuntimeError("The engine check needs a fresh output directory.")
        before = fingerprint(kind)
        command = [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-native-engine-check.py"),
                   "--feature", kind, "--output", str(output), "--worker"]
        validate = (lambda: check_video(output / "video" / "check.mp4", kind)) if kind in VIDEO_KINDS else (lambda: check_bolt(output))
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
