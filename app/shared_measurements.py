"""Platform-neutral, privacy-allowlisted observations. References never qualify a machine."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

PATH = Path(__file__).with_name("model_measurements.json")
HEX = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
HARDWARE = ("physical_total_mb", "host_total_mb", "gpu_dedicated_total_mb", "recorded_cuda_pool_mb",
            "unified_total_mb")
POOLS = ("host_available_mb", "host_commit_headroom_mb", "dedicated_gpu_free_mb", "unified_available_mb")
METRICS = ("elapsed_s", "peak_job_commit_mb", "peak_process_commit_mb", "gpu_pool_mb", "commit_limit_mb",
           "host_pool_mb", "unified_pool_mb")
RECIPE = ("width", "height", "count", "workflow_sha256", "fps", "seconds", "frames", "view_size", "strength",
          "steps", "quality", "anchor", "denoise", "megapixels", "window", "silent", "clean_frame",
          "max_words", "max_characters", "max_seconds", "reference", "live", "asr", "engine", "backend",
          "weights", "sampling")
OUTCOMES = ("passed", "commit_reserve", "memory_refusal", "provenance_changed", "cancelled", "execution_failed")


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def recipe_descriptor(raw):
    if not isinstance(raw, dict):
        return {}
    found = {key: value for key, value in raw.items() if key in RECIPE and
             (type(value) is bool or number(value) or
              key == "workflow_sha256" and isinstance(value, str) and HEX.fullmatch(value))}
    if isinstance(raw.get("sampling"), dict):
        found["sampling"] = {key: value for key, value in raw["sampling"].items()
                             if key in ("temperature", "top_p", "top_k", "steps", "dit_cfg", "ar_cfg") and number(value)}
    return found


def portable_identity(fingerprint, hashes):
    models = fingerprint.get("models", {})
    weights = [{"sha256": hashes[name], "bytes": row["size"]} for name, row in models.items()
               if isinstance(hashes.get(name), str) and HEX.fullmatch(hashes[name])
               and type(row.get("size")) is int and row["size"] >= 0]
    contracts = {}
    for key in ("contract", "runner_contract"):
        if type(fingerprint.get(key)) is int and fingerprint[key] >= 0:
            contracts[key] = fingerprint[key]
    abi = fingerprint.get("abi", {})
    if isinstance(abi, dict):
        contracts["abi"] = {key: value for key, value in abi.items()
                            if key in ("contract", "torch_major", "numpy_major", "cuda_major") and type(value) is int and value >= 0
                            or key == "python_abi" and isinstance(value, str) and re.fullmatch(r"\d+\.\d+", value)}
    if isinstance(fingerprint.get("python_abi"), str) and re.fullmatch(r"\d+\.\d+", fingerprint["python_abi"]):
        contracts["python_abi"] = fingerprint["python_abi"]
    for key in ("recipe", "fixtures", "guard", "image_options"):
        if key in fingerprint:
            contracts[key + "_sha256"] = digest(fingerprint[key])
    engine = fingerprint.get("engine", {})
    if engine:
        contracts["engine_sha256"] = sorted(value for value in engine.values()
                                            if isinstance(value, str) and HEX.fullmatch(value))
    backend = fingerprint.get("backend", {})
    if backend:
        contracts["backend_sha256"] = digest({key: value for key, value in backend.items() if key != "python"})
    feature = fingerprint.get("model", fingerprint.get("kind"))
    value = {"feature": feature if isinstance(feature, str) and TOKEN.fullmatch(feature) else "unknown",
             "recipe": recipe_descriptor(fingerprint.get("recipe", {})), "contracts": contracts,
             "weights": sorted(weights, key=lambda row: (row["sha256"], row["bytes"])),
             "weights_expected": len(models), "weights_complete": bool(models) and len(weights) == len(models),
             "engine_expected": len(engine)}
    value["sha256"] = digest(value)
    return value


def execution_profile(platform, backend, engine):
    return {"platform": platform, "backend": backend, "engine": engine, "architecture": "unknown",
            "topology": {"memory": "unknown", "devices": None, "device_family": "unknown", "device_model": "unknown"}}


def validate_execution(value):
    if not isinstance(value, dict) or set(value) != {"platform", "backend", "engine", "architecture", "topology"}:
        raise ValueError("Invalid execution profile.")
    if value["platform"] not in ("windows", "macos", "linux", "unknown"):
        raise ValueError("Invalid platform.")
    allowed = {"backend": ("torch-cuda", "torch-metal", "mlx", "native-cuda", "native-metal", "cpu", "unknown"),
               "engine": ("comfyui", "tensorfold", "mlx", "diffusers", "mmaudio", "ffmpeg", "tesseract", "unknown"),
               "architecture": ("x86-64", "arm64", "unknown")}
    if any(value[key] not in choices for key, choices in allowed.items()):
        raise ValueError("Invalid execution backend/architecture.")
    topology = value["topology"]
    if not isinstance(topology, dict) or set(topology) != {"memory", "devices", "device_family", "device_model"}:
        raise ValueError("Invalid memory topology.")
    if topology["memory"] not in ("unified", "discrete", "host", "unknown"):
        raise ValueError("Invalid memory pool.")
    if topology["devices"] is not None and (type(topology["devices"]) is not int or not 1 <= topology["devices"] <= 8):
        raise ValueError("Invalid device count.")
    if topology["device_family"] not in ("unknown", "apple-m1", "apple-m2", "apple-m3", "apple-m4", "apple-m5",
                                         "nvidia-blackwell", "nvidia-hopper", "nvidia-ada", "nvidia-ampere",
                                         "nvidia-gb10", "nvidia-n1x"):
        raise ValueError("Invalid public device family.")
    if topology["device_model"] not in (
        "unknown", "nvidia-dgx-spark-gb10", "nvidia-rtx-spark-n1x", "nvidia-rtx-pro-6000-blackwell",
        "nvidia-rtx-5090", "nvidia-rtx-4090", "nvidia-rtx-3090", "nvidia-h100", "nvidia-a100",
        *(f"apple-m{generation}{suffix}" for generation in range(1, 6) for suffix in ("", "-pro", "-max", "-ultra"))):
        raise ValueError("Invalid public device model.")


def compatible(left, right):
    """Unknown historical hardware is never proof of same-device compatibility."""
    for value in (left, right):
        validate_execution(value)
        if any(value[key] == "unknown" for key in ("platform", "backend", "engine", "architecture")):
            return False
        if any(item in ("unknown", None) for item in value["topology"].values()):
            return False
    return left == right


def complete_identity(identity):
    return bool(identity and identity["weights_complete"] and identity["engine_expected"] is not None
                and identity["engine_expected"] == len(identity["contracts"].get("engine_sha256", []))
                and "recipe_sha256" in identity["contracts"] and "contract" in identity["contracts"]
                and ("abi" in identity["contracts"] or "python_abi" in identity["contracts"]))


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Invalid shared measurement fields.")


def _hash(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("Invalid content hash.")


def _token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError("Invalid public identifier.")


def _timestamp(value):
    from datetime import datetime
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid measurement timestamp.") from error


def validate_case(value):
    if value is None:
        return
    if isinstance(value, dict) and value == recipe_descriptor(value):
        return
    if isinstance(value, str) and (
        re.fullmatch(r"[1-5]s-(under|replace)", value)
        or re.fullmatch(r"[1-9]\d? 512x512 storyboard drafts", value)
        or re.fullmatch(r"(Qwen-Image 2\.1 (Turbo|Full)|FLUX3-K (Fast|Full)|MOSS-Image): [1-9]\d? image\(s\), \d{2,4}x\d{2,4}", value)
        or value in ("Short local Voice", "MiniMax-Music3: 10-second instrumental", "Five-second sound effect",
                     "Turnaround sheet on bundled baker fixture", "Upscale on bundled baker fixture",
                     "Find a person on bundled baker fixture", "Remove a person on bundled baker fixture",
                     "Character Swap on bundled baker fixture")):
        return
    raise ValueError("Invalid observation case.")


def validate(data):
    _keys(data, ("schema", "generated_at", "purpose", "profiles"))
    if data["schema"] != 1 or not isinstance(data["profiles"], list):
        raise ValueError("Invalid shared measurement schema.")
    _timestamp(data["generated_at"])
    if data["purpose"] != "Shared observations, not local passes, minimum RAM requirements or guaranteed fit.":
        raise ValueError("Invalid measurement purpose.")
    seen = set()
    import model_catalog
    models = set(model_catalog.catalogue()["models"]) | {"sd-turbo"}
    features = models | {"scout", "sfx", "turnaround", "upscale", "remove", "mask"}
    for row in data["profiles"]:
        _keys(row, ("id", "model", "feature", "scope", "outcome", "checked_at", "identity", "hardware",
                    "execution", "runtime_versions", "observations", "interrupted", "provenance_complete", "provenance"))
        _hash(row["id"])
        if row["id"] in seen:
            raise ValueError("Duplicate observation profile.")
        seen.add(row["id"])
        for key in ("model", "feature", "scope"):
            _token(row[key])
        if row["model"] not in models or row["feature"] not in features:
            raise ValueError("Unknown canonical model or feature.")
        spec = model_catalog.model(row["model"]) if row["model"] != "sd-turbo" else {"feature": "scout"}
        linked = spec.get("features") or [spec.get("feature", row["model"])]
        if row["feature"] not in linked:
            raise ValueError("The feature does not use this canonical model.")
        if row["outcome"] not in OUTCOMES or type(row["interrupted"]) is not bool or type(row["provenance_complete"]) is not bool:
            raise ValueError("Invalid observation outcome.")
        if row["checked_at"] is not None:
            _timestamp(row["checked_at"])
        validate_execution(row["execution"])
        _keys(row["hardware"], (), HARDWARE)
        if not all(number(value) for value in row["hardware"].values()):
            raise ValueError("Invalid hardware capacity.")
        _keys(row["runtime_versions"], (), ("torch", "numpy", "comfy-kitchen"))
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,79}", value)
               for value in row["runtime_versions"].values()):
            raise ValueError("Invalid runtime version.")
        provenance = row["provenance"]
        _keys(provenance, (), ("source_profile_id", "source_model", "source_identity_sha256", "source_provenance_complete"))
        for key in ("source_profile_id", "source_identity_sha256"):
            if key in provenance:
                _hash(provenance[key])
        if "source_model" in provenance:
            _token(provenance["source_model"])
            if provenance["source_model"] not in features:
                raise ValueError("Unknown historical feature.")
        if "source_provenance_complete" in provenance and type(provenance["source_provenance_complete"]) is not bool:
            raise ValueError("Invalid historical provenance.")
        identity = row["identity"]
        if identity is not None:
            _keys(identity, ("feature", "recipe", "contracts", "weights", "sha256", "weights_expected", "weights_complete",
                             "engine_expected"))
            _token(identity["feature"])
            if identity["feature"] != row["feature"]:
                raise ValueError("The identity names a different feature.")
            if identity["recipe"] != recipe_descriptor(identity["recipe"]):
                raise ValueError("Invalid recipe descriptor.")
            contracts = identity["contracts"]
            _keys(contracts, (), ("contract", "runner_contract", "abi", "python_abi", "recipe_sha256", "fixtures_sha256",
                                 "guard_sha256", "image_options_sha256", "engine_sha256", "backend_sha256"))
            for key, value in contracts.items():
                if key == "engine_sha256":
                    if not isinstance(value, list):
                        raise ValueError("Invalid engine hashes.")
                    for item in value:
                        _hash(item)
                elif key.endswith("_sha256"):
                    _hash(value)
                elif key == "abi":
                    _keys(value, (), ("contract", "python_abi", "torch_major", "numpy_major", "cuda_major"))
                    for name, item in value.items():
                        if name == "python_abi":
                            if not isinstance(item, str) or not re.fullmatch(r"\d+\.\d+", item):
                                raise ValueError("Invalid Python ABI.")
                        elif type(item) is not int or item < 0:
                            raise ValueError("Invalid ABI.")
                elif key == "python_abi":
                    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+", value):
                        raise ValueError("Invalid Python ABI.")
                elif type(value) is not int or value < 0:
                    raise ValueError("Invalid contract.")
            if not isinstance(identity["weights"], list):
                raise ValueError("Invalid weight identities.")
            for weight in identity["weights"]:
                _keys(weight, ("sha256", "bytes"))
                _hash(weight["sha256"])
                if type(weight["bytes"]) is not int or weight["bytes"] < 0:
                    raise ValueError("Invalid weight byte size.")
            expected = identity["weights_expected"]
            if expected is not None and (type(expected) is not int or expected < len(identity["weights"])):
                raise ValueError("Invalid expected weight count.")
            complete = expected is not None and expected > 0 and expected == len(identity["weights"])
            if type(identity["weights_complete"]) is not bool or identity["weights_complete"] != complete:
                raise ValueError("Incomplete weight provenance.")
            engine_expected = identity["engine_expected"]
            if engine_expected is not None and (type(engine_expected) is not int or
                                               engine_expected < len(contracts.get("engine_sha256", []))):
                raise ValueError("Invalid expected engine count.")
            _hash(identity["sha256"])
            if identity["sha256"] != digest({key: value for key, value in identity.items() if key != "sha256"}):
                raise ValueError("Invalid identity digest.")
        if row["provenance_complete"] != complete_identity(identity):
            raise ValueError("Invalid provenance completeness.")
        if not isinstance(row["observations"], list):
            raise ValueError("Invalid observations.")
        for reading in row["observations"]:
            _keys(reading, ("rendered", "samples", "minimum_free_mb", "case"), METRICS)
            if type(reading["rendered"]) is not bool or type(reading["samples"]) is not int or reading["samples"] < 0:
                raise ValueError("Invalid sample outcome.")
            validate_case(reading["case"])
            if any(not number(reading[key]) for key in METRICS if key in reading):
                raise ValueError("Invalid numeric reading.")
            _keys(reading["minimum_free_mb"], (), POOLS)
            if not all(number(value) for value in reading["minimum_free_mb"].values()):
                raise ValueError("Invalid memory reading.")
        if row["id"] != digest({key: value for key, value in row.items() if key != "id"}):
            raise ValueError("Invalid profile digest.")
    return data


def load(path=PATH):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def references(model, data=None, *, platform, backend=None, engine=None, feature=None, hardware=None):
    rows = [row for row in (data if data is not None else load())["profiles"]
            if row["model"] == model and row["execution"]["platform"] == platform
            and (backend is None or row["execution"]["backend"] == backend)
            and (engine is None or row["execution"]["engine"] == engine)
            and (feature is None or row["feature"] == feature)]
    if hardware is not None:
        rows = [row for row in rows if all(
            number(hardware.get(key)) and number(row["hardware"].get(key))
            and hardware[key] <= row["hardware"][key]
            for key in ("physical_total_mb", "gpu_dedicated_total_mb"))]
    passed = {(row["feature"], row["scope"]) for row in rows if row["outcome"] == "passed"}
    failed = {(row["feature"], row["scope"]) for row in rows if row["outcome"] != "passed"}
    observations = [{**reading, "interrupted": row.get("interrupted", False)}
                    for row in rows for reading in row.get("observations", [])]
    memory = memory_summary(observations)
    capacities = sorted({row.get("hardware", {}).get("physical_total_mb") for row in rows
                         if number(row.get("hardware", {}).get("physical_total_mb"))})
    hardware_note = ("Recorded on " + "/".join(f"{size / 1024:g}" for size in capacities)
                     + " GiB installed RAM. ") if capacities else ""
    return {"passed_recipes": len(passed), "failed_recipes": len(failed), **memory,
            "summary": hardware_note + "Reference measurements; not a local qualification."} if rows else {}


def install_advice(model, hardware, execution, data=None):
    """Failed shared recipes gate downloads only on the same recorded execution/capacity profile."""
    rows = [row for row in (data if data is not None else load())["profiles"] if row["model"] == model]
    def comparable(recorded):
        validate_execution(recorded)
        validate_execution(execution)
        return (all(recorded[key] == execution[key] and execution[key] != "unknown"
                    for key in ("platform", "backend", "engine", "architecture"))
                and execution["topology"]["memory"] != "unknown"
                and recorded["topology"]["memory"] == execution["topology"]["memory"]
                and recorded["topology"]["device_family"] == execution["topology"]["device_family"]
                and execution["topology"]["device_family"] != "unknown"
                and recorded["topology"]["devices"] == execution["topology"]["devices"] == 1)

    matching = [row for row in rows if comparable(row["execution"])
                and all(number(hardware.get(key)) and number(row["hardware"].get(key))
                        and 0 < hardware[key] <= row["hardware"][key]
                        for key in ("physical_total_mb", "gpu_dedicated_total_mb"))]
    failures = [row for row in matching if row["outcome"] in
                ("commit_reserve", "memory_refusal", "execution_failed")]
    passes = [row for row in matching if row["outcome"] == "passed"
              and all(hardware[key] == row["hardware"][key]
                      for key in ("physical_total_mb", "gpu_dedicated_total_mb"))]
    if failures and not passes:
        limited = number(hardware.get("physical_total_mb")) and 0 < hardware["physical_total_mb"] < 128 * 1024
        return {"state": "matching_failures", "blocked": limited,
                "why": "Not recommended for this machine: no tested preset completed on a comparable "
                       "configuration with at least this much RAM and GPU memory. "
                       + ("Below 128 GiB installed RAM, an explicit download override is required to avoid "
                          "large downloads that are unlikely to be useful. " if limited else
                          "At 128 GiB or more, this is a warning only; download is allowed. ")
                       + "Live guards remain unchanged."}
    if failures and passes:
        return {"state": "partially_useful", "blocked": False,
                "why": "Some presets should be usable; larger presets may exceed this machine's memory. "
                       "Local qualification and live guards still apply."}
    if passes:
        return {"state": "matching_observations", "blocked": False,
                "why": "Tested presets ran on comparable memory capacity; local checks still apply."}
    return {"state": "no_matching_metadata", "blocked": False,
            "why": f"WARNING: {model} has no shared measurement metadata for this hardware/backend and capacity "
                   "configuration. Runtime fit is unverified; download size is not runtime memory."}


def memory_summary(observations):
    """Keep distinct, overlapping pools separate; adapter usage is not owned allocation."""
    values = {key: [] for key in ("gpu_usage", "job_commitment", "host_usage", "unified_usage")}
    for row in observations:
        partial = row.get("interrupted", False) and not row["rendered"]
        for pool, free, key in (("gpu_pool_mb", "dedicated_gpu_free_mb", "gpu_usage"),
                                ("host_pool_mb", "host_available_mb", "host_usage"),
                                ("unified_pool_mb", "unified_available_mb", "unified_usage")):
            total, remaining = row.get(pool), row.get("minimum_free_mb", {}).get(free)
            if number(total) and total > 0 and number(remaining) and remaining <= total:
                values[key].append((total - remaining, partial))
        if number(row.get("peak_job_commit_mb")):
            values["job_commitment"].append((row["peak_job_commit_mb"], partial))
    ranges = {key: ({"min_mb": min(value for value, _ in rows), "max_mb": max(value for value, _ in rows),
                     "incomplete": any(partial for _, partial in rows)} if rows else None)
              for key, rows in values.items()}
    labels = []
    for key, label in (("gpu_usage", "GPU usage (adapter-wide)"), ("job_commitment", "owned job commitment"),
                       ("host_usage", "host RAM usage (system-wide)"), ("unified_usage", "unified memory usage (system-wide)")):
        value = ranges[key]
        if value:
            labels.append(f"{label}: {value['min_mb'] / 1024:.1f}–{value['max_mb'] / 1024:.1f}"
                          f"{'+' if value['incomplete'] else ''} GiB")
    if not labels:
        return {}
    return {"memory_ranges": ranges, "memory_summary": "Observed " + " · ".join(labels) + ".",
            "memory_note": "Ranges are observed recipe peaks, not minimum requirements. "
                           "GPU usage includes other apps. + means a memory-limited run ended before completion; "
                           "the full-run peak may be higher. GPU, host, unified and commitment pools overlap; do not add them."}


def fit_summary(levels):
    """A local qualification summary, separate from historical shared references."""
    if not levels:
        return None
    passed = sum(bool(level.get("qualified")) for level in levels)
    failed = sum(not level.get("qualified") and level.get("outcome") == "failed" for level in levels)
    unchecked = len(levels) - passed - failed
    if not passed and not failed:
        return None
    if passed == len(levels):
        text = f"All {passed} recipes verified on this machine."
    elif passed:
        text = f"{passed}/{len(levels)} recipes verified here"
        text += f"; {failed} failed — some recipes run, not all have passed." if failed else "."
    else:
        text = f"{failed}/{len(levels)} recipes failed here; none verified."
    if unchecked:
        text += f" {unchecked} not verified."
    readings = [row for level in levels for row in level.get("observations", [])]
    return {"passed": passed, "failed": failed, "total": len(levels), "summary": text, **memory_summary(readings)}
