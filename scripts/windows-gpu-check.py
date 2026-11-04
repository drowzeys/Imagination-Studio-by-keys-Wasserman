#!/usr/bin/env python3
"""Tiny CUDA checks only: no model downloads, servers, or admission-policy changes."""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys


def check_values(label: str, actual: list[float], expected: list[float], tolerance: float = 0.001) -> None:
    if len(actual) != len(expected):
        raise RuntimeError(f"{label}: wrong result length")
    if any(not math.isfinite(value) or abs(value - want) > tolerance for value, want in zip(actual, expected)):
        raise RuntimeError(f"{label}: result differs from the reference")


def probe(device_index: int) -> dict:
    import torch
    import torchaudio
    import torchvision

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; a CPU-only PyTorch installation does not qualify")
    if not 0 <= device_index < torch.cuda.device_count():
        raise ValueError("the requested CUDA device index is not present")
    device = torch.device("cuda", device_index)
    with torch.cuda.device(device), torch.inference_mode():
        free, total = torch.cuda.mem_get_info()
        checks = []
        for dtype in (torch.float32, torch.float16, torch.bfloat16):
            name = str(dtype).removeprefix("torch.")
            if dtype == torch.bfloat16 and not torch.cuda.is_bf16_supported():
                raise RuntimeError("the selected CUDA device does not support bf16")
            value = torch.ones((16, 16), dtype=dtype, device=device)
            check_values(name + " matmul", (value @ value).float().cpu().flatten().tolist(), [16.0] * 256)
            check_values(name + " addition", (value + value).float().cpu().flatten().tolist(), [2.0] * 256)
            query = torch.ones((1, 1, 4, 8), dtype=dtype, device=device)
            values = torch.arange(32, dtype=dtype, device=device).reshape(1, 1, 4, 8)
            attention = torch.nn.functional.scaled_dot_product_attention(query, query, values)
            check_values(name + " attention", attention.float().cpu().flatten().tolist(), list(range(12, 20)) * 4)
            checks.extend([name + " matmul", name + " addition", name + " attention"])
        boxes = torch.tensor([[0, 0, 10, 10], [0, 0, 10, 10]], dtype=torch.float32, device=device)
        scores = torch.tensor([0.9, 0.8], device=device)
        check_values("torchvision CUDA nms", torchvision.ops.nms(boxes, scores, 0.5).cpu().tolist(), [0])
        checks.append("torchvision CUDA nms")
        audio = torchaudio.functional.resample(torch.zeros((1, 64), device=device), 48000, 24000)
        check_values("torchaudio CUDA resample", audio.cpu().flatten().tolist(), [0.0] * 32)
        checks.append("torchaudio CUDA resample")
        torch.cuda.synchronize()
        peak = torch.cuda.max_memory_allocated()
    return {
        "ok": True,
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torchvision": torchvision.__version__,
        "torchaudio": torchaudio.__version__,
        "cuda": torch.version.cuda,
        "device_index": device_index,
        "compute_capability": list(torch.cuda.get_device_capability(device)),
        "cuda_budget_total_bytes": total,
        "cuda_budget_free_bytes_before_checks": free,
        "peak_tensor_bytes": peak,
        "checks": checks,
        "scope": "tiny operations only; not a model render, capacity qualification, or memory admission check",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-index", type=int, default=0)
    args = parser.parse_args()
    try:
        result = probe(args.device_index)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
