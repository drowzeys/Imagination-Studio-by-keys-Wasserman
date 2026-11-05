"""Explicit native-image checks; observed recipes are not minimum-RAM claims."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from feature_checks import matches_qualification
import windows_cuda
import windows_features as features
import windows_runtime
from windows_qualification import gpu_runtime, gpu_version_contract, hardware_profile, verify_model_updates

KINDS = tuple(windows_runtime.IMAGE_SPECS)
SIDE = 512
COUNT = 1
CONTRACT = 1
TIMEOUT_S = 480
PROMPT = "A red ceramic cup on a wooden table in soft daylight."
SCOPES = {
    "basic": ("Basic", 512, 512, 1),
    "studio": ("Studio still", 1024, 576, 1),
    "scout": ("Scout canvas", 1280, 736, 1),
    "landscape": ("Landscape", 1344, 768, 1),
    "square": ("Square", 1024, 1024, 1),
    "portrait": ("Portrait", 768, 1344, 1),
    "large": ("Large landscape", 1664, 928, 1),
    "candidates": ("Four sequential studio candidates", 1024, 576, 4),
}
GROUPS = {
    "studio": ("Studio & scouting", ("basic", "studio", "scout", "candidates")),
    "images": ("Image presets", ("landscape", "square", "portrait")),
    "large": ("Large image", ("large",)),
    "all": ("All unchecked", tuple(SCOPES)),
}


class SafetyRefusal(RuntimeError):
    """A memory, timeout, backend or drain failure that must stop grouped checks."""


def scope_settings(scope: str = "basic") -> dict:
    if scope not in SCOPES:
        raise ValueError("Choose a registered image qualification level.")
    title, width, height, count = SCOPES[scope]
    return {"id": scope, "title": title, "width": width, "height": height, "count": count}


def receipt_key(model: str, scope: str) -> str:
    scope_settings(scope)
    return model if scope == "basic" else f"{model}-{scope}"


def title(model: str) -> str:
    return windows_runtime.IMAGE_SPECS[model][0]


def render_args(model: str) -> list[str]:
    if model not in KINDS:
        raise ValueError("Choose a registered image model.")
    if model == "qwen-full":
        return ["--model", "qwen", "--base", "--steps", "40", "--cfg", "4"]
    return ["--model", model]


def timeout_seconds(model: str, scope: str = "basic") -> int:
    return TIMEOUT_S if model == "qwen" else 1800 * scope_settings(scope)["count"]


def recipe(scope: str = "basic", model: str = "qwen") -> dict:
    import spark_generate
    settings = scope_settings(scope)
    args = SimpleNamespace(model=model, base=model == "qwen-full", width=settings["width"], height=settings["height"],
                           negative=os.environ.get("NEGATIVE", ""), steps=None, cfg=4.0)
    if model.startswith("qwen"):
        graph = spark_generate.qwen_image_workflow(args, PROMPT, 7)
    elif model in spark_generate.FLUX3:
        graph = spark_generate.flux3_workflow(args, PROMPT, 7)
    elif model in spark_generate.MOSS:
        graph = spark_generate.moss_workflow(args, PROMPT, 7)
    else:
        raise ValueError("Choose a registered image model.")
    graph["16"]["inputs"]["filename_prefix"] = "qualification"
    return {"width": args.width, "height": args.height, "count": settings["count"],
            "workflow_sha256": hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()}


def prerequisites(model: str) -> str:
    if model not in KINDS:
        raise ValueError("This native image check is not implemented.")
    if why := windows_cuda.why_not():
        return why
    missing = [path.name for path in windows_runtime.image_files(model) if not path.is_file() or path.stat().st_size == 0]
    if not missing and model.startswith("flux3k"):
        prompts = windows_runtime.model_root() / windows_runtime.IMAGE_SPECS[model][1] / "system_prompts.json"
        try:
            if not isinstance(json.loads(prompts.read_text(encoding="utf-8")).get("t2i"), str):
                return "FLUX3-K's prompt upsampler needs a valid t2i system prompt; rerun its setup."
        except (OSError, ValueError, AttributeError) as error:
            return f"FLUX3-K's prompt upsampler settings could not be read: {error}"
    return "Missing model/runtime files: " + ", ".join(missing) if missing else ""


def model_files(model: str) -> dict:
    root = windows_runtime.model_root()
    result = {}
    for path in windows_runtime.image_files(model):
        if path.is_relative_to(root):
            stat = path.stat()
            result[str(path.relative_to(root))] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


@lru_cache(maxsize=1)
def device_uuid() -> str:
    from windows_memory import single_gpu_uuid
    return single_gpu_uuid()


def fingerprint(model: str, scope: str = "basic") -> dict:
    runtime = gpu_runtime(Path(windows_cuda.python()).parent.parent)
    return {"contract": CONTRACT, "model": model, "recipe": recipe(scope, model), "models": model_files(model),
            "model_root": hashlib.sha256(str(windows_runtime.model_root().resolve()).encode()).hexdigest(),
            "backend": {"flags": (os.environ.get("SPARK_COMFY_ARGS") or "--use-ck-attention").split(),
                        "python": hashlib.sha256(str(Path(windows_cuda.python()).resolve()).encode()).hexdigest(),
                        "allocator": os.environ.get("PYTORCH_CUDA_ALLOC_CONF", ""),
                        "allocator_new": os.environ.get("PYTORCH_ALLOC_CONF", ""),
                        "image_overrides": {name: os.environ.get(name, "") for name in ("TURBO", "EXTRA", "PROMPT_FILE")}},
            "hardware": {**hardware_profile(), "device": hashlib.sha256(device_uuid().encode()).hexdigest(),
                         "recorded_cuda_pool_mb": windows_cuda.config().get("cuda_pool_mb")},
            "runtime": runtime, "abi": gpu_version_contract(runtime["python"], runtime["versions"]),
            **({"image_options": {name: os.environ.get(name, "") for name in ("STEPS", "UPSAMPLE")}}
               if model != "qwen" else {}),
            "sources": {str(Path(*parts)): hashlib.sha256(features.PACK.joinpath(*parts).read_bytes()).hexdigest()
                        for parts in (("spark", "spark_generate.py"), ("spark", "comfy_win.py"),
                                      ("app", "windows_image_checks.py"), ("scripts", "windows-comfy-check.py"),
                                      ("app", "windows_memory.py"))}}


def compatibility(value: dict) -> dict:
    return {**{key: value[key] for key in ("contract", "model", "recipe", "hardware", "abi", "backend")},
            **({"image_options": value["image_options"]} if "image_options" in value else {}),
            "model_sizes": {name: entry["size"] for name, entry in value["models"].items()}}


def scope_status(model: str, scope: str = "basic") -> dict:
    base = {"installed": False, "available": False, "qualified": False, "state": "needs_install",
            "check_available": True, "verified_recipe": {key: scope_settings(scope)[key] for key in ("width", "height", "count")}}
    try:
        if why := prerequisites(model):
            return {**base, "why": why}
        base.update(installed=True, state="needs_verification")
        previous = features.saved(receipt_key(model, scope), "")
        if previous.get("ok") is not True:
            settings = scope_settings(scope)
            return {**base, "why": f"Model verification needed: check {settings['width']}x{settings['height']}, "
                    f"{settings['count']} image(s) in Machine > Feature checks."}
        current = fingerprint(model) if scope == "basic" else fingerprint(model, scope)
        recorded = previous["fingerprint"]
        if not matches_qualification(recorded, current, compatibility(recorded), compatibility(current)):
            return {**base, "why": "The model, recipe, hardware or runtime contract changed. Check this recipe again."}
        files = {name: (entry["size"], previous["model_sha256"][name]) for name, entry in recorded["models"].items()}
        mtimes = ({name: entry["mtime_ns"] for name, entry in recorded["models"].items()}
                  if recorded.get("model_root") == current["model_root"] else {})
        verify_model_updates(windows_runtime.model_root(), files, mtimes)
        return {**base, "available": True, "qualified": True, "state": "qualified", "why": "",
                "runtime_updated": recorded != current, "hardware": recorded["hardware"]}
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
        return {**base, "state": "qualification_error", "why": f"Image verification could not be checked: {error}"}


def status(model: str) -> dict:
    levels = []
    latest = features.saved(model, "-attempt")
    for scope in SCOPES:
        found = scope_status(model, scope)
        receipt = features.saved(receipt_key(model, scope), "") if found["qualified"] else {}
        attempt = features.saved(f"{model}-{scope}", "-attempt")
        if not attempt:
            if latest.get("scope") == scope:
                attempt = latest
            else:
                attempt = next((result for result in latest.get("results", [])
                                if result.get("scope") == scope and result.get("state") in ("failed", "cancelled")), {})
        outcome = ("passed" if found["qualified"] else "cancelled" if attempt.get("state") == "cancelled"
                   else "failed" if attempt.get("error") else "not_checked")
        levels.append({**scope_settings(scope), **found,
                       "outcome": outcome, "attempt": {key: attempt[key] for key in
                           ("error", "state", "checked_at", "observations") if key in attempt},
                       "evidence": {"checked_at": receipt.get("checked_at"), "results": receipt.get("results", [])}})
    verified = [level for level in levels if level["qualified"]]
    base = next((level for level in levels if level["qualified"]), levels[0])
    scopes = [{key: level[key] for key in ("id", "title", "width", "height", "count")} for level in verified]
    next_scope = next((scope for scope in SCOPES if scope not in {level["id"] for level in verified}), "basic")
    return {**{key: value for key, value in base.items() if key not in ("id", "title", "width", "height", "count", "evidence")},
            "verified_scopes": scopes, "qualification_levels": levels, "next_scope": next_scope,
            "qualification_groups": [{"id": group, "title": title, "scopes": list(members),
                                      "remaining": [scope for scope in members if scope not in {level["id"] for level in verified}]}
                                     for group, (title, members) in GROUPS.items()],
            "runtime_updated": any(level.get("runtime_updated") for level in verified)}


def require_scope(model: str, width: int, height: int, count: int) -> None:
    found = status(model)
    if not any((scope["width"], scope["height"]) == (width, height) and 1 <= count <= scope["count"]
               for scope in found["verified_scopes"]):
        raise ValueError(f"{width}x{height}, {count} image(s) is not a verified recipe for this model. "
                         "Choose a checked size/count or check that level in Machine > Feature checks.")


def native_pressure(snapshot, previous=None) -> str:
    """Keep the native guard's floors, not the draft engine's allocator/commit caps."""
    import comfy_win
    from windows_memory import host_floor_mb
    snapshot.validate()
    if snapshot.host_low_memory:
        return "Windows reports low physical memory"
    if previous is not None and not 0 < snapshot.at - previous.at <= 2:
        raise ValueError("memory samples are out of order or stale")
    floors = (("host", snapshot.host_free_mb, max(2048, host_floor_mb(snapshot, os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()))),
              ("commit", snapshot.host_commit_limit_mb - snapshot.host_commit_used_mb, comfy_win.COMMIT_FLOOR_MB),
              ("dedicated GPU", snapshot.gpu_dedicated_free_mb, comfy_win.DEDICATED_FLOOR_MB))
    for label, free, floor in floors:
        if free < floor:
            return f"{label} memory {free} MiB is below the native {floor} MiB floor"
    return ""


def check_output(path: Path, width: int = SIDE, height: int = SIDE) -> None:
    from PIL import Image, ImageStat
    with Image.open(path) as image:
        image.load()
        if image.format != "PNG" or image.size != (width, height) or max(ImageStat.Stat(image.convert("RGB")).stddev) < 1:
            raise RuntimeError(f"The native image check did not produce a nonblank {width}x{height} PNG.")


def run_case(model: str, output: Path, observations: dict | None = None, scope: str = "basic") -> dict:
    output.mkdir(parents=True, exist_ok=True)
    target = output / "image.png"
    settings = scope_settings(scope)
    width, height, count = (settings[key] for key in ("width", "height", "count"))
    targets = [target] if count == 1 else [output / f"image_s{seed}.png" for seed in range(7, 7 + count)]
    if any(path.exists() for path in targets):
        raise RuntimeError("The image check needs a fresh output folder; an old image cannot count as a pass.")
    result = observations if observations is not None else {}
    result.update(case=f"{title(model)}: {count} image(s), {width}x{height}", scope=scope, rendered=False)
    command = [windows_cuda.python(), "-X", "utf8", str(features.PACK / "spark" / "spark_generate.py"), "image",
               *render_args(model), "--negative", os.environ.get("NEGATIVE", ""), "--prompt", PROMPT,
               "--width", str(width), "--height", str(height), "--seed", "7", "-o", str(target),
               *([] if count == 1 else ["--seeds", ",".join(str(seed) for seed in range(7, 7 + count))])]

    def validate():
        for path in targets:
            if scope == "basic":
                check_output(path)
            else:
                check_output(path, width, height)

    return observe_native(command, output, validate, result, timeout_seconds(model, scope))


def observe_native(command: list[str], output: Path, validate, result: dict, timeout_s: int) -> dict:
    """Supervise a native production subprocess under owned lifetime and fresh Windows memory readings."""
    import windows_storyboard
    from windows_process import OwnedJob

    tools = windows_storyboard.tools().checker()
    output.mkdir(parents=True, exist_ok=True)
    job = OwnedJob()
    monitor = None
    original = None
    pending_guard = None
    began = time.monotonic()
    try:
        with (output / "native-image.log").open("w+", encoding="utf-8") as log:
            try:
                monitor = tools.MemoryWatchdog(job, 0, log, wddm_device_uuid=device_uuid(), pressure_policy=native_pressure)
                monitor.budget_basis = "Native production guard plus independent host/WDDM observation"
                monitor.wait_ready()
            except RuntimeError as error:
                raise SafetyRefusal(str(error)) from error
            print("[qualification] " + result["case"], flush=True)
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            process = job.spawn(command,
                                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=0x08000000)
            while process.poll() is None:
                try:
                    monitor.check()
                except RuntimeError as error:
                    raise SafetyRefusal(str(error)) from error
                if time.monotonic() - began > timeout_s:
                    raise SafetyRefusal("The native generation check exceeded its deadline.")
                time.sleep(0.1)
            try:
                monitor.check()
            except RuntimeError as error:
                raise SafetyRefusal(str(error)) from error
            if process.returncode:
                log.flush()
                log.seek(max(0, log.tell() - 4000))
                raise SafetyRefusal(f"The native generation backend failed ({process.returncode}); the check cannot safely continue: " + log.read())
            validate()
            result["rendered"] = True
            return result
    except BaseException as error:
        original = error
        raise
    finally:
        if monitor is not None:
            monitor.stopping.set()
            try:
                monitor.check()
            except RuntimeError as error:
                if original is None:
                    pending_guard = SafetyRefusal(str(error))
            result["elapsed_s"] = round(time.monotonic() - began, 3)
            try:
                if monitor.initial is not None:
                    import comfy_win
                    result["measurements"] = {**monitor.metrics(), "cuda_floor_mb": None,
                                              "dedicated_floor_mb": comfy_win.DEDICATED_FLOOR_MB,
                                              "host_policy": "Native host/commit/dedicated floors; strict two-second observer freshness"}
                result["peak_job_commit_mb"] = job.commitment()["peak_job_commit_bytes"] / 2 ** 20
            except (OSError, RuntimeError) as error:
                result["measurement_error"] = str(error)
                print("[qualification] Could not collect all observations: " + str(error), file=sys.stderr)
        cleanup_errors = []
        for cleanup in (job.close, *((lambda: monitor.finish(check_result=False),) if monitor is not None else ())):
            try:
                cleanup()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            message = "Image check cleanup failed: " + "; ".join(cleanup_errors)
            primary = original if original is not None else pending_guard
            if primary is None:
                raise SafetyRefusal(message)
            primary.add_note(message)
            print(message, file=sys.stderr)
            if original is not None:
                failure = SafetyRefusal(str(original) + "; " + message)
                failure.add_note(message)
                raise failure from original
        if pending_guard is not None:
            raise pending_guard


def qualify(model: str, output: Path, job_id: str = "", scope: str = "basic") -> dict:
    scope_settings(scope)
    attempt = {"feature": model, "scope": scope, "job_id": job_id,
               "checked_at": datetime.now(timezone.utc).isoformat(), "ok": False}
    observations = {}
    features.save_attempt(model, attempt)
    features.save_attempt(f"{model}-{scope}", attempt)
    try:
        if why := prerequisites(model):
            raise RuntimeError(why)
        if (os.environ.get("TURBO", "1") != "1" or os.environ.get("EXTRA") or os.environ.get("PROMPT_FILE")
                or model == "qwen-full" and os.environ.get("STEPS", "40") != "40"
                or model.startswith("flux3k") and os.environ.get("UPSAMPLE", "1") != "1"):
            raise ValueError("This check uses the production model's default recipe. Clear TURBO, STEPS, UPSAMPLE, EXTRA and PROMPT_FILE overrides first.")
        before = fingerprint(model) if scope == "basic" else fingerprint(model, scope)
        result = (run_case(model, output, observations) if scope == "basic"
                  else run_case(model, output, observations, scope))
        print("[qualification] Render checked and owned backend drained; checking model identities.", flush=True)
        digests = None
        for previous_scope in SCOPES:
            if scope_status(model, previous_scope)["qualified"]:
                digests = features.saved(receipt_key(model, previous_scope), "")["model_sha256"]
                break
        if digests is None:
            digests = {}
            for name in before["models"]:
                with (windows_runtime.model_root() / name).open("rb") as handle:
                    digests[name] = hashlib.file_digest(handle, "sha256").hexdigest()
        current = fingerprint(model) if scope == "basic" else fingerprint(model, scope)
        if (not matches_qualification(before, current, compatibility(before), compatibility(current))
                or before["models"] != current["models"] or before["model_root"] != current["model_root"]):
            raise SafetyRefusal("The model or runtime changed during the check; no new pass was saved.")
        receipt = {**attempt, "ok": True, "fingerprint": before, "model_sha256": digests, "results": [result]}
        features.save_pass(receipt_key(model, scope), receipt)
        features.save_attempt(model, receipt)
        features.save_attempt(f"{model}-{scope}", receipt)
        return receipt
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        failed = {**attempt, "state": "failed", "error": str(error), "observations": observations}
        features.save_attempt(model, failed)
        features.save_attempt(f"{model}-{scope}", failed)
        raise


def qualify_group(model: str, output: Path, job_id: str, group: str) -> dict:
    if group not in GROUPS:
        raise ValueError("Choose a registered image qualification group.")
    title, scopes = GROUPS[group]
    results = []
    stopped = False
    for scope in scopes:
        if scope_status(model, scope)["qualified"]:
            results.append({"scope": scope, "state": "skipped", "reason": "Existing valid pass retained"})
            continue
        print(f"[qualification] {title}: checking {scope}.", flush=True)
        try:
            receipt = qualify(model, output / scope, job_id, scope)
            results.append({"scope": scope, "state": "passed", "results": receipt["results"]})
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
            attempt = features.saved(model, "-attempt")
            stopped = isinstance(error, (SafetyRefusal, OSError, subprocess.SubprocessError))
            results.append({"scope": scope, "state": "failed", "error": str(error),
                            "observations": attempt.get("observations", {}), "safety_stop": stopped})
            print(f"[qualification] {scope} failed: {error}", file=sys.stderr)
            if stopped:
                break
    failed = [result for result in results if result["state"] == "failed"]
    report = {"feature": model, "job_id": job_id, "group": group, "ok": not failed,
              "checked_at": datetime.now(timezone.utc).isoformat(), "results": results,
              "stopped_for_safety": stopped,
              "not_run": [scope for scope in scopes if scope not in {result["scope"] for result in results}]}
    if failed:
        report.update(error="; ".join(result["scope"] + ": " + result["error"] for result in failed),
                      observations=failed[-1]["observations"])
    features.save_attempt(model, report)
    return report
