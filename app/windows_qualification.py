"""Compatibility boundaries for bounded Windows qualification; hashes remain provenance."""
from __future__ import annotations

from importlib import metadata
from functools import lru_cache
import os
from pathlib import Path
import re

GPU_CONTRACT = 1
LEGACY_GPU_COMPATIBILITY = {"contract": 1, "python_abi": "3.13", "torch_major": 2, "numpy_major": 2, "cuda_major": 13,
                            "limits_mb": [12288, 12288, 16384]}


def version_family(version: str, parts: int = 1) -> str:
    match = re.match(r"(\d+(?:\.\d+)*)", version)
    if not match or len(match[1].split(".")) < parts:
        raise ValueError("The runtime version is not identifiable.")
    return ".".join(match[1].split(".")[:parts])


def gpu_runtime(venv: Path) -> dict:
    config = dict(line.split("=", 1) for line in (venv / "pyvenv.cfg").read_text().splitlines() if "=" in line)
    config = {key.strip(): value.strip() for key, value in config.items()}
    versions = {dist.metadata["Name"].lower().replace("-", "_"): dist.version
                for dist in metadata.distributions(path=[str(venv / "Lib" / "site-packages")]) if dist.metadata["Name"]}
    return {"python": config["version"], "versions": versions}


def gpu_compatibility(venv: Path) -> dict:
    runtime = gpu_runtime(venv)
    return gpu_version_contract(runtime["python"], runtime["versions"])


def gpu_version_contract(python: str, versions: dict) -> dict:
    cuda = re.search(r"\+cu(\d{2})\d", versions["torch"])
    if not cuda:
        raise ValueError("The CUDA runtime family is not identifiable.")
    return {"contract": GPU_CONTRACT, "python_abi": version_family(python, 2),
            "torch_major": int(version_family(versions["torch"])), "numpy_major": int(version_family(versions["numpy"])),
            "cuda_major": int(cuda[1])}


def baseline_gpu_compatibility() -> dict:
    import windows_storyboard
    return {**gpu_compatibility(Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "gpu-venv"),
            "limits_mb": [windows_storyboard.GPU_LIMIT_MB, windows_storyboard.PROCESS_LIMIT_MB, windows_storyboard.JOB_LIMIT_MB]}


def baseline_gpu_runtime() -> dict:
    return gpu_runtime(Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio" / "gpu-venv")


@lru_cache(maxsize=1)
def hardware_profile() -> dict:
    from windows_memory import dedicated_capacity, host_memory, physical_memory_mb, single_gpu_uuid
    return {"physical_total_mb": physical_memory_mb(), "host_total_mb": host_memory()[0],
            "gpu_dedicated_total_mb": dedicated_capacity(single_gpu_uuid())}


def hardware_matches(recorded: dict) -> bool:
    return recorded == hardware_profile()


@lru_cache(maxsize=64)
def _verify_model(path: Path, size: int, digest: str, mtime_ns: int) -> None:
    from windows_brain import verify_file
    verify_file(path, size, digest)
    if path.stat().st_mtime_ns != mtime_ns:
        raise RuntimeError("The model changed during compatibility verification.")


def verify_model_updates(root: Path, files: dict, recorded_mtimes: dict) -> None:
    for name, (size, digest) in files.items():
        path = root / name
        current = path.stat().st_mtime_ns
        if recorded_mtimes.get(name) != current:
            _verify_model(path.resolve(), size, digest, current)
