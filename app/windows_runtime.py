"""One Windows runtime: per-feature backends, explicit verification policy and honest capability reports."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
import subprocess
import uuid

import windows_cuda
import model_catalog
from windows_memory import physical_memory_mb

OPTIONAL_QUALIFICATION_MB = 128 * 1024
_SESSION_KEY = uuid.uuid4().hex
BOUNDED = ("scout", "sfx", "foley", "hlg", "finish", "graphics")
IMAGE_SPECS = model_catalog.image_specs()


class RouteUnavailable(ValueError):
    """A supported route whose prerequisites, verification or capacity are not satisfied."""


def mode_file() -> Path:
    return Path(os.environ.get("STUDIO_WINDOWS_FEATURE_DIR") or windows_cuda.root() / "qualification") / "mode.json"


def session_env() -> dict:
    return {"STUDIO_WINDOWS_SESSION_KEY": _SESSION_KEY}


def release_native() -> bool:
    """Drain only this app's native backend, with private session and PID/start identity checks."""
    import comfy_win
    identity = comfy_win.recorded()
    if identity.get("session") != hashlib.sha256(_SESSION_KEY.encode()).hexdigest() or not comfy_win.running():
        return False
    done = subprocess.run(windows_cuda.comfy_argv("stop"), env={**os.environ, **session_env()},
                          capture_output=True, text=True, timeout=60, creationflags=0x08000000 if windows_cuda.native_os() else 0)
    if done.returncode:
        raise RuntimeError("The owned native ComfyUI backend did not stop: " + done.stderr[-500:])
    return True


def backend_status() -> dict:
    import windows_storyboard
    status = windows_storyboard.backend_status()
    import comfy_win
    identity = comfy_win.recorded()
    if identity.get("session") == hashlib.sha256(_SESSION_KEY.encode()).hexdigest() and comfy_win.running():
        return {"running": True, "loaded": True, "native": True, "kind": "native",
                "address": f"http://127.0.0.1:{comfy_win.PORT}", "why": "", "busy": False}
    return status


def mode() -> dict:
    error = ""
    try:
        physical = physical_memory_mb()
        if type(physical) is not int or physical <= 0:
            raise ValueError("installed memory is invalid")
    except (OSError, AttributeError, ValueError) as failure:
        physical = None
        error = f"Installed memory could not be established: {failure}"
    required = physical is None or physical < OPTIONAL_QUALIFICATION_MB
    requested = None
    try:
        stored = json.loads(mode_file().read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or type(stored.get("enabled")) is not bool:
            raise ValueError("the saved qualification mode is invalid")
        requested = stored["enabled"]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as failure:
        error = f"The qualification setting could not be read: {failure}"
        requested = True
    return {"enabled": True if required else bool(requested), "required": required, "can_disable": not required,
            "physical_total_mb": physical,
            "reason": error or ("Qualification is required below 128 GiB installed memory." if required else
                               "Qualification is optional at 128 GiB and above; memory guards and prerequisites always apply.")}


def set_mode(enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("Qualification mode must be a boolean.")
    if not enabled and mode()["required"]:
        raise ValueError("Qualification cannot be disabled below 128 GiB or while installed memory is unknown.")
    target = mode_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=target.parent, suffix=".tmp", delete=False, encoding="utf-8") as pending:
            temporary = Path(pending.name)
            json.dump({"enabled": enabled}, pending)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return mode()


def model_root() -> Path:
    return Path(os.environ.get("SPARK_MODELS") or windows_cuda.paths()["models"])


def sound_python(kind: str) -> Path:
    if kind == "foley":
        if os.environ.get("FOLEY_PYTHON"):
            return Path(os.environ["FOLEY_PYTHON"])
        isolated = windows_cuda.root() / "foley-gpu-venv" / "Scripts" / "python.exe"
        if foley_packages(isolated):
            return isolated
    return Path(os.environ.get("SOUND_PYTHON") or windows_cuda.paths()["python"])


def foley_packages(python: Path) -> bool:
    from importlib import metadata
    if not python.is_file():
        return False
    site = python.parent.parent / "Lib" / "site-packages"
    packages = {dist.metadata["Name"].lower().replace("-", "_") for dist in metadata.distributions(path=[str(site)])
                if dist.metadata["Name"]}
    return {"torch", "numpy", "soxr", "librosa", "torchdiffeq", "open_clip_torch"}.issubset(packages)


def native_sound_installed(kind: str) -> bool:
    import sound
    if windows_cuda.why_not() or not (windows_cuda.root() / "ComfyUI" / "extra_model_paths.yaml").is_file():
        return False
    root = model_root() / "Sound-Comfy"
    if not all((root / name).is_file() for name in sound.SPARK_FILES[kind]):
        return False
    if kind == "foley":
        return ((windows_cuda.root() / "ComfyUI" / "custom_nodes" / "ComfyUI-MMAudio" / "nodes.py").is_file()
                and foley_packages(sound_python(kind)))
    return True


def bounded(kind: str, params: dict | None = None) -> bool:
    if kind == "qualification" or kind == "graphics":
        return True
    if kind == "scout":
        model = (params or {}).get("image_model") or default_image()
        return model == "sd-turbo"
    if kind in ("hlg", "finish", "export"):
        return mode()["enabled"]
    if kind in ("sfx", "foley"):
        return mode()["enabled"] or not native_sound_installed(kind)
    return False


def usage_status(kind: str, found: dict | None = None, selected_mode: dict | None = None) -> dict:
    """Verification gates use, never substitutes for prerequisites or live resource admission."""
    import windows_baseline_checks as baseline
    import windows_features
    import windows_foley
    import windows_storyboard
    import windows_sound
    from windows_qualification import gpu_compatibility, hardware_profile

    selected_mode = mode() if selected_mode is None else selected_mode
    if found is None:
        if kind in ("scout", "sfx"):
            found = windows_storyboard.status() if kind == "scout" else windows_sound.status()
            found = {**found, "qualified": found["available"],
                     "installed": found["available"] or not baseline.prerequisites(kind)}
        else:
            found = (windows_foley.status() if kind == "foley" else windows_features.status(kind)
                     if kind in windows_features.KINDS else baseline.status(kind))
    if kind == "scout":
        found = {**found, "width": windows_storyboard.SIDE, "height": windows_storyboard.SIDE,
                 "max_count": windows_storyboard.MAX_COUNT, "model": windows_storyboard.MODEL}
    qualified = bool(found.get("qualified", found.get("available", False)))
    if qualified or selected_mode["enabled"]:
        return {**found, "available": qualified, "qualified": qualified,
                "installed": found.get("installed", qualified), "qualification_required": selected_mode["enabled"]}
    why = (baseline.prerequisites(kind) if kind in baseline.KINDS else windows_features.prerequisites(kind)
           if kind in windows_features.KINDS else "" if found.get("installed") else found.get("why", "Foley is not installed."))
    installed = not why
    available = installed and (qualified or not selected_mode["enabled"])
    result = {**found, "installed": installed, "qualified": qualified, "available": available,
              "qualification_required": selected_mode["enabled"]}
    if available and not qualified:
        try:
            if kind in ("scout", "sfx", "foley"):
                python = windows_foley.gpu_python() if kind == "foley" else windows_cuda.paths()["python"]
                gpu_compatibility(python.parent.parent)
                if kind == "foley":
                    windows_foley.verify_nodes()
                result["hardware"] = hardware_profile()
        except (OSError, RuntimeError, ValueError) as error:
            return {**result, "available": False, "state": "prerequisite_error", "why": str(error)}
        result.update(state="available_unverified", why="")
    elif not installed:
        result.update(state="not_installed", why=why)
    elif not available:
        result["why"] = found.get("why") or "This recipe needs verification in Machine > Feature checks."
    return result


def image_files(model: str) -> list[Path]:
    _, folder, transformer, _ = IMAGE_SPECS[model]
    root = model_root() / folder
    if model.startswith("flux3k"):
        from spark_generate import FLUX3_OLD
        previous = FLUX3_OLD[transformer]
        if not (root / "diffusion_models" / transformer).is_file() and (root / "diffusion_models" / previous).is_file():
            transformer = previous
    needed = [root / "diffusion_models" / transformer, windows_cuda.root() / "ComfyUI" / "extra_model_paths.yaml"]
    if model.startswith("qwen"):
        needed += [root / "text_encoders" / "qwen3vl_8b_int8_convrot.safetensors",
                   root / "vae" / "qwen_image_2.1_vae_bf16.safetensors"]
    elif model.startswith("flux3k"):
        needed += [root / "text_encoders" / "qwen3vl_4b_bf16.safetensors",
                   root / "text_encoders" / "qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors",
                   root / "vae" / "flux2-vae.safetensors", root / "system_prompts.json"]
    else:
        needed += [root / "text_encoders" / "qwen3.5_9b_bf16.safetensors", root / "vae" / "flux2-vae_small.safetensors",
                   windows_cuda.root() / "ComfyUI" / "comfy_extras" / "nodes_moss_image.py"]
    return needed


def image_options(selected_mode: dict | None = None) -> list[dict]:
    selected_mode = mode() if selected_mode is None else selected_mode
    draft = usage_status("scout", selected_mode=selected_mode)
    rows = [{"id": "sd-turbo", "title": "SD-Turbo storyboard drafts", "label": "SD-Turbo (draft fallback)",
             **draft, "backend": "bounded", "quality": "draft", "scope": "512x512, one step, 1-6 sequential drafts.",
             "requirements": [{"pool": "device", "required_mb": 12288, "reserve_mb": 2048, "basis": "policy"},
                              {"pool": "host", "required_mb": None, "reserve_mb": 0, "basis": "unknown"}]}]
    for model, (title, folder, transformer, part) in IMAGE_SPECS.items():
        needed = image_files(model)
        core_why = windows_cuda.why_not()
        missing = [path.name for path in needed if not path.is_file() or path.stat().st_size == 0]
        installed = not core_why and not missing
        why = (core_why or "Missing model/runtime files: " + ", ".join(missing) if not installed else
               "Installed, but this Windows model recipe has no verification check yet." if selected_mode["enabled"] else "")
        rows.append({"id": model, "title": title, "label": title, "installed": installed, "qualified": False,
                     "available": installed and not selected_mode["enabled"], "qualification_required": selected_mode["enabled"],
                     "state": "needs_install" if not installed else "needs_verification" if selected_mode["enabled"] else "available_unverified",
                     "why": why, "backend": "native", "quality": "full-model",
                     "scope": "Native model workflow; existing canvas and engine limits apply. Windows peak memory is not measured yet.",
                     "requirements": [{"pool": pool, "required_mb": None, "reserve_mb": 0, "basis": "unknown"}
                                      for pool in ("host", "device", "commit")],
                     "setup": rf"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-images.ps1 -Model {model}",
                     "check_available": False, "measurement_source": "No Windows peak-memory envelope established."})
        import windows_image_checks
        if model in windows_image_checks.KINDS:
            found = windows_image_checks.status(model)
            available = found["qualified"] or found["installed"] and not selected_mode["enabled"]
            rows[-1].update({**found, "available": available, "qualification_required": selected_mode["enabled"]})
            if available:
                rows[-1].update(why="", state="qualified" if found["qualified"] else "available_unverified")
            verified = found.get("verified_scopes", [])
            rows[-1]["scope"] = ("Verified: " + "; ".join(f"{scope['width']}x{scope['height']}, {scope['count']} image(s)"
                                                        for scope in verified) if verified else
                                 "512x512, one image is the basic check; larger canvases and counts are checked separately.")
            rows[-1]["measurement_source"] = "This machine's observed check, not a minimum RAM requirement." if found["qualified"] else "No successful native image check saved yet."
    return rows


def default_image() -> str:
    rows = image_options()
    ready = [row for row in rows if row["available"]]
    if not mode()["enabled"]:
        native = next((row for row in ready if row["backend"] == "native"), None)
        if native is not None:
            return native["id"]
    return ready[0]["id"] if ready else "sd-turbo"


def freeze(kind: str, params: dict) -> dict:
    if kind == "scout" and not params.get("image_model"):
        return {**params, "image_model": default_image()}
    return params


def video_missing(params: dict | None = None) -> list[str]:
    root = model_root()
    common = root / "FastH3-Comfy"
    missing = [name for name in ("text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                                 "vae/minimax_h3_video_vae_int8_convrot.safetensors", "vae/minimax_h3_audio_vae_fp32.safetensors")
               if not (common / name).is_file()]
    fast = any((common / "diffusion_models" / name).is_file() for name in
               ("fastvideo_fasth3_8step_v2_pruned_bf16.safetensors", "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"))
    reference = any((root / "MiniMax-H3-Comfy" / "diffusion_models" / name).is_file() for name in
                    ("minimax_h3_ref2va_pruned_bf16.safetensors", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"))
    if params is None:
        if not (fast or reference):
            missing.append("a FastH3 or Ref2VA transformer")
    elif any(params.get(key) for key in ("references", "reference_videos", "reference_audios")):
        if not reference:
            missing.append("the Ref2VA transformer")
    elif not fast:
        missing.append("the FastH3 transformer")
    return missing


def require_native(kind: str, params: dict) -> None:
    import windows_library_checks as library
    if kind in library.KINDS:
        found = library.usage_status(kind)
        if not found["available"]:
            raise RouteUnavailable(found["why"])
        if mode()["enabled"]:
            library.require_scope(kind, params)
        return
    if kind in ("voice", "music") and mode()["enabled"]:
        import windows_audio_checks as audio
        found = audio.status(kind)
        if not found["qualified"]:
            raise RouteUnavailable(found["why"])
        audio.require_scope(kind, params)
        if not found["installed"]:
            raise RouteUnavailable(found["why"])
        return
    if kind == "scout":
        model = params.get("image_model") or default_image()
        found = next((row for row in image_options() if row["id"] == model), None)
        if found is None:
            raise ValueError("Unknown Windows picture model.")
        if not found["available"]:
            raise RouteUnavailable(found["why"])
        return
    if kind in ("video", "studio", "long"):
        if why := windows_cuda.h3_why_not():
            raise RouteUnavailable(why)
        if missing := video_missing(params):
            raise RouteUnavailable("H3 model files are missing: " + ", ".join(missing))
    if kind in windows_cuda.NATIVE and kind not in ("export", "soundtrack", "graphics", "hlg", "finish"):
        if mode()["enabled"]:
            raise RouteUnavailable("This native recipe has not been verified yet. Machine > Feature checks explains the available checked alternatives.")
        if why := windows_cuda.configuration_error():
            raise RouteUnavailable(why)
        if kind in ("voice", "music"):
            if kind == "voice":
                import voice
                found = voice.state()
            else:
                import music
                engine = params.get("engine") or music.default_engine()
                if engine not in music.ENGINES:
                    raise ValueError("Choose an installed music engine.")
                found = music.engine_state(engine)
            if not found["available"]:
                raise RouteUnavailable(found["why"])
        elif windows_cuda.why_not():
            raise RouteUnavailable(windows_cuda.why_not())


def gate_native_state(kind: str, state: dict, selected_mode: dict | None = None) -> dict:
    selected_mode = mode() if selected_mode is None else selected_mode
    audio_kind = "music" if kind == "music (music3)" else kind
    if (audio_kind in ("voice", "music") and (audio_kind != "music" or state.get("engine", "music3") == "music3")
            and (selected_mode["enabled"] or state.get("backend", state.get("route", "cuda")) == "cuda")):
        import windows_audio_checks as audio
        checked = audio.status(audio_kind)
        available = checked["installed"] and (checked["qualified"] or not selected_mode["enabled"])
        why = checked["why"]
        raw_blocked = not state.get("available") and state.get("state") not in ("needs_verification", "qualified")
        if checked["installed"] and raw_blocked and state.get("why"):
            available, why = False, state["why"]
        result = {**state, **checked, "available": available, "why": why,
                  "qualification_required": selected_mode["enabled"]}
        if selected_mode["enabled"] and checked["qualified"]:
            result.update(live=False, stream=False)
            if audio_kind == "voice":
                result.update(text_limit=120, reference_seconds=None)
            else:
                result["seconds"] = [10, 10]
        if available:
            result.update(why="", state="qualified" if checked["qualified"] else "available_unverified")
        return result
    installed = state.get("installed", state.get("available", False))
    result = {**state, "installed": installed, "qualified": False,
              "qualification_required": selected_mode["enabled"], "backend": state.get("backend", "native"),
              "state": state.get("state") or ("not_installed" if not installed else
                                             "available_unverified" if state.get("available") else "unavailable")}
    if why := windows_cuda.configuration_error():
        return {**result, "available": False, "state": "configuration_error", "why": why}
    if installed and selected_mode["enabled"] and (state.get("available") or state.get("state") == "needs_verification"):
        result.update(available=False, state="needs_verification",
                      why=f"The native {kind} recipe has no Windows verification check yet. See Machine > Feature checks.")
    return result


def native_status(kind: str, selected_mode: dict, base: dict | None = None) -> dict:
    if base is not None:
        return gate_native_state(kind, base, selected_mode)
    if kind in ("video", "studio", "long"):
        why = windows_cuda.h3_why_not()
        installed = not windows_cuda.why_not() and windows_cuda.paths()["tf_h3"].is_file()
        try:
            pool = int(windows_cuda.config().get("cuda_pool_mb") or 0)
        except (TypeError, ValueError):
            pool = 0
        missing = video_missing()
        state = ("not_installed" if not installed else "capacity_unknown" if pool <= 0 else
                 "insufficient_capacity" if pool < windows_cuda.MIN_POOL_MB else
                 "needs_install" if missing else "available_unverified")
        if not why and missing:
            why = "H3 model files are missing: " + ", ".join(missing)
        installed = installed and not missing
        return gate_native_state(kind, {"available": not why, "installed": installed, "why": why, "state": state,
                                       "cuda_pool_required_mb": windows_cuda.MIN_POOL_MB,
                                       "cuda_pool_capacity_mb": pool if pool > 0 else None}, selected_mode)
    why = windows_cuda.why_not()
    return gate_native_state(kind, {"available": not why, "installed": not why, "why": why,
                                    "state": "not_installed" if why else "available_unverified"}, selected_mode)
