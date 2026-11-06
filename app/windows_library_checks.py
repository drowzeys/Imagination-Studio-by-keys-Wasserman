"""Independent Library qualification on pinned bundled media, never automatic GPU tests."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from feature_checks import matches_qualification
import windows_audio_checks as audio
import windows_cuda
import windows_features as features
import windows_image_checks as native
import windows_runtime
from windows_qualification import gpu_compatibility, hardware_profile, verify_model_updates

KINDS = ("turnaround", "upscale", "mask", "remove", "swap")
TITLES = {"turnaround": "Turnaround sheet", "upscale": "Upscale", "mask": "Find a person",
          "remove": "Remove a person", "swap": "Character Swap"}
CONTRACT = 1
TIMEOUT_S = 3600
FIXTURES = {
    "baker_studio_864x480_5s.mp4": "83e7615a664ba8c05835e8633fdbd67a33df18a66f9438297dbf3edf1d002526",
    "baker_studio_1344x768_8s.png": "05dd028ae55cb5bc9e65aea37e691c0c37592cf0bd986bb234c8574ba4564f95",
}
PERSON = "man in a white shirt and green apron"


def scope_label(kind: str) -> str:
    if kind == "turnaround":
        return "Bundled baker picture; input up to 512x288, five 512px views, strength 1, 28 production passes."
    if kind == "swap":
        return "Bundled baker shot; five-second 864x480 part at 24 fps, 480 quality, anchor off. One window; no likeness guarantee."
    return ("Bundled baker shot; input up to 512x288, 22 frames at 24 fps, without audio. "
            + ("1 MP output, denoise 0.1." if kind == "upscale" else
               "22-frame windows, 12 passes, automatic clean frame, silent output." if kind == "remove" else
               "SAM person mask, matte and plate; no H3 transformer required."))


def recipe(kind: str) -> dict:
    if kind not in KINDS:
        raise ValueError("Choose a registered Library qualification.")
    if kind == "turnaround":
        return {"width": 512, "height": 288, "view_size": 512, "strength": 1.0, "steps": 28}
    if kind == "swap":
        return {"width": 864, "height": 480, "fps": 24, "seconds": 5, "quality": 480,
                "anchor": False, "direction": "", "steps": 8}
    result = {"width": 512, "height": 288, "fps": 24, "frames": 22, "audio": False}
    if kind == "upscale":
        result.update(megapixels=1.0, denoise=0.1)
    if kind == "remove":
        result.update(window=22, steps=12, silent=True, clean_frame=False)
    return result


def studio_home() -> Path:
    from studio_home import studio_home as find
    return find()


def builders(home: Path):
    import actions
    import swap
    import graphics
    def inside(value):
        path = (home / value).resolve()
        if not path.is_relative_to(home.resolve()):
            raise ValueError("Library input must be inside its Studio folder.")
        return path
    def relative(path):
        return str(Path(path).relative_to(home))
    def measure(path):
        found = graphics.probe(Path(path))
        return found["seconds"], found["audio"]
    action = actions.attach(home, features.PACK, True, inside, relative, windows=False)[1]
    swapped = swap.attach(home, features.PACK, True, inside, relative, measure)[1]
    return action, swapped


def required_files(kind: str, home: Path | None = None) -> list[Path]:
    import spark_generate as generation
    import spark_workflows as workflows
    root = windows_runtime.model_root()
    prefix = windows_cuda.root()
    names = [root / "Workflows-Comfy" / "checkpoints" / workflows.SAM] if kind in ("mask", "remove", "swap") else []
    if kind == "upscale":
        names.append(root / "Workflows-Comfy" / "latent_upscale_models" / workflows.UPSCALER)
    if kind != "mask":
        shared = root / "FastH3-Comfy"
        names += [shared / "text_encoders" / generation.H3_TEXT, shared / "vae" / generation.H3_VAE,
                  shared / "vae" / generation.H3_AUDIO_VAE]
        # These action LoRAs require bf16 blocks. Runtime merging into int8 spoils their output.
        transformer = generation.REF2VA["bf16"]
        if kind == "swap":
            import swap
            told = os.environ.get("SWAP_WEIGHTS", "")
            choices = (told,) if told else ("int8", "bf16")
            transformer = next((generation.REF2VA[route] for route in choices if route in generation.REF2VA
                                and (root / "MiniMax-H3-Comfy" / "diffusion_models" / generation.REF2VA[route]).is_file()),
                               generation.REF2VA["int8"])
            names += [root / swap.SWAP_LORA, root / swap.TURBO]
        names.append(root / "MiniMax-H3-Comfy" / "diffusion_models" / transformer)
    if kind != "swap":
        packs = {"turnaround": "ComfyUI-H3-ContactSheet", "upscale": "ComfyUI-H3-Video-Upsampler",
                 "mask": "h3-relay", "remove": "h3-relay"}
        pack = prefix / "ComfyUI" / "custom_nodes" / packs[kind]
        if not pack.is_dir():
            names.append(pack)
        else:
            names += sorted(pack.rglob("*.py")) or [pack / "__init__.py"]
        import actions
        if actions.LORA[kind]:
            builder = builders(home or studio_home())[0]
            lora = builder.lora_file(actions.LORA[kind])
            names.append(lora or (home or studio_home()) / "loras" / (actions.LORA[kind] + ".safetensors"))
    return names


def prerequisites(kind: str) -> str:
    recipe(kind)
    if why := windows_cuda.why_not():
        return why
    if not (windows_cuda.root() / "ComfyUI" / "extra_model_paths.yaml").is_file():
        return "Run the native setup to configure ComfyUI's model folders."
    for tool in ("ffmpeg", "ffprobe"):
        import windows_media
        if not shutil.which(tool, path=windows_media.search_path(os.environ.get("PATH", ""))):
            return "Install native FFmpeg/ffprobe with scripts\\setup-windows-ffmpeg.ps1."
    missing = [path.name for path in required_files(kind) if not path.is_file() or path.stat().st_size == 0]
    if missing:
        return "Missing Library prerequisites: " + ", ".join(missing) + ". Use the component setup command below and install the action's LoRA in the LoRA Hub."
    if kind == "turnaround" and (os.environ.get("STEPS", "28") != "28" or os.environ.get("PROMPT")):
        return "This turnaround check uses 28 passes and the production default instruction; clear STEPS/PROMPT overrides."
    for name in FIXTURES:
        if not (features.PACK / "samples" / name).is_file():
            return "The bundled baker qualification fixtures are missing from this checkout."
    return ""


def fingerprint(kind: str) -> dict:
    for name, expected in FIXTURES.items():
        path = features.PACK / "samples" / name
        if audio.digest(path, path.stat().st_size, path.stat().st_mtime_ns) != expected:
            raise RuntimeError("The pinned bundled qualification media changed.")
    files = required_files(kind)
    identities = {str(path.resolve()): {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} for path in files}
    return {"contract": CONTRACT, "kind": kind, "recipe": recipe(kind), "fixtures": FIXTURES,
            "hardware": {**hardware_profile(), "device": hashlib.sha256(native.device_uuid().encode()).hexdigest()},
            "abi": gpu_compatibility(Path(windows_cuda.python()).parent.parent), "models": identities,
            "backend": {key: os.environ.get(key, "") for key in
                        ("SPARK_COMFY_ARGS", "SPARK_VAE", "SWAP_WEIGHTS", "SWAP_REF_SIDE", "STEPS", "PROMPT",
                         "SPARK_MEMORY_FLOOR_MB", "PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_ALLOC_CONF")},
            "sources": {name: hashlib.sha256(features.PACK.joinpath(*name.split("/")).read_bytes()).hexdigest()
                        for name in ("app/actions.py", "app/swap.py", "spark/spark_workflows.py",
                                     "spark/spark_swap.py", "app/windows_library_checks.py")}}


def compatibility(value: dict) -> dict:
    return {**{key: value[key] for key in ("contract", "kind", "recipe", "fixtures", "hardware", "abi", "backend")},
            "model_sizes": {name: row["size"] for name, row in value["models"].items()}}


def status(kind: str) -> dict:
    base = {"installed": False, "available": False, "qualified": False, "check_available": True,
            "state": "not_installed", "verified_recipe": recipe(kind)}
    try:
        if why := prerequisites(kind):
            return {**base, "why": why}
        base.update(installed=True, state="needs_verification")
        previous = features.saved(kind, "")
        if previous.get("ok") is not True:
            return {**base, "why": "Check this Library recipe in Machine > Feature checks."}
        current = fingerprint(kind)
        recorded = previous["fingerprint"]
        if not matches_qualification(recorded, current, compatibility(recorded), compatibility(current)):
            return {**base, "why": "The Library recipe, files, hardware or execution contract changed. Check it again."}
        verify_model_updates(Path(), {name: (row["size"], previous["model_sha256"][name])
                                      for name, row in recorded["models"].items()},
                             {name: row["mtime_ns"] for name, row in recorded["models"].items()})
        return {**base, "qualified": True, "available": True, "state": "qualified", "why": "",
                "runtime_updated": recorded != current, "hardware": recorded["hardware"]}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {**base, "state": "qualification_error", "why": f"Library verification could not be checked: {error}"}


def usage_status(kind: str, selected_mode=None) -> dict:
    found = status(kind)
    mode = selected_mode or windows_runtime.mode()
    available = found["installed"] and (found["qualified"] or not mode["enabled"])
    return {**found, "available": available, "why": "" if available else found["why"],
            "qualification_required": mode["enabled"], "scope": scope_label(kind)}


def require_scope(kind: str, params: dict) -> None:
    import graphics
    from PIL import Image
    settings = recipe(kind)
    key = "image" if kind == "turnaround" else "video"
    path = (studio_home() / str(params.get(key, ""))).resolve()
    if not path.is_relative_to(studio_home().resolve()) or not path.is_file():
        raise ValueError("Choose existing media inside this Studio.")
    if kind == "turnaround":
        with Image.open(path) as image:
            if image.width > settings["width"] or image.height > settings["height"]:
                raise ValueError("Verified turnaround input is at most 512x288; use an explicitly resized copy.")
        if int(params.get("size", 1024)) != 512 or float(params.get("strength", 1)) != 1 or params.get("prompt"):
            raise ValueError("Verified turnaround: 512px views, strength 1, production default instruction.")
    else:
        info = graphics.probe(path)
        if (info["width"] > settings["width"] or info["height"] > settings["height"]
                or not math.isclose(info["fps"], 24, abs_tol=0.01)):
            raise ValueError(f"Verified {kind}: input up to {settings['width']}x{settings['height']} at 24 fps.")
        seconds = info["seconds"]
        if seconds is None:
            raise ValueError("The clip duration could not be established.")
        if kind == "swap":
            if (float(params.get("seconds", 5)) != 5 or int(params.get("quality", 480)) != 480
                    or params.get("anchor", True) is not False or params.get("direction")):
                raise ValueError("Verified Swap: five-second part, 480 quality, anchor off, default direction.")
            reference = (studio_home() / str(params.get("image", ""))).resolve()
            if not reference.is_relative_to(studio_home().resolve()) or not reference.is_file():
                raise ValueError("Choose a reference picture inside Studio.")
            with Image.open(reference) as image:
                if image.width > 512 or image.height > 288:
                    raise ValueError("Verified Swap reference input is at most 512x288; use an explicitly resized copy.")
        elif round(seconds * 24) != 22 or info["audio"]:
            raise ValueError("Verified Library clip: 22 frames / 24 fps, without audio.")
        if kind == "upscale" and (float(params.get("megapixels", 2)) != 1 or float(params.get("denoise", 0.1)) != 0.1):
            raise ValueError("Verified upscale: 1 megapixel, denoise 0.1.")
        if kind == "remove" and (int(params.get("window", 22)) != 22 or int(params.get("steps", 12)) != 12
                                 or params.get("clean_frame") or not params.get("silent")):
            raise ValueError("Verified removal: 22-frame windows, 12 passes, automatic clean frame, silent output.")


def tool(name: str) -> str:
    import windows_media
    found = shutil.which(name, path=windows_media.search_path(os.environ.get("PATH", "")))
    if not found:
        raise RuntimeError(f"{name} is missing; install native media tools.")
    return found


def fixtures(output: Path, kind: str) -> dict:
    from PIL import Image
    for name, expected in FIXTURES.items():
        path = features.PACK / "samples" / name
        actual = audio.digest(path, path.stat().st_size, path.stat().st_mtime_ns)
        if actual != expected:
            raise RuntimeError("Bundled qualification fixture changed; no check was started.")
    inputs = output / "inputs"
    inputs.mkdir(parents=True)
    with Image.open(features.PACK / "samples" / "baker_studio_1344x768_8s.png") as image:
        image.convert("RGB").resize((512, 288)).save(inputs / "person.png")
    command = [tool("ffmpeg"), "-v", "error", "-y", "-i",
               str(features.PACK / "samples" / "baker_studio_864x480_5s.mp4")]
    if kind == "swap":
        command += ["-t", "5", "-vf", "fps=24,scale=864:480", "-c:a", "aac"]
    else:
        command += ["-vf", "fps=24,scale=512:288", "-frames:v", "22", "-an"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(inputs / "clip.mp4")]
    subprocess.run(command, capture_output=True, timeout=60, check=True, creationflags=0x08000000)
    params = {"image": "inputs/person.png", "video": "inputs/clip.mp4", "person": PERSON, "seed": 7}
    params.update({"size": 512, "strength": 1} if kind == "turnaround" else
                  {"megapixels": 1, "denoise": 0.1} if kind == "upscale" else
                  {"window": 22, "steps": 12, "silent": True} if kind == "remove" else
                  {"seconds": 5, "quality": 480, "anchor": False} if kind == "swap" else {})
    return params


def built(kind: str, home: Path, params: dict):
    from fastapi import HTTPException
    action, swapped = builders(home)
    try:
        command, env, outputs = (swapped if kind == "swap" else action)(kind, params, "check")
    except HTTPException as error:
        raise ValueError(str(error.detail)) from error
    command = windows_cuda.translate(command)
    # Turnaround's wrapper reads the environment; pin it to the same defaults represented in the receipt.
    if kind == "turnaround":
        env["STEPS"] = "28"
    return command, env, outputs


def worker(kind: str, output: Path, params: dict):
    command, env, _ = built(kind, output, params)
    done = subprocess.run(command, env={**os.environ, **{key: str(value) for key, value in env.items()}},
                          stdin=subprocess.DEVNULL, timeout=TIMEOUT_S, creationflags=0x08000000)
    if done.returncode:
        raise RuntimeError(f"The production {kind} workflow failed ({done.returncode}).")


def check_outputs(kind: str, outputs: list[str]):
    import graphics
    from PIL import Image, ImageStat
    for name in outputs:
        path = Path(name)
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"The workflow did not write {path.name}.")
        if path.suffix == ".png":
            with Image.open(path) as image:
                image.load()
                if max(ImageStat.Stat(image.convert("RGB")).stddev) < 1:
                    raise RuntimeError("The workflow produced a blank picture.")
                if kind == "turnaround" and "_view" in path.stem and image.size != (512, 512):
                    raise RuntimeError("Turnaround did not produce 512x512 views.")
        else:
            found = graphics.probe(path)
            maximum = 5.1 if kind == "swap" else 22 / 24 + 0.1
            if (found["seconds"] is None or not 0.1 <= found["seconds"] <= maximum or found["width"] <= 0
                    or not math.isclose(found["fps"], 24, abs_tol=0.01)):
                raise RuntimeError("The workflow output duration, canvas or frame rate is invalid.")
            if kind == "upscale" and not 0.9e6 <= found["width"] * found["height"] <= 1.1e6:
                raise RuntimeError("The upscale did not produce its one-megapixel canvas.")
            if kind in ("mask", "remove", "swap"):
                expected = (864, 480) if kind == "swap" else (512, 288)
                frames = 120 if kind == "swap" else 22
                if (found["width"], found["height"]) != expected or round(found["seconds"] * 24) != frames:
                    raise RuntimeError("The Library output did not preserve the checked canvas/frame count.")
            if kind == "mask" and "_matte" in path.stem:
                raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(path), "-frames:v", "1",
                                      "-vf", "scale=64:36", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                                     capture_output=True, check=True, timeout=30, creationflags=0x08000000).stdout
                if not raw or max(raw) < 32 or min(raw) > 224:
                    raise RuntimeError("Find a person produced an empty or full-frame mask.")


def qualify(kind: str, output: Path, job_id: str) -> dict:
    attempt = {"feature": kind, "job_id": job_id, "ok": False, "checked_at": datetime.now(timezone.utc).isoformat()}
    observations = {"case": TITLES[kind] + " on bundled baker fixture", "rendered": False}
    features.save_attempt(kind, attempt)
    try:
        if why := prerequisites(kind):
            raise RuntimeError(why)
        if output.exists():
            raise RuntimeError("The Library check requires a fresh output folder.")
        output.mkdir(parents=True)
        params = fixtures(output, kind)
        before = fingerprint(kind)
        _, _, outputs = built(kind, output, params)
        request = output / "request.json"
        request.write_text(json.dumps(params), encoding="utf-8")
        command = [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-library-check.py"),
                   "--feature", kind, "--output", str(output), "--worker"]
        native.observe_native(command, output, lambda: check_outputs(kind, outputs), observations, TIMEOUT_S + 60)
        digests = {name: audio.digest(Path(name), row["size"], row["mtime_ns"]) for name, row in before["models"].items()}
        current = fingerprint(kind)
        if before["models"] != current["models"] or not matches_qualification(before, current, compatibility(before), compatibility(current)):
            raise RuntimeError("The Library execution contract or model files changed during the check.")
        receipt = {**attempt, "ok": True, "fingerprint": before, "model_sha256": digests, "results": [observations]}
        features.save_pass(kind, receipt)
        features.save_attempt(kind, receipt)
        return receipt
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        features.save_attempt(kind, {**attempt, "error": str(error), "observations": observations})
        raise
