#!/usr/bin/env python3
"""Apply a native PyTorch allocation cap before the pinned ComfyUI calibration starts."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys


def cap_allocator(torch, limit_mb: int) -> dict:
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("the first calibration requires exactly one visible CUDA device")
    if type(limit_mb) is not int or limit_mb <= 0:
        raise ValueError("the calibration GPU allocation limit must be positive")
    total = torch.cuda.get_device_properties(0).total_memory
    if limit_mb * (1 << 20) + (2048 << 20) > total:
        raise RuntimeError("the calibration cap would leave less than the CUDA reserve")
    if torch.cuda.memory.get_allocator_backend() != "native":
        raise RuntimeError("calibration requires the native PyTorch allocator for its allocation cap")
    torch.cuda.set_per_process_memory_fraction(limit_mb * (1 << 20) / total, 0)
    return {"gpu_limit_bytes": limit_mb << 20, "allocator": "native",
            "scope": "PyTorch allocations only; driver/library allocations still require monitoring"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy", type=Path, required=True)
    parser.add_argument("--gpu-limit-mb", type=int, required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.environ["PYTORCH_ALLOC_CONF"] = "backend:native"
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "backend:native"
    import torch

    print("[calibration] " + json.dumps(cap_allocator(torch, args.gpu_limit_mb)), flush=True)
    flags = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    if "--disable-cuda-malloc" not in flags:
        raise RuntimeError("calibration must not enable cudaMallocAsync")
    sys.path.insert(0, str(args.comfy.parent))
    sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "app"))
    sys.argv = [str(args.comfy), *flags]
    runpy.run_path(str(args.comfy), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
