"""Pre-download native Windows capacity policy. No weights, CUDA contexts or inference are loaded."""
from __future__ import annotations

import argparse
import json
import os
import platform
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import model_catalog
import windows_measurements

ROOT = Path(__file__).resolve().parents[1]


def requirement(part: str) -> dict:
    models = model_catalog.catalogue()["models"]
    key = part if part in models else next((key for key, row in models.items() if row["part"] == part), None)
    if key is None:
        return {"required_mb": None, "basis": "native recipe memory requirement not established by this planner",
                "source": None, "route": part}
    spec = models[key]
    routes = spec["runtime"]
    selected = ((os.environ.get("MUSIC3_WEIGHTS", "int8").strip().lower() or "int8")
                if key == "music" else next(iter(routes)))
    if selected not in routes:
        raise ValueError(f"No runtime requirement is declared for {key}/{selected}.")
    row = routes[selected]
    return {"required_mb": int(row["required_free_gib"] * 1024) if row["required_free_gib"] is not None else None,
            "basis": "existing native CUDA admission policy" if row["basis"] == "policy" else row["note"],
            "source": f"model_catalog.json:models.{key}.runtime.{selected}", "route": spec["label"] + " " + selected}


def probe() -> dict:
    """Same GPU pool type used by native engine admission; never add host/shared/dedicated pools."""
    from windows_memory import host_memory, physical_memory_mb
    result = {"physical_total_mb": physical_memory_mb(), "host_total_mb": host_memory()[0],
              "host_free_mb": host_memory()[1]}
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.free",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True,
                              check=True, timeout=5, creationflags=0x08000000 if sys.platform == "win32" else 0)
        rows = [list(map(str.strip, line.split(","))) for line in done.stdout.splitlines()]
        rows = [row for row in rows if len(row) == 5 and row[1].startswith("GPU-")]
        selected = os.environ.get("STUDIO_GPU", "").strip()
        if selected:
            rows = [row for row in rows if row[0] == selected or row[1] == selected]
        if len(rows) != 1:
            raise ValueError("The selected physical GPU is missing or ambiguous.")
        _, _, name, total, free = rows[0]
        total, free = int(total), int(free)
        if not 0 <= free <= total or total <= 0:
            raise ValueError("The driver returned invalid GPU capacity.")
        result.update(gpu_total_mb=total, gpu_free_mb=free,
                      n1x=os.environ.get("STUDIO_N1X") == "1" or "N1X" in name or "RTX Spark" in name)
        result["gpu_dedicated_total_mb"] = total
        result["architecture"] = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86-64"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(gpu_total_mb=None, gpu_free_mb=None, n1x=None,
                      error=f"GPU capacity could not be established: {type(error).__name__}: {error}")
    return result


@lru_cache(maxsize=1)
def inventory_hardware() -> dict:
    """Cache static pool identity for UI metadata; fresh admission still runs for every job/install."""
    data = probe()
    return {key: value for key, value in data.items() if not key.endswith("_free_mb")}


def model_info(key: str, *, hardware: dict | None = None, installed=False, evidence: dict | None = None) -> dict:
    size = model_catalog.size_info(key)
    hardware = inventory_hardware() if hardware is None else hardware
    fit = plan(key, hardware, installed=installed)
    result = {"download_size": size, "runtime_fit": fit}
    result["model"] = key
    result["model_metadata"] = {"label": model_catalog.model(key)["label"], "platform": "windows"}
    import shared_measurements
    profile = windows_measurements.execution_profile(model_catalog.model(key).get("feature", key))
    result["shared_measurements"] = shared_measurements.references(
        key, platform="windows", backend=profile["backend"], engine=profile["engine"], hardware=hardware)
    result["download_advice"] = shared_advice(key, hardware)
    if evidence is not None:
        result["recipe_fit"] = windows_measurements.recipe_fit(evidence)
    else:
        spec = model_catalog.model(key)
        kinds = spec.get("features") or ([spec["feature"]] if spec.get("feature") else [])
        if kinds:
            result["recipe_fit"] = windows_measurements.feature_measurements(kinds)
    return result


def cached(part: str, models: Path) -> bool:
    """File presence avoids blocking repair/reuse; it is not integrity or qualification evidence."""
    def present(path):
        return path.is_file() and path.stat().st_size > 0
    if part == "voice":
        folder = Path(os.environ.get("DRAMA3_CUDA_MODEL") or models / "drama-3")
        index = folder / "model.safetensors.index.json"
        if not present(index) or not present(folder / "config.json"):
            return False
        shards = set(json.loads(index.read_text(encoding="utf-8")).get("weight_map", {}).values())
        return bool(shards) and all((folder / name).resolve().is_relative_to(folder.resolve())
                                    and present(folder / name) for name in shards)
    manifest = model_catalog.parts()
    if part in manifest:
        return all(present(models / folder / name) for _, folder, names in manifest[part] for name in names)
    if part in model_catalog.checked_downloads():
        return all(present(models / folder / target)
                   for _, _, _, folder, target, _ in model_catalog.checked_downloads()[part])
    if part == "bolt":
        checkpoints = {key: value for key, value in model_catalog.checkpoints().items() if key != "drama-3"}
        for folder in checkpoints:
            base = models / folder
            if not present(base / "config.json"):
                return False
            if present(base / "model.safetensors"):
                continue
            index = base / "model.safetensors.index.json"
            if not present(index):
                return False
            shards = set(json.loads(index.read_text(encoding="utf-8")).get("weight_map", {}).values())
            if not shards or not all((base / name).resolve().is_relative_to(base.resolve())
                                     and present(base / name) for name in shards):
                return False
        return bool(checkpoints)
    return False


def shared_advice(model: str, hardware: dict) -> dict:
    import shared_measurements as shared
    spec = model_catalog.model(model)
    execution = windows_measurements.execution_profile(spec.get("feature", model))
    if hardware.get("n1x") is True and hardware.get("architecture") == "arm64":
        execution["architecture"] = "arm64"
        execution["topology"] = {"memory": "unified", "devices": 1, "device_family": "nvidia-n1x",
                                 "device_model": "nvidia-rtx-spark-n1x"}
    return shared.install_advice(model, hardware, execution)


def download_plan(part: str, hardware: dict, *, installed=False, automatic=False, allow_insufficient=False,
                  allow_shared_image_failures=False) -> dict:
    result = plan(part, hardware, installed=installed, automatic=automatic, allow_insufficient=allow_insufficient)
    models = [key for key, spec in model_catalog.catalogue()["models"].items() if spec["part"] == part]
    advice = [shared_advice(model, hardware) for model in models]
    result["shared_advice"] = advice
    image_override = allow_shared_image_failures and models and all(
        model_catalog.model(model)["kind"] == "image" for model in models)
    if not installed and any(row["blocked"] for row in advice) and not (allow_insufficient or image_override):
        result["action"] = "skip" if automatic else "blocked"
    return result


def plan(part: str, hardware: dict, *, installed=False, automatic=False, allow_insufficient=False) -> dict:
    need = requirement(part)
    required = need["required_mb"]
    total, free = hardware.get("gpu_total_mb"), hardware.get("gpu_free_mb")
    pools = [("GPU pool", total, free)]
    if hardware.get("n1x") is False:
        pools.append(("host RAM", hardware.get("host_total_mb"), hardware.get("host_free_mb")))
    insufficient = [(name, size) for name, size, _ in pools
                    if required is not None and type(size) is int and 0 < size < required]
    unknown = required is None or any(type(size) is not int or size <= 0 for _, size, _ in pools) or hardware.get("n1x") is None
    busy = required is not None and any(type(room) is int and room < required for _, _, room in pools)
    state = "insufficient_capacity" if insufficient else "unknown" if unknown else "temporarily_busy" if busy else "capacity_possible"
    if insufficient:
        name, size = insufficient[0]
        why = (f"{need['route']} requires about {required / 1024:g} GiB free under its current admission policy; "
               f"this {name} totals only {size / 1024:.1f} GiB. Installed system RAM is not the GPU pool.")
    elif required is None:
        why = "Runtime memory fit is unknown for this component/recipe; download bytes are not RAM needs."
    elif unknown:
        why = "Runtime capacity is unknown; no fit or incompatibility has been inferred."
    elif busy:
        why = (f"Capacity could meet the {required / 1024:g} GiB admission policy, but current free memory is lower. "
               "This is contention, not permanent incompatibility.")
    else:
        why = f"Capacity could meet the {required / 1024:g} GiB admission policy; execution, commit backing and live guards still need checking."
    action = ("reuse" if installed else "skip" if automatic and insufficient and not allow_insufficient else
              "blocked" if insufficient and not allow_insufficient else "download")
    return {"part": part, **need, "state": state, "action": action, "installed": installed,
            "why": why, "gpu_total_mb": total, "gpu_free_mb": free,
            "qualified": False, "minimum_installed_ram_mb": None,
            "note": "Policy/estimate only, not a local successful render or a minimum installed-RAM claim."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--automatic", action="store_true")
    parser.add_argument("--allow-insufficient", action="store_true")
    parser.add_argument("--allow-shared-image-failures", action="store_true")
    args = parser.parse_args(argv)
    try:
        parts = list(dict.fromkeys(part.strip() for part in args.parts.split(",") if part.strip()))
        hardware = probe()
        plans = [download_plan(part, hardware, installed=cached(part, args.models), automatic=args.automatic,
                      allow_insufficient=args.allow_insufficient,
                      allow_shared_image_failures=args.allow_shared_image_failures)
                 for part in parts]
        for row in plans:
            print(f"[setup-fit] {row['part']}: {row['state']} / {row['action']}. {row['why']}", file=sys.stderr)
            for advice in row["shared_advice"]:
                print("[setup-fit] " + advice["why"], file=sys.stderr)
        if hardware.get("error"):
            print("[setup-fit] " + hardware["error"], file=sys.stderr)
        print(json.dumps({"plans": plans, "skip_parts": [row["part"] for row in plans if row["action"] == "skip"],
                          "blocked": any(row["action"] == "blocked" for row in plans)}))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("Setup capacity planning failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
