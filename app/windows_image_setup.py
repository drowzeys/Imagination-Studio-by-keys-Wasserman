"""Weights-only Windows image setup policy; planning never probes hardware.

Auto deliberately chooses the bounded SD-Turbo route. The Windows ARM64 N1X
128 GB Qwen Turbo reference (8 steps, 864x480, pinned Torch/ComfyUI) is a
reference, not a model minimum or a qualification for other configurations.
Matching failed shared recipes require explicit download acknowledgment; missing shared metadata warns only.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import model_catalog

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("auto", "sd-turbo", "qwen", "qwen-full", "flux3k-fast", "flux3k-full", "moss-image")
NATIVE_PARTS = {part: tuple(key for key, row in model_catalog.catalogue()["models"].items()
                           if row.get("kind") == "image" and row["part"] == part)
                for part in dict.fromkeys(row["part"] for row in model_catalog.catalogue()["models"].values()
                                          if row.get("kind") == "image")}
PART_FOR_MODEL = {model: part for part, models in NATIVE_PARTS.items() for model in models}
AUTO_WHY = ("Auto selects SD-Turbo: native image memory fit is unknown without a matched "
            "runtime/hardware profile. RAM size alone does not establish fit.")


@lru_cache(maxsize=1)
def downloader():
    spec = importlib.util.spec_from_file_location("windows_image_model_downloader",
                                                ROOT / "scripts" / "windows-cuda-setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_files(model: str) -> list[tuple[str, str, list[str]]]:
    """Use the existing downloader's manifest, without fetching another variant."""
    return [(row["repo"], row["folder"], row["files"]) for row in model_catalog.download_rows(model)]


def complete_file(path: Path) -> bool:
    """Check cached file structure/length, not model identity or render qualification."""
    try:
        size = path.stat().st_size
        if path.suffix == ".json":
            return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
        if path.suffix != ".safetensors":
            return False
        with path.open("rb") as handle:
            length = int.from_bytes(handle.read(8), "little")
            if not 2 <= length <= min(16 << 20, size - 8):
                return False
            header = json.loads(handle.read(length))
        spans = []
        for name, tensor in header.items():
            if name == "__metadata__":
                continue
            start, end = tensor["data_offsets"]
            if (not tensor["dtype"] or not isinstance(tensor["shape"], list)
                    or type(start) is not int or type(end) is not int or not 0 <= start < end):
                return False
            spans.append((start, end))
        cursor = 0
        for start, end in sorted(spans):
            if start != cursor:
                return False
            cursor = end
        return bool(spans) and cursor == size - 8 - length
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def installed_models(base: Path, models: list[str]) -> set[str]:
    installed = set()
    for model in models:
        if model == "sd-turbo":
            import windows_storyboard
            folder = Path(os.environ.get("STUDIO_WINDOWS_IMAGE_DIR") or base / "SD-Turbo")
            try:
                cached = all((folder / name).stat().st_size == size and complete_file(folder / name)
                             for name, (size, _) in windows_storyboard.FILES.items())
                cached = cached and complete_file(folder / "model_index.json") and (folder / "LICENSE.md").stat().st_size > 0
            except OSError:
                cached = False
        else:
            cached = all(complete_file(base / folder / name)
                         for _, folder, names in model_files(model) for name in names)
        if cached:
            installed.add(model)
    return installed


def notices(model: str) -> list[str]:
    if model in ("qwen", "qwen-full"):
        return ["Qwen-Image-2.1: approximately 33 GB of weights for Turbo (Full differs); "
                "download size is NOT runtime RAM. Qwen Research License, non-commercial."]
    if model.startswith("flux3k-"):
        return ["FLUX3-K: large weight download (size not established here), FLUX Kommunity License; "
                "runtime memory fit is unknown."]
    if model == "moss-image":
        return ["MOSS-Image: large weight download (size not established here), Apache 2.0; "
                "runtime memory fit is unknown."]
    return ["SD-Turbo: approximately 2.6 GB of official FP16 weights; Stability AI Community License. "
            "Commercial use has registration and revenue conditions. Download size is NOT runtime RAM."]


def plan(model: str = "auto", *, installed: set[str] | None = None,
         no_download: bool = False, allow_unverified: bool = False, hardware: dict | None = None) -> dict:
    if model not in MODELS:
        raise ValueError(f"Unknown image model: {model}")
    selected = "sd-turbo" if model == "auto" else model
    cached = selected in (installed or set())
    native = selected != "sd-turbo"
    from windows_setup_fit import shared_advice
    advice = (shared_advice(selected, hardware or {}) if native else
              {"state": "bounded", "blocked": False, "why": "SD-Turbo retains its bounded capacity preflight."})
    needs_ack = native and advice["blocked"] and not cached and not no_download and not allow_unverified
    return {"requested_model": model, "selected_model": selected,
            "why": AUTO_WHY if model == "auto" else "Explicit model retained; no silent substitution.",
            "model_files_complete": cached, "download": not cached and not no_download,
            "native_fit": "unknown" if native else "bounded 512x512 route; live check still required",
            "requires_acknowledgment": needs_ack, "allowed": not needs_ack,
            "prerequisites": ["native ARM64 GPU environment", "pinned ComfyUI"],
            "cache_check": "file structure/length only; not identity verification or render qualification",
            "notices": [*notices(selected), advice["why"]],
            "shared_advice": advice, "qualification": "not performed; weights setup only"}


def guard_parts(parts: list[str], base: Path, *, allow_unverified: bool = False, hardware: dict | None = None) -> list[dict]:
    models = list(dict.fromkeys(model for part in parts for model in NATIVE_PARTS.get(part, ())))
    installed = installed_models(base, models)
    return [plan(model, installed=installed, allow_unverified=allow_unverified, hardware=hardware) for model in models]


def validate_sd_capacity(hardware: dict) -> None:
    """Mirror the existing storyboard preflight caps/reserves, not inferred model RAM."""
    floor = hardware.get("host_floor_mb", 0)
    if type(floor) is not int or floor < 0:
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer when set")
    if hardware["platform"] != "win32" or hardware["architecture"].upper() not in ("ARM64", "AARCH64"):
        raise RuntimeError("The bounded SD-Turbo installer requires native Windows ARM64.")
    if hardware["low_memory"] or hardware["host_free_mb"] <= 0 or hardware["host_free_mb"] < floor:
        raise RuntimeError("SD-Turbo setup refused: Windows physical memory reserve is unavailable.")
    if hardware["commit_free_mb"] < 16384 + 2048:
        raise RuntimeError("SD-Turbo setup refused: its existing 16 GiB job cap plus 2 GiB reserve does not fit commit headroom.")
    if hardware["gpu_free_mb"] < 12288 + 2048:
        raise RuntimeError("SD-Turbo setup refused: its existing 12 GiB GPU cap plus 2 GiB reserve does not fit.")


def native_device() -> str:
    if sys.platform != "win32" or platform.machine().upper() not in ("ARM64", "AARCH64"):
        raise RuntimeError("The image installer requires native Windows ARM64.")
    from windows_memory import single_gpu_uuid
    return single_gpu_uuid()


def preflight_sd() -> None:
    from windows_memory import DedicatedMemory, WindowsSignals, host_memory

    device = native_device()
    floor = os.environ.get("SPARK_MEMORY_FLOOR_MB", "").strip()
    if floor and (not floor.isdigit() or int(floor) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer")
    with DedicatedMemory(device) as memory:
        _, free = memory.read()
    with WindowsSignals() as signals:
        used, limit, _, low = signals.read()
    validate_sd_capacity({"platform": sys.platform, "architecture": platform.machine(),
                          "host_free_mb": host_memory()[1], "commit_free_mb": limit - used,
                          "gpu_free_mb": free, "low_memory": low, "host_floor_mb": int(floor or "0")})


def check_runtime(component: str, home: Path) -> None:
    spec = importlib.util.spec_from_file_location("windows_image_install_audit",
                                                ROOT / "scripts" / "windows-install-check.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    manifest = "requirements-windows-gpu.txt" if component == "gpu" else "requirements-windows-comfy-constraints.txt"
    audit.dependencies(home / "gpu-venv" / "Scripts" / "python.exe", ROOT / "app" / manifest, arm64=True)
    if component == "comfy":
        # Native setup owns untracked node/config files; do not mistake those for edits to pinned source.
        source = home / "ComfyUI"
        head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        if head != "b9b290e43f1eb379946dfeb013faf9bbefa1a618" or dirty:
            raise RuntimeError("Existing ComfyUI is not clean at the required pin; it was not changed.")


def check_model_access(models: list[str], base: Path) -> None:
    """Check missing components' access with HEAD requests, never weight downloads."""
    if not models:
        return
    from huggingface_hub import get_hf_file_metadata, hf_hub_url
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

    checked = set()
    for model in models:
        for repo, folder, names in model_files(model):
            missing = next((name for name in names if not complete_file(base / folder / name)), None)
            if missing is None or repo in checked:
                continue
            try:
                get_hf_file_metadata(hf_hub_url(repo, missing), timeout=10)
            except HfHubHTTPError as error:
                status = error.response.status_code if error.response is not None else None
                if isinstance(error, GatedRepoError) or status in (401, 403):
                    raise RuntimeError(
                        f"Model access denied: https://huggingface.co/{repo}. "
                        "Accept that repository's access/license terms while signed into Hugging Face, then use "
                        "the same account's token with read permission for gated repositories. "
                        "Sign in with the GPU environment's hf.exe auth login; an HF_TOKEN environment variable "
                        "takes precedence over the saved login. No model download was started.") from None
                raise RuntimeError(f"Model access preflight failed for {repo} (HTTP {status}); "
                                   "no model download was started.") from None
            checked.add(repo)


def download_model(model: str, base: Path) -> int:
    module = downloader()
    rows = model_files(model)
    for _, folder, names in rows:
        for name in names:
            path = base / folder / name
            if path.is_file() and path.stat().st_size and not complete_file(path):
                raise RuntimeError(f"Incomplete cached image component: {path.name}. "
                                   "Move it aside explicitly before retrying; it was not overwritten.")
    check_model_access([model], base)
    # A private in-process manifest selection: never modify the downloader file or another process.
    module.PARTS = {**module.PARTS, "selected-image": rows}
    result = module.models(argparse.Namespace(models=str(base), parts="selected-image"))
    if result == 0 and model not in installed_models(base, [model]):
        raise RuntimeError("The image download did not leave complete model components; setup is not finished.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, default="auto")
    parser.add_argument("--models", type=Path)
    parser.add_argument("--guard-parts")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--allow-unverified", action="store_true")
    parser.add_argument("--download-model", action="store_true")
    parser.add_argument("--preflight-sd", action="store_true")
    parser.add_argument("--preflight-native", action="store_true")
    parser.add_argument("--check-runtime", choices=("gpu", "comfy"))
    parser.add_argument("--check-access", action="store_true")
    args = parser.parse_args(argv)
    home = Path(os.environ.get("LOCALAPPDATA", "")) / "ImaginationStudio"
    base = args.models or home / "models"
    try:
        if args.check_runtime:
            check_runtime(args.check_runtime, home)
            return 0
        if args.preflight_sd:
            preflight_sd()
            return 0
        if args.preflight_native:
            native_device()
            return 0
        if args.guard_parts:
            from windows_setup_fit import probe
            parts = [part.strip() for part in args.guard_parts.split(",")]
            selected_models = {model for part in parts for model in NATIVE_PARTS.get(part, ())}
            missing = selected_models - installed_models(base, list(selected_models))
            hardware = probe() if not args.no_download and missing else {}
            plans = [] if args.no_download else guard_parts(
                parts, base, allow_unverified=args.allow_unverified, hardware=hardware)
        else:
            selected = "sd-turbo" if args.model == "auto" else args.model
            installed = installed_models(base, [selected])
            from windows_setup_fit import probe
            hardware = probe() if selected != "sd-turbo" and selected not in installed and not args.no_download else {}
            plans = [plan(args.model, installed=installed, hardware=hardware,
                          no_download=args.no_download, allow_unverified=args.allow_unverified)]
        for row in plans:
            for notice in row["notices"]:
                print(notice, file=sys.stderr)
        if any(not row["allowed"] for row in plans):
            raise RuntimeError("Shared tests failed on this configuration. Add -AllowUnverifiedImageModel "
                               "to explicitly accept the download risk, or use -Model auto for SD-Turbo. "
                               "No model bytes were downloaded; runtime guards remain unchanged.")
        if args.check_access:
            check_model_access([row["selected_model"] for row in plans if row["download"]], base)
            print("Model repository access checked; no model downloads started.")
            return 0
        if args.download_model:
            selected = plans[0]["selected_model"]
            if selected == "sd-turbo" or args.no_download:
                raise ValueError("Use setup-windows-storyboard.ps1 for SD-Turbo; --download-model is native only.")
            if plans[0]["download"]:
                return download_model(selected, base)
            return 0
        print(json.dumps(plans if args.guard_parts else plans[0]))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
