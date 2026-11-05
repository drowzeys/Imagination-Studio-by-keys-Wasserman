"""Windows recipe registry and local receipt normalization; never grants qualification."""
from __future__ import annotations

from datetime import datetime, timezone
import re

import shared_measurements as shared
import windows_features as evidence

HARDWARE = ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb", "recorded_cuda_pool_mb")
FEATURE_MODELS = {"scout": "sd-turbo", "sfx": "sound", "turnaround": "library-h3",
                  "upscale": "library-h3", "remove": "library-h3", "mask": "workflows"}


def model_id(feature):
    return FEATURE_MODELS.get(feature, feature)


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


def execution_profile(feature, fingerprint=None):
    """Only recorded public hardware metadata can establish device compatibility."""
    backend, engine = (("native-cuda", "tensorfold") if feature in ("voice", "music", "bolt")
                       else ("torch-cuda", "comfyui"))
    if feature in ("scout", "sfx", "foley"):
        engine = "mmaudio" if feature == "foley" else "diffusers"
    profile = shared.execution_profile("windows", backend, engine)
    recorded = (fingerprint or {}).get("measurement_profile")
    if recorded is not None:
        shared.validate_execution(recorded)
        if (recorded["platform"], recorded["backend"], recorded["engine"]) != ("windows", backend, engine):
            raise ValueError("The recorded execution profile does not match the Windows route.")
        profile = recorded
    return profile


def hardware(fingerprint):
    return {key: value for key, value in fingerprint.get("hardware", {}).items()
            if key in HARDWARE and shared.number(value)}


def observation(raw):
    found = {key: raw[key] for key in ("elapsed_s", "peak_job_commit_mb", "peak_process_commit_mb")
             if shared.number(raw.get(key))}
    found["rendered"] = raw.get("rendered") is True
    measured = raw.get("measurements", {})
    samples = measured.get("samples", 0)
    found["samples"] = samples if type(samples) is int and samples >= 0 else 0
    found["minimum_free_mb"] = {key: value for key, value in measured.get("minima", {}).items()
                               if key in shared.POOLS and shared.number(value)}
    for source, target in (("gpu_dedicated_total_mb", "gpu_pool_mb"), ("host_commit_limit_mb", "commit_limit_mb")):
        value = measured.get("initial", {}).get(source)
        if shared.number(value):
            found[target] = value
    # A case is a bounded recipe descriptor, never arbitrary prompt/path text.
    case = raw.get("case")
    try:
        shared.validate_case(case)
    except ValueError:
        case = None
    found["case"] = case
    return found


def failure_kind(attempt):
    error = str(attempt.get("error", "")).lower()
    if "commit memory" in error or "commit headroom" in error:
        return "commit_reserve"
    if "memory" in error or "memavailable" in error:
        return "memory_refusal"
    if "changed during" in error:
        return "provenance_changed"
    return "cancelled" if attempt.get("state") == "cancelled" else "execution_failed"


def make_profile(feature, scope, outcome, receipt, identity, pools):
    rows = receipt.get("results", []) if outcome == "passed" else [receipt.get("observations", {})]
    readings = [observation(row) for row in rows if isinstance(row, dict)]
    pools = {key: value for key, value in pools.items() if key in HARDWARE and shared.number(value)}
    if not pools:
        for row in rows:
            pools.update(hardware({"hardware": row.get("measurements", {}).get("initial", {})}))
    fingerprint = receipt.get("fingerprint", {})
    runtime = fingerprint.get("runtime", {})
    versions = {key: value for key, value in runtime.get("versions", {}).items()
                if key in ("torch", "numpy", "comfy-kitchen") and isinstance(value, str)
                and re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,79}", value)}
    try:
        checked = datetime.fromisoformat(receipt.get("checked_at")).isoformat()
    except (TypeError, ValueError):
        checked = None
    value = {"model": model_id(feature), "feature": feature, "scope": scope, "outcome": outcome,
             "checked_at": checked, "identity": identity, "hardware": pools,
             "execution": execution_profile(feature, fingerprint), "runtime_versions": versions,
             "observations": readings,
             "interrupted": outcome != "passed" and any(not row["rendered"] for row in readings),
             "provenance_complete": shared.complete_identity(identity), "provenance": {}}
    value["id"] = shared.digest(value)
    return value


def export():
    profiles = []
    for feature, scope, module, key in recipes():
        receipt = evidence.saved(key, "")
        attempt = evidence.saved(feature + "-" + scope if key == feature and module.__name__ == "windows_image_checks" else key, "-attempt")
        if not attempt and scope == "basic":
            attempt = evidence.saved(feature, "-attempt")
        recorded = receipt.get("fingerprint", {})
        identity = shared.portable_identity(recorded, receipt.get("model_sha256", {})) if recorded else None
        if receipt.get("ok") is True:
            profiles.append(make_profile(feature, scope, "passed", receipt, identity, hardware(recorded)))
        if attempt.get("ok") is False and attempt.get("error"):
            failed_fp = attempt.get("fingerprint", {})
            failed_identity = shared.portable_identity(failed_fp, attempt.get("model_sha256", {})) if failed_fp else None
            profiles.append(make_profile(feature, scope, failure_kind(attempt), attempt, failed_identity, hardware(failed_fp)))
    import windows_baseline_checks as baseline
    for feature in ("scout", "sfx", "foley"):
        receipt = evidence.saved(feature, "")
        if feature in baseline.KINDS and baseline.receipt_path(feature).is_file():
            receipt = baseline.evidence(feature, False)
        if receipt.get("ok") is True:
            profiles.append(make_profile(feature, "bounded", "passed", receipt, None, receipt.get("hardware", {})))
    data = {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "Shared observations, not local passes, minimum RAM requirements or guaranteed fit.",
            "profiles": profiles}
    shared.validate(data)
    return data


def normalized_levels(levels):
    normalized = []
    for level in levels:
        rows = list(level.get("evidence", {}).get("results", [])) if level.get("qualified") else []
        attempt = level.get("attempt", {})
        error = str(attempt.get("error", "")).lower()
        interrupted = level.get("outcome") == "failed" and any(
            phrase in error for phrase in ("memory watchdog", "memory guard", "out of memory", "outofmemory"))
        readings = [observation(row) for row in rows]
        if interrupted and attempt.get("observations"):
            readings.append({**observation(attempt["observations"]), "interrupted": True})
        normalized.append({"qualified": level.get("qualified", False), "outcome": level.get("outcome"),
                           "observations": readings})
    return normalized


def recipe_fit(evidence):
    levels = evidence.get("qualification_levels", [])
    result = shared.fit_summary(normalized_levels(levels))
    if result is not None:
        result.update(recipe_memory(levels))
    return result


def recipe_memory(levels):
    result = shared.memory_summary([row for level in normalized_levels(levels) for row in level["observations"]])
    pools = set()
    for level in levels:
        error = str(level.get("attempt", {}).get("error", "")).lower()
        if level.get("outcome") != "failed":
            continue
        if "commit memory" in error or "commit headroom" in error:
            pools.add("commit backing")
        elif any(word in error for word in ("dedicated gpu", "gpu memory", "cuda budget")):
            pools.add("GPU memory")
        elif any(word in error for word in ("physical memory", "host memory", "memavailable")):
            pools.add("host RAM")
    if result and pools:
        result["memory_note"] += " Stopped by " + ", ".join(sorted(pools)) + "."
    return result


def feature_measurements(kinds):
    """Historical local measurements do not grant current recipe qualification."""
    import windows_baseline_checks as baseline
    levels = []
    for kind in dict.fromkeys(kinds):
        receipt = baseline.evidence(kind, False) if kind in baseline.KINDS else evidence.saved(kind, "")
        results = receipt.get("results", []) if kind in baseline.KINDS or receipt.get("ok") is True else []
        attempt = evidence.saved(kind, "-attempt")
        levels.append({"qualified": bool(results), "evidence": {"results": results},
                       "outcome": "failed" if attempt.get("ok") is False else "", "attempt": attempt})
    memory = recipe_memory(levels)
    return {"summary": "Recorded check measurements on this machine; not a current fit guarantee.", **memory} if memory else None
