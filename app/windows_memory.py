"""Windows host/GPU budgets. No model route is qualified by these infrastructure checks."""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import json
import math
import os
import sys
import time

MIB = 1 << 20
HOST_MIN_MB = 8000
GPU_MIN_MB = 2048


def host_memory() -> tuple[int, int]:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(Status)]
    api.GlobalMemoryStatusEx.restype = ctypes.c_int
    status = Status()
    status.length = ctypes.sizeof(status)
    if not api.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return status.total // MIB, status.available // MIB


def physical_memory_mb() -> int:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetPhysicallyInstalledSystemMemory.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    api.GetPhysicallyInstalledSystemMemory.restype = ctypes.c_int
    size_kb = ctypes.c_ulonglong()
    if not api.GetPhysicallyInstalledSystemMemory(ctypes.byref(size_kb)):
        raise ctypes.WinError(ctypes.get_last_error())
    return size_kb.value // 1024


@dataclass(frozen=True)
class Snapshot:
    physical_total_mb: int
    host_total_mb: int
    host_free_mb: int
    gpu_total_mb: int
    gpu_free_mb: int
    at: float

    def validate(self) -> None:
        for name in ("physical_total_mb", "host_total_mb", "host_free_mb", "gpu_total_mb", "gpu_free_mb"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not self.host_total_mb or not self.gpu_total_mb:
            raise ValueError("memory totals must be positive")
        if self.host_total_mb > self.physical_total_mb or self.gpu_total_mb > self.physical_total_mb:
            raise ValueError("a memory budget exceeds physical capacity")
        if self.host_free_mb > self.host_total_mb or self.gpu_free_mb > self.gpu_total_mb:
            raise ValueError("free memory exceeds its budget")
        if not isinstance(self.at, (int, float)) or isinstance(self.at, bool) or not math.isfinite(self.at):
            raise ValueError("invalid sample timestamp")


def host_floor_mb(snapshot: Snapshot, configured: str = "") -> int:
    if configured and (not configured.isdigit() or int(configured) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer")
    return max(HOST_MIN_MB, snapshot.physical_total_mb // 10, int(configured or "0"))


def pressure(snapshot: Snapshot, configured: str = "", previous: Snapshot | None = None) -> str:
    snapshot.validate()
    host_floor = host_floor_mb(snapshot, configured)
    if snapshot.host_free_mb < host_floor:
        return f"host memory {snapshot.host_free_mb} MiB is below the {host_floor} MiB reserve"
    if snapshot.gpu_free_mb < GPU_MIN_MB:
        return f"CUDA budget {snapshot.gpu_free_mb} MiB is below the {GPU_MIN_MB} MiB reserve"
    if previous is not None:
        previous.validate()
        elapsed = snapshot.at - previous.at
        if not 0 < elapsed <= 2:
            raise ValueError("memory samples are out of order or stale")
        for label, free, before, floor in (
            ("host", snapshot.host_free_mb, previous.host_free_mb, host_floor),
            ("CUDA", snapshot.gpu_free_mb, previous.gpu_free_mb, GPU_MIN_MB),
        ):
            if free < before and free - (before - free) / elapsed * 0.5 < floor:
                return f"{label} memory is falling toward its reserve within 0.5 seconds"
    return ""


@dataclass(frozen=True)
class Profile:
    """Host need includes CPU staging and shared-GPU growth; CUDA need includes dedicated/shared allocations.

    These overlap: compare each to its own headroom, never add the two free-memory figures.
    """

    route: str
    measurement: str
    physical_total_mb: int
    host_total_mb: int
    host_need_mb: int
    gpu_need_mb: int


def admission(snapshot: Snapshot, profile: Profile | None, configured: str = "") -> dict:
    snapshot.validate()
    if profile is not None and (not isinstance(profile.measurement, str) or not isinstance(profile.route, str)):
        raise ValueError("profile route and measurement must be strings")
    reason = pressure(snapshot, configured)
    if profile is None or not profile.measurement.strip():
        reason = reason or "this Windows route has no measured peak profile"
    else:
        for value in (profile.physical_total_mb, profile.host_total_mb, profile.host_need_mb, profile.gpu_need_mb):
            if type(value) is not int or value < 0:
                raise ValueError("profile budgets must be nonnegative integers")
        if not profile.route.strip():
            raise ValueError("a measured profile must name its route")
        if (profile.physical_total_mb, profile.host_total_mb) != (snapshot.physical_total_mb, snapshot.host_total_mb):
            reason = reason or "the measured profile does not match this physical/host memory split"
        elif snapshot.host_free_mb - profile.host_need_mb < host_floor_mb(snapshot, configured):
            reason = reason or "the measured host/shared growth would consume the host reserve"
        elif snapshot.gpu_free_mb - profile.gpu_need_mb < GPU_MIN_MB:
            reason = reason or "the measured CUDA growth would consume the CUDA reserve"
    return {"fits": not reason, "reason": reason, "host_floor_mb": host_floor_mb(snapshot, configured),
            "gpu_floor_mb": GPU_MIN_MB, "route": profile.route if profile else None,
            "host_need_mb": profile.host_need_mb if profile else None,
            "gpu_need_mb": profile.gpu_need_mb if profile else None}


def watch(device_index: int) -> int:
    import torch

    if sys.platform != "win32" or not torch.cuda.is_available():
        raise RuntimeError("the Windows memory sampler requires native Windows CUDA")
    if not 0 <= device_index < torch.cuda.device_count():
        raise ValueError("the CUDA device index is not present")
    physical = physical_memory_mb()
    with torch.cuda.device(device_index):
        while True:
            host_total, host_free = host_memory()
            gpu_free, gpu_total = torch.cuda.mem_get_info()
            snapshot = Snapshot(physical, host_total, host_free, gpu_total // MIB, gpu_free // MIB, time.monotonic())
            snapshot.validate()
            print(json.dumps(asdict(snapshot)), flush=True)
            time.sleep(0.25)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", required=True)
    parser.add_argument("--device-index", type=int, default=0)
    args = parser.parse_args()
    try:
        raise SystemExit(watch(args.device_index))
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error)}), flush=True)
        raise SystemExit(1)
