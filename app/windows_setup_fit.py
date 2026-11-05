"""Pre-download native Windows capacity policy. No weights, CUDA contexts or inference are loaded."""
from __future__ import annotations

import argparse
import json
import math
import os
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import model_catalog

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
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(gpu_total_mb=None, gpu_free_mb=None, n1x=None,
                      error=f"GPU capacity could not be established: {type(error).__name__}: {error}")
    return result


@lru_cache(maxsize=1)
def inventory_hardware() -> dict:
    """Cache static pool identity for UI metadata; fresh admission still runs for every job/install."""
    data = probe()
    return {key: value for key, value in data.items() if not key.endswith("_free_mb")}


def recipe_fit(evidence: dict) -> dict | None:
    levels = evidence.get("qualification_levels", [])
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
        if failed:
            text += f"; {failed} failed — some recipes run, not all have passed."
        else:
            text += "."
    else:
        text = f"{failed}/{len(levels)} recipes failed here; none verified."
    if unchecked:
        text += f" {unchecked} not verified."
    memory = recipe_memory(levels)
    return {"passed": passed, "failed": failed, "total": len(levels), "summary": text, **memory}


def recipe_memory(levels: list[dict]) -> dict:
    """Ranges of observed peaks, not model requirements; GPU readings include other adapter users."""
    gpu, commit, pools = [], [], set()

    def valid(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    for level in levels:
        rows = list(level.get("evidence", {}).get("results", [])) if level.get("qualified") else []
        attempt = level.get("attempt", {})
        error = str(attempt.get("error", "")).lower()
        interrupted = level.get("outcome") == "failed" and any(
            phrase in error for phrase in ("memory watchdog", "memory guard", "out of memory", "outofmemory"))
        if interrupted and attempt.get("observations"):
            rows.append(attempt["observations"])
        if interrupted:
            if "commit memory" in error or "commit headroom" in error:
                pools.add("commit backing")
            elif "dedicated gpu" in error or "gpu memory" in error or "cuda budget" in error:
                pools.add("GPU memory")
            elif "physical memory" in error or "host memory" in error or "memavailable" in error:
                pools.add("host RAM")
        for row in rows:
            partial = interrupted and row is attempt.get("observations") and not row.get("rendered", False)
            measurements = row.get("measurements", {})
            initial = measurements.get("initial", {})
            total = initial.get("gpu_dedicated_total_mb")
            free = measurements.get("minima", {}).get("dedicated_gpu_free_mb")
            if valid(total) and total > 0 and valid(free) and free <= total:
                gpu.append((total - free, partial))
            peak = row.get("peak_job_commit_mb")
            if valid(peak):
                commit.append((peak, partial))

    def span(values):
        if not values:
            return None
        return {"min_mb": min(value for value, _ in values), "max_mb": max(value for value, _ in values),
                "incomplete": any(partial for _, partial in values)}

    ranges = {"gpu_usage": span(gpu), "job_commitment": span(commit)}
    labels = []
    for key, label in (("gpu_usage", "GPU usage (adapter-wide)"), ("job_commitment", "owned job commitment")):
        value = ranges[key]
        if value:
            labels.append(f"{label}: {value['min_mb'] / 1024:.1f}–{value['max_mb'] / 1024:.1f}"
                          f"{'+' if value['incomplete'] else ''} GiB")
    if not labels:
        return {}
    summary = "Observed " + " · ".join(labels) + "."
    return {"memory_ranges": ranges, "memory_summary": summary,
            "memory_note": "Ranges are observed recipe peaks, not minimum requirements. "
                           "GPU usage includes other apps. + means a memory-limited run ended before completion; "
                           "the full-run peak may be higher. GPU and commitment pools overlap; do not add them."
                           + (" Stopped by " + ", ".join(sorted(pools)) + "." if pools else "")}


def model_info(key: str, *, hardware: dict | None = None, installed=False, evidence: dict | None = None) -> dict:
    size = model_catalog.size_info(key)
    fit = plan(key, inventory_hardware() if hardware is None else hardware, installed=installed)
    result = {"download_size": size, "runtime_fit": fit}
    import shared_measurements
    result["shared_measurements"] = shared_measurements.references(key)
    if evidence is not None:
        result["recipe_fit"] = recipe_fit(evidence)
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
    args = parser.parse_args(argv)
    try:
        parts = list(dict.fromkeys(part.strip() for part in args.parts.split(",") if part.strip()))
        hardware = probe() if any(requirement(part)["required_mb"] is not None for part in parts) else {}
        plans = [plan(part, hardware, installed=cached(part, args.models), automatic=args.automatic,
                      allow_insufficient=args.allow_insufficient)
                 for part in parts]
        for row in plans:
            print(f"[setup-fit] {row['part']}: {row['state']} / {row['action']}. {row['why']}", file=sys.stderr)
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
