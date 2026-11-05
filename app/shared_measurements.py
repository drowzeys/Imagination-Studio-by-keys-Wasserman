"""Portable, allowlisted observations. Shared evidence never becomes a local qualification."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import windows_features as evidence

PATH = Path(__file__).with_name("model_measurements.json")
HEX = re.compile(r"[0-9a-f]{64}")
HARDWARE = ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb", "recorded_cuda_pool_mb")
METRICS = ("elapsed_s", "peak_job_commit_mb", "peak_process_commit_mb")
POOLS = ("host_available_mb", "host_commit_headroom_mb", "dedicated_gpu_free_mb")
RECIPE = ("width", "height", "count", "workflow_sha256", "fps", "seconds", "frames", "view_size", "strength",
          "steps", "quality", "anchor", "denoise", "megapixels", "window", "silent", "clean_frame",
          "max_words", "max_characters", "max_seconds", "reference", "live", "asr", "engine", "backend",
          "weights", "sampling")


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def recipes():
    import windows_image_checks as images
    import windows_audio_checks as audio
    import windows_library_checks as library
    for model in images.KINDS:
        for scope in images.SCOPES:
            yield model, scope, images, images.receipt_key(model, scope)
    for module in (audio, library):
        for model in module.KINDS:
            yield model, "basic", module, model


def portable_identity(fingerprint: dict, hashes: dict) -> dict:
    """Hashes include execution recipe/contract, but not device identity or host-specific paths."""
    model_rows = []
    for name, row in fingerprint.get("models", {}).items():
        digest = hashes.get(name)
        if not isinstance(digest, str) or not HEX.fullmatch(digest):
            continue
        # Names are not exported: an external filename may itself contain personal information.
        model_rows.append({"sha256": digest, "bytes": row["size"]})
    recipe = {key: value for key, value in fingerprint.get("recipe", {}).items() if key in RECIPE}
    # Avoid arbitrary string values from local recipe implementations.
    recipe = {key: value for key, value in recipe.items()
              if type(value) in (bool, int, float) or key == "workflow_sha256" and isinstance(value, str) and HEX.fullmatch(value)
              or key == "sampling" and isinstance(value, dict)}
    if "sampling" in recipe:
        recipe["sampling"] = {key: value for key, value in recipe["sampling"].items()
                              if key in ("temperature", "top_p", "top_k", "steps", "dit_cfg", "ar_cfg") and number(value)}
    contracts = {key: fingerprint[key] for key in ("contract", "abi", "python_abi", "runner_contract")
                 if key in fingerprint}
    for key in ("recipe", "fixtures", "guard", "image_options"):
        if key in fingerprint:
            contracts[key + "_sha256"] = hashlib.sha256(
                json.dumps(fingerprint[key], sort_keys=True).encode()).hexdigest()
    engine = fingerprint.get("engine", {})
    if engine:
        contracts["engine_sha256"] = sorted(value for value in engine.values()
                                            if isinstance(value, str) and HEX.fullmatch(value))
    backend = fingerprint.get("backend", {})
    # Commit to backend configuration without exposing values (paths/options may be private).
    if backend:
        portable_backend = {key: value for key, value in backend.items() if key != "python"}
        contracts["backend_sha256"] = hashlib.sha256(json.dumps(portable_backend, sort_keys=True).encode()).hexdigest()
    value = {"model": fingerprint.get("model", fingerprint.get("kind")), "recipe": recipe, "contracts": contracts,
             "weights": sorted(model_rows, key=lambda row: (row["sha256"], row["bytes"]))}
    value["sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def observation(raw: dict) -> dict:
    found = {key: raw[key] for key in METRICS if number(raw.get(key))}
    found["rendered"] = raw.get("rendered") is True
    measured = raw.get("measurements", {})
    found["samples"] = measured.get("samples", 0) if type(measured.get("samples", 0)) is int else 0
    found["minimum_free_mb"] = {key: value for key, value in measured.get("minima", {}).items()
                               if key in POOLS and number(value)}
    start = measured.get("initial", {})
    if number(start.get("gpu_dedicated_total_mb")):
        found["gpu_pool_mb"] = start["gpu_dedicated_total_mb"]
    if number(start.get("host_commit_limit_mb")):
        found["commit_limit_mb"] = start["host_commit_limit_mb"]
    return found


def failure_kind(attempt: dict) -> str:
    error = str(attempt.get("error", "")).lower()
    if "commit memory" in error or "commit headroom" in error:
        return "commit_reserve"
    if "memory" in error or "memavailable" in error:
        return "memory_refusal"
    if "changed during" in error:
        return "provenance_changed"
    if attempt.get("state") == "cancelled":
        return "cancelled"
    return "execution_failed"


def export() -> dict:
    profiles = []
    for model, scope, module, key in recipes():
        receipt = evidence.saved(key, "")
        attempt = evidence.saved(model + "-" + scope if key == model and module.__name__ == "windows_image_checks" else key, "-attempt")
        # Audio/Library keep their current attempt under the feature id.
        if not attempt and scope == "basic":
            attempt = evidence.saved(model, "-attempt")
        recorded = receipt.get("fingerprint", {})
        identity = portable_identity(recorded, receipt.get("model_sha256", {})) if recorded else None
        hardware = {name: recorded["hardware"][name] for name in HARDWARE
                    if number(recorded.get("hardware", {}).get(name))}
        if receipt.get("ok") is True:
            profiles.append(make_profile(model, scope, "passed", receipt, identity, hardware))
        if attempt.get("ok") is False and attempt.get("error"):
            # Failed attempts may predate fingerprint persistence. Never fill gaps from another run.
            failed_fp = attempt.get("fingerprint", {})
            failed_identity = portable_identity(failed_fp, attempt.get("model_sha256", {})) if failed_fp else None
            profiles.append(make_profile(model, scope, failure_kind(attempt), attempt, failed_identity, {}))
    return {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "Shared observations, not local passes, minimum RAM requirements or guaranteed fit.",
            "profiles": profiles}


def make_profile(model, scope, outcome, receipt, identity, hardware):
    rows = receipt.get("results", []) if outcome == "passed" else [receipt.get("observations", {})]
    readings = [observation(row) for row in rows if isinstance(row, dict)]
    if not hardware:
        for row in rows:
            initial = row.get("measurements", {}).get("initial", {})
            hardware.update({key: initial[key] for key in HARDWARE if number(initial.get(key))})
    runtime = receipt.get("fingerprint", {}).get("runtime", {})
    versions = {key: value for key, value in runtime.get("versions", {}).items()
                if key in ("torch", "numpy", "comfy-kitchen") and isinstance(value, str)
                and re.fullmatch(r"[0-9A-Za-z.+_-]{1,80}", value)}
    checked = receipt.get("checked_at")
    try:
        checked = datetime.fromisoformat(checked).isoformat()
    except (TypeError, ValueError):
        checked = None
    value = {"model": model, "scope": scope, "outcome": outcome, "checked_at": checked,
             "identity": identity, "hardware": hardware, "runtime_versions": versions,
             "observations": readings, "interrupted": outcome != "passed" and any(not row["rendered"] for row in readings),
             "provenance_complete": bool(identity and identity["weights"])}
    value["id"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def load(path: Path = PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("profiles"), list):
        raise ValueError("Invalid shared measurement schema.")
    return data


def references(model: str, data=None) -> dict:
    rows = [row for row in (data or load())["profiles"] if row["model"] == model]
    passed = {row["scope"] for row in rows if row["outcome"] == "passed"}
    failed = {row["scope"] for row in rows if row["outcome"] != "passed"}
    return {"passed_recipes": len(passed), "failed_recipes": len(failed),
            "summary": f"Shared tests: {len(passed)} recipes passed, {len(failed)} had unsuccessful attempts on recorded hardware."
                       " Reference only; local fit is not inferred."} if rows else {}
