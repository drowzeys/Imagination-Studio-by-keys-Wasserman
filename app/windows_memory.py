"""Windows host/GPU budgets. No model route is qualified by these infrastructure checks."""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import json
import math
import os
import sys
import time
import uuid

MIB = 1 << 20
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


class WindowsSignals:
    def __init__(self):
        from ctypes import wintypes

        class Performance(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in
                ("commit_total", "commit_limit", "commit_peak", "physical_total", "physical_available",
                 "system_cache", "kernel_total", "kernel_paged", "kernel_nonpaged", "page_size")
            ] + [(name, wintypes.DWORD) for name in ("handles", "processes", "threads")]

        self.performance_type = Performance
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMemoryResourceNotification.argtypes = [ctypes.c_int]
        self.api.CreateMemoryResourceNotification.restype = wintypes.HANDLE
        self.api.QueryMemoryResourceNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.api.QueryMemoryResourceNotification.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.performance_api = ctypes.WinDLL("psapi", use_last_error=True)
        self.performance_api.GetPerformanceInfo.argtypes = [ctypes.POINTER(Performance), wintypes.DWORD]
        self.performance_api.GetPerformanceInfo.restype = wintypes.BOOL
        self.handle = self.api.CreateMemoryResourceNotification(0)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def read(self) -> tuple[int, int, int, bool]:
        from ctypes import wintypes

        low = wintypes.BOOL()
        if not self.api.QueryMemoryResourceNotification(self.handle, ctypes.byref(low)):
            raise ctypes.WinError(ctypes.get_last_error())
        info = self.performance_type()
        info.size = ctypes.sizeof(info)
        if not self.performance_api.GetPerformanceInfo(ctypes.byref(info), info.size):
            raise ctypes.WinError(ctypes.get_last_error())
        return (info.commit_total * info.page_size // MIB, info.commit_limit * info.page_size // MIB,
                info.kernel_nonpaged * info.page_size // MIB, bool(low.value))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if not self.api.CloseHandle(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())


class DedicatedMemory:
    """NVML dedicated counters for the same UUID CUDA selected; never assume index ordering matches."""

    def __init__(self, device_uuid):
        class Memory(ctypes.Structure):
            _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong),
                        ("reserved", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

        self.memory_type = Memory
        system = ctypes.create_unicode_buffer(32768)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        kernel.GetSystemDirectoryW.restype = ctypes.c_uint
        length = kernel.GetSystemDirectoryW(system, len(system))
        if not length or length >= len(system):
            raise ctypes.WinError(ctypes.get_last_error())
        self.api = ctypes.CDLL(os.path.join(system.value, "nvml.dll"))
        self.api.nvmlInit_v2.argtypes = []
        self.api.nvmlInit_v2.restype = ctypes.c_int
        self.api.nvmlShutdown.argtypes = []
        self.api.nvmlShutdown.restype = ctypes.c_int
        self.api.nvmlDeviceGetHandleByUUID.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        self.api.nvmlDeviceGetHandleByUUID.restype = ctypes.c_int
        self.api.nvmlDeviceGetMemoryInfo_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(Memory)]
        self.api.nvmlDeviceGetMemoryInfo_v2.restype = ctypes.c_int
        identifier = "GPU-" + str(uuid.UUID(str(device_uuid).removeprefix("GPU-")))
        self.check(self.api.nvmlInit_v2(), "initialize")
        self.handle = ctypes.c_void_p()
        try:
            self.check(self.api.nvmlDeviceGetHandleByUUID(identifier.encode(), ctypes.byref(self.handle)), "select CUDA device")
        except RuntimeError:
            self.check(self.api.nvmlShutdown(), "shutdown")
            raise

    @staticmethod
    def check(code: int, operation: str):
        if code:
            raise RuntimeError(f"NVML could not {operation} (error {code})")

    def read(self) -> tuple[int, int]:
        memory = self.memory_type()
        memory.version = ctypes.sizeof(memory) | (2 << 24)
        self.check(self.api.nvmlDeviceGetMemoryInfo_v2(self.handle, ctypes.byref(memory)), "read dedicated memory")
        return memory.total // MIB, memory.free // MIB

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.check(self.api.nvmlShutdown(), "shutdown")


@dataclass(frozen=True)
class Snapshot:
    physical_total_mb: int
    host_total_mb: int
    host_free_mb: int
    gpu_total_mb: int
    gpu_free_mb: int
    at: float
    host_commit_used_mb: int
    host_commit_limit_mb: int
    host_nonpaged_mb: int
    host_low_memory: bool
    gpu_dedicated_total_mb: int
    gpu_dedicated_free_mb: int

    def validate(self) -> None:
        for name in ("physical_total_mb", "host_total_mb", "host_free_mb", "gpu_total_mb", "gpu_free_mb",
                     "host_commit_used_mb", "host_commit_limit_mb", "host_nonpaged_mb",
                     "gpu_dedicated_total_mb", "gpu_dedicated_free_mb"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not self.host_total_mb or not self.gpu_total_mb or not self.host_commit_limit_mb or not self.gpu_dedicated_total_mb:
            raise ValueError("memory totals must be positive")
        if self.host_total_mb > self.physical_total_mb or self.gpu_total_mb > self.physical_total_mb:
            raise ValueError("a memory budget exceeds physical capacity")
        if self.host_free_mb > self.host_total_mb or self.gpu_free_mb > self.gpu_total_mb:
            raise ValueError("free memory exceeds its budget")
        if self.gpu_dedicated_total_mb > self.physical_total_mb or self.gpu_dedicated_free_mb > self.gpu_dedicated_total_mb:
            raise ValueError("invalid dedicated GPU budget")
        if self.host_total_mb + self.gpu_dedicated_total_mb > self.physical_total_mb:
            raise ValueError("dedicated GPU and usable host memory exceed physical capacity")
        if self.host_nonpaged_mb > self.host_total_mb or self.host_commit_used_mb > self.host_commit_limit_mb:
            raise ValueError("invalid host resident or commit budget")
        if type(self.host_low_memory) is not bool:
            raise ValueError("Windows low-memory state must be boolean")
        if not isinstance(self.at, (int, float)) or isinstance(self.at, bool) or not math.isfinite(self.at):
            raise ValueError("invalid sample timestamp")


def host_floor_mb(snapshot: Snapshot, configured: str = "") -> int:
    if configured and (not configured.isdigit() or int(configured) <= 0):
        raise ValueError("SPARK_MEMORY_FLOOR_MB must be a positive integer")
    return int(configured or "0")


def pressure(snapshot: Snapshot, configured: str = "", previous: Snapshot | None = None) -> str:
    snapshot.validate()
    host_floor = host_floor_mb(snapshot, configured)
    if snapshot.host_low_memory:
        return "Windows reports low physical memory"
    commit_free = snapshot.host_commit_limit_mb - snapshot.host_commit_used_mb
    if commit_free == 0:
        return "Windows commit headroom is exhausted"
    if snapshot.host_free_mb == 0:
        return "Windows physical memory headroom is exhausted"
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
            ("commit", commit_free, previous.host_commit_limit_mb - previous.host_commit_used_mb, 0),
            ("CUDA", snapshot.gpu_free_mb, previous.gpu_free_mb, GPU_MIN_MB),
        ):
            if free < before and free - (before - free) / elapsed * 0.5 < floor:
                return f"{label} memory is falling toward its reserve within 0.5 seconds"
    return ""


@dataclass(frozen=True)
class Profile:
    """Host need is resident staging/shared growth; commit need includes pageable CPU/shared allocations.

    Compare host residency, commit and CUDA budgets separately; the underlying allocations overlap.
    """

    route: str
    measurement: str
    physical_total_mb: int
    host_total_mb: int
    host_need_mb: int
    gpu_need_mb: int
    host_commit_need_mb: int
    gpu_dedicated_total_mb: int


def admission(snapshot: Snapshot, profile: Profile | None, configured: str = "") -> dict:
    snapshot.validate()
    if profile is not None and (not isinstance(profile.measurement, str) or not isinstance(profile.route, str)):
        raise ValueError("profile route and measurement must be strings")
    reason = pressure(snapshot, configured)
    if profile is None or not profile.measurement.strip():
        reason = reason or "this Windows route has no measured peak profile"
    else:
        for value in (profile.physical_total_mb, profile.host_total_mb, profile.host_need_mb, profile.gpu_need_mb,
                      profile.host_commit_need_mb, profile.gpu_dedicated_total_mb):
            if type(value) is not int or value < 0:
                raise ValueError("profile budgets must be nonnegative integers")
        if not profile.route.strip():
            raise ValueError("a measured profile must name its route")
        if (profile.physical_total_mb, profile.host_total_mb, profile.gpu_dedicated_total_mb) != (
            snapshot.physical_total_mb, snapshot.host_total_mb, snapshot.gpu_dedicated_total_mb,
        ):
            reason = reason or "the measured profile does not match this physical/host memory split"
        elif snapshot.host_total_mb - snapshot.host_nonpaged_mb - profile.host_need_mb <= host_floor_mb(snapshot, configured):
            reason = reason or "the measured resident growth exceeds the usable host capacity"
        elif snapshot.host_commit_limit_mb - snapshot.host_commit_used_mb - profile.host_commit_need_mb <= 0:
            reason = reason or "the measured CPU/shared commitment would exhaust commit headroom"
        elif snapshot.gpu_free_mb - profile.gpu_need_mb < GPU_MIN_MB:
            reason = reason or "the measured CUDA growth would consume the CUDA reserve"
    return {"fits": not reason, "reason": reason, "host_floor_mb": host_floor_mb(snapshot, configured),
            "gpu_floor_mb": GPU_MIN_MB, "route": profile.route if profile else None,
            "host_need_mb": profile.host_need_mb if profile else None,
            "host_commit_need_mb": profile.host_commit_need_mb if profile else None,
            "gpu_need_mb": profile.gpu_need_mb if profile else None}


def watch(device_index: int) -> int:
    import torch

    if sys.platform != "win32" or not torch.cuda.is_available():
        raise RuntimeError("the Windows memory sampler requires native Windows CUDA")
    if not 0 <= device_index < torch.cuda.device_count():
        raise ValueError("the CUDA device index is not present")
    physical = physical_memory_mb()
    device_uuid = torch.cuda.get_device_properties(device_index).uuid
    with torch.cuda.device(device_index), WindowsSignals() as signals, DedicatedMemory(device_uuid) as dedicated:
        while True:
            host_total, host_free = host_memory()
            commit_used, commit_limit, nonpaged, low_memory = signals.read()
            gpu_free, gpu_total = torch.cuda.mem_get_info()
            dedicated_total, dedicated_free = dedicated.read()
            snapshot = Snapshot(physical, host_total, host_free, gpu_total // MIB, gpu_free // MIB, time.monotonic(),
                                commit_used, commit_limit, nonpaged, low_memory, dedicated_total, dedicated_free)
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
