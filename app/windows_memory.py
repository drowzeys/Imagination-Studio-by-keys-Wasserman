"""Windows host/GPU budgets. No model route is qualified by these infrastructure checks."""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
import math
import ntpath
import os
import re
import subprocess
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


def process_memory(process_handle: int) -> dict:
    """Query an owned Windows handle (-1 for this process), not a reusable PID.

    Working set includes resident shared/mapped pages. Private commitment is not resident RAM.
    """
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("page_faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in
            ("peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
             "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile", "private")
        ]

    api = ctypes.WinDLL("psapi", use_last_error=True)
    api.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    api.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.size = ctypes.sizeof(counters)
    if not api.GetProcessMemoryInfo(wintypes.HANDLE(process_handle), ctypes.byref(counters), counters.size):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set_bytes": counters.working_set, "peak_working_set_bytes": counters.peak_working_set,
            "private_commit_bytes": counters.private, "peak_commit_bytes": counters.peak_pagefile,
            "page_fault_count": counters.page_faults}


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

    def processes(self) -> dict:
        """NVML's process memory is unavailable under WDDM; retain that distinction."""
        class Process(ctypes.Structure):
            _fields_ = [("pid", ctypes.c_uint), ("used_memory", ctypes.c_ulonglong),
                        ("gpu_instance", ctypes.c_uint), ("compute_instance", ctypes.c_uint)]

        found, warnings = {}, []
        for kind in ("Compute", "Graphics"):
            function = getattr(self.api, f"nvmlDeviceGet{kind}RunningProcesses_v2", None)
            if function is None:
                warnings.append(f"{kind.lower()} process query is unsupported by this NVML")
                continue
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(Process)]
            function.restype = ctypes.c_int
            capacity = 32
            for _ in range(3):
                count = ctypes.c_uint(capacity)
                rows = (Process * capacity)()
                code = function(self.handle, ctypes.byref(count), rows)
                if code == 7:  # NVML_ERROR_INSUFFICIENT_SIZE: processes may appear between queries.
                    capacity = max(capacity * 2, count.value)
                    if capacity <= 4096:
                        continue
                    warnings.append(f"{kind.lower()} process list exceeds the diagnostic limit")
                    break
                if code:
                    warnings.append(f"NVML {kind.lower()} process query failed (error {code})")
                    break
                if count.value > capacity:
                    warnings.append(f"NVML {kind.lower()} process count is invalid")
                    break
                for row in rows[:count.value]:
                    used = None if row.used_memory == (1 << 64) - 1 else row.used_memory / MIB
                    previous = found.get(row.pid)
                    if previous is None:
                        found[row.pid] = {"pid": row.pid, "name": None, "used_mb": used}
                    elif used is not None:
                        previous["used_mb"] = max(previous["used_mb"] or 0, used)
                break
            else:
                warnings.append(f"{kind.lower()} process list kept changing; query is incomplete")

        name_query = getattr(self.api, "nvmlSystemGetProcessName", None)
        if name_query is not None:
            name_query.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint]
            name_query.restype = ctypes.c_int
        for item in found.values():
            if name_query is None:
                item["name_error"] = "process-name query unsupported"
                continue
            buffer = ctypes.create_string_buffer(32768)
            code = name_query(item["pid"], buffer, len(buffer))
            if code:
                reason = "access denied" if code == 4 else "process may have exited"
                item["name_error"] = f"name unavailable (NVML error {code}; {reason})"
            else:
                name = ntpath.basename(buffer.value.decode("utf-8", errors="replace"))
                item["name"] = "".join(character for character in name if character.isprintable()) or None
        return {"processes": sorted(found.values(), key=lambda item: (
            item["used_mb"] is None, -(item["used_mb"] or 0), item["pid"])), "warnings": warnings}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.check(self.api.nvmlShutdown(), "shutdown")


def _windows_gpu_counter(path: str) -> list[tuple[str, int, int]]:
    from ctypes import wintypes

    class ValueUnion(ctypes.Union):
        _fields_ = [("large", ctypes.c_longlong), ("double", ctypes.c_double),
                    ("long", wintypes.LONG), ("string", ctypes.c_void_p)]

    class Value(ctypes.Structure):
        _fields_ = [("status", wintypes.DWORD), ("value", ValueUnion)]

    class Item(ctypes.Structure):
        _fields_ = [("name", wintypes.LPWSTR), ("value", Value)]

    api = ctypes.WinDLL("pdh", use_last_error=True)
    api.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
    api.PdhAddEnglishCounterW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
    api.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
    api.PdhGetFormattedCounterArrayW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    api.PdhCloseQuery.argtypes = [wintypes.HANDLE]
    for name in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
                 "PdhGetFormattedCounterArrayW", "PdhCloseQuery"):
        getattr(api, name).restype = wintypes.DWORD

    def check(code):
        if code:
            raise RuntimeError(f"Windows GPU process counters unavailable (PDH error 0x{code:08x})")

    query, counter = wintypes.HANDLE(), wintypes.HANDLE()
    check(api.PdhOpenQueryW(None, 0, ctypes.byref(query)))
    try:
        check(api.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter)))
        check(api.PdhCollectQueryData(query))
        size, count = wintypes.DWORD(), wintypes.DWORD()
        code = api.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size), ctypes.byref(count), None)
        if code != 0x800007D2:  # PDH_MORE_DATA
            check(code)
        for _ in range(3):
            if not 0 < size.value <= 32 << 20:
                raise RuntimeError("Windows GPU process counter buffer size is invalid")
            buffer = ctypes.create_string_buffer(size.value)
            code = api.PdhGetFormattedCounterArrayW(
                counter, 0x400, ctypes.byref(size), ctypes.byref(count), buffer)
            if code != 0x800007D2:
                check(code)
                break
        else:
            raise RuntimeError("Windows GPU process counter list kept changing")
        if count.value * ctypes.sizeof(Item) > len(buffer):
            raise RuntimeError("Windows GPU process counter count is invalid")
        rows = ctypes.cast(buffer, ctypes.POINTER(Item))
        found = []
        for index in range(count.value):
            item = rows[index]
            found.append((item.name or "", item.value.status, item.value.value.large))
        return found
    finally:
        check(api.PdhCloseQuery(query))


def windows_gpu_process_memory() -> dict[int, float]:
    """Dedicated bytes per PID across Windows adapters, using the native PDH counters."""
    found = {}
    for name, status, value in _windows_gpu_counter(r"\GPU Process Memory(*)\Dedicated Usage"):
        match = re.fullmatch(r"pid_(\d+)_luid_0x[0-9a-f]+_0x[0-9a-f]+_phys_\d+", name, re.I)
        if match and status in (0, 1) and value >= 0:
            pid = int(match[1])
            found[pid] = found.get(pid, 0) + value / MIB
    return found


def single_gpu_uuid() -> str:
    result = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
                            capture_output=True, text=True, check=True, timeout=5,
                            creationflags=0x08000000 if sys.platform == "win32" else 0)
    # The N1X's DLA row is not a CUDA GPU.
    devices = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("GPU-")]
    if len(devices) != 1:
        raise RuntimeError("This bounded Windows profile requires one physical GPU.")
    return "GPU-" + str(uuid.UUID(devices[0].removeprefix("GPU-")))


def cuda_adapter_luid(device_uuid: str) -> tuple[int, int]:
    """Identify a WDDM node without creating a CUDA context."""
    expected = uuid.UUID(str(device_uuid).removeprefix("GPU-"))
    api = ctypes.WinDLL("nvcuda.dll", winmode=0x800)
    api.cuInit.argtypes = [ctypes.c_uint]
    api.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    api.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    identifier = getattr(api, "cuDeviceGetUuid_v2", None) or api.cuDeviceGetUuid
    identifier.argtypes = [ctypes.c_void_p, ctypes.c_int]
    api.cuDeviceGetLuid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_int]
    for function in (api.cuInit, api.cuDeviceGetCount, api.cuDeviceGet, identifier, api.cuDeviceGetLuid):
        function.restype = ctypes.c_int

    def check(code):
        if code:
            raise RuntimeError(f"CUDA could not identify the WDDM adapter (error {code})")

    check(api.cuInit(0))
    count = ctypes.c_int()
    check(api.cuDeviceGetCount(ctypes.byref(count)))
    matches = []
    for index in range(count.value):
        device = ctypes.c_int()
        check(api.cuDeviceGet(ctypes.byref(device), index))
        value = (ctypes.c_ubyte * 16)()
        check(identifier(value, device))
        if uuid.UUID(bytes=bytes(value)) == expected:
            luid, mask = (ctypes.c_uint32 * 2)(), ctypes.c_uint()
            check(api.cuDeviceGetLuid(luid, ctypes.byref(mask), device))
            if mask.value != 1:
                raise RuntimeError("The bounded WDDM check requires one physical node on its adapter.")
            matches.append((luid[1], luid[0]))
    if len(matches) != 1:
        raise RuntimeError("The selected CUDA UUID does not identify exactly one WDDM adapter.")
    return matches[0]


@lru_cache(maxsize=8)
def dedicated_capacity(device_uuid: str) -> int:
    with DedicatedMemory(device_uuid) as dedicated:
        total, _ = dedicated.read()
    if type(total) is not int or total <= 0:
        raise RuntimeError("The selected adapter has no valid dedicated capacity.")
    return total


class WddmMemory:
    """Fresh adapter-wide resident usage, not CUDA allocator or per-process commitment."""

    def __init__(self, device_uuid: str):
        self.luid = cuda_adapter_luid(device_uuid)
        # Capacity is static. Do not query NVML while the model is loading.
        self.total = dedicated_capacity(device_uuid)

    def read(self) -> tuple[int, int]:
        matches = []
        for name, status, used in _windows_gpu_counter(r"\GPU Adapter Memory(*)\Dedicated Usage"):
            match = re.fullmatch(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)_phys_(\d+)", name, re.I)
            if match and (int(match[1], 16), int(match[2], 16)) == self.luid:
                if int(match[3]) != 0 or status not in (0, 1) or used < 0:
                    raise RuntimeError("The selected WDDM adapter returned invalid memory counters.")
                matches.append(used)
        if len(matches) != 1:
            raise RuntimeError("The selected WDDM adapter memory counter is missing or ambiguous.")
        used_mb = (matches[0] + MIB - 1) // MIB
        if used_mb > self.total:
            raise RuntimeError("WDDM dedicated usage exceeds the selected adapter's capacity.")
        return self.total, self.total - used_mb


def gpu_memory_warning(free_mb: int, required_mb: int, device_uuid: str | None = None,
                       *, device_index: int = 0) -> str:
    message = (f"Not enough free dedicated GPU memory: {free_mb / 1024:.1f} GiB available; "
               f"{required_mb / 1024:.1f} GiB required (including the reserve). "
               "Unload unused models or close other GPU applications, then retry. "
               "Studio will not stop unrelated processes.")
    try:
        if device_uuid is None:
            device_uuid = subprocess.run(
                ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader", f"--id={device_index}"],
                capture_output=True, text=True, timeout=5, check=True,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            ).stdout.strip()
        with DedicatedMemory(device_uuid) as memory:
            details = memory.processes()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        return message + f" GPU process details unavailable ({type(error).__name__}). Check Task Manager's Dedicated GPU memory column."
    processes = details["processes"]
    windows_usage = {}
    if any(item["used_mb"] is None for item in processes):
        try:
            windows_usage = windows_gpu_process_memory()
        except (OSError, RuntimeError) as error:
            details["warnings"].append(f"Windows GPU process counters unavailable ({type(error).__name__})")
    large = []
    for item in processes:
        used = item["used_mb"]
        source = ""
        if used is None:
            used = windows_usage.get(item["pid"])
            source = ", Windows total across adapters"
        if used is not None and used >= 1024:
            large.append({**item, "used_mb": used, "source": source})
    large.sort(key=lambda item: (-item["used_mb"], item["pid"]))
    if large:
        labels = []
        for item in large:
            usage = f"{item['used_mb'] / 1024:.2f} GiB{item['source']}"
            labels.append(f"{item['name'] or item.get('name_error', 'name unavailable')} (PID {item['pid']}, {usage})")
        message += " Large GPU users (at least 1 GiB, largest first): " + "; ".join(labels) + "."
    else:
        message += " No GPU processes with measured usage of at least 1 GiB were identified."
    message += " Smaller or unknown usage is excluded; this is not a complete attribution of memory."
    if details["warnings"]:
        message += " Process query incomplete: " + "; ".join(details["warnings"]) + "."
    if details["warnings"] or not large:
        message += " Check Task Manager's Dedicated GPU memory column for Windows memory attribution."
    return message


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
        trends = [
            ("commit", commit_free, previous.host_commit_limit_mb - previous.host_commit_used_mb, 0),
            ("CUDA", snapshot.gpu_free_mb, previous.gpu_free_mb, GPU_MIN_MB),
        ]
        if host_floor:
            trends.append(("host", snapshot.host_free_mb, previous.host_free_mb, host_floor))
        for label, free, before, floor in trends:
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


def dedicated_snapshot(physical: int, signals, dedicated) -> Snapshot:
    host_total, host_free = host_memory()
    commit_used, commit_limit, nonpaged, low_memory = signals.read()
    total, free = dedicated.read()
    value = Snapshot(physical, host_total, host_free, total, free, time.monotonic(),
                     commit_used, commit_limit, nonpaged, low_memory, total, free)
    value.validate()
    return value


def watch_nvml(device_uuid: str) -> int:
    if sys.platform != "win32":
        raise RuntimeError("The NVML sampler requires native Windows.")
    physical = physical_memory_mb()
    with WindowsSignals() as signals, DedicatedMemory(device_uuid) as dedicated:
        while True:
            if os.environ.get("STUDIO_MEMORY_DEBUG") == "1":
                import faulthandler
                faulthandler.dump_traceback_later(1.5, file=sys.stderr)
            try:
                print(json.dumps(asdict(dedicated_snapshot(physical, signals, dedicated))), flush=True)
            finally:
                if os.environ.get("STUDIO_MEMORY_DEBUG") == "1":
                    faulthandler.cancel_dump_traceback_later()
            time.sleep(.25)


def watch_wddm(device_uuid: str) -> int:
    if sys.platform != "win32":
        raise RuntimeError("The WDDM sampler requires native Windows.")
    physical = physical_memory_mb()
    dedicated = WddmMemory(device_uuid)
    with WindowsSignals() as signals:
        while True:
            print(json.dumps(asdict(dedicated_snapshot(physical, signals, dedicated))), flush=True)
            time.sleep(.25)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", required=True)
    parser.add_argument("--device-index", type=int, default=0)
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--nvml-only", action="store_true")
    provider.add_argument("--wddm-only", action="store_true")
    parser.add_argument("--device-uuid")
    args = parser.parse_args()
    try:
        if (args.nvml_only or args.wddm_only) and not args.device_uuid:
            raise ValueError("The dedicated-memory sampler requires the selected device UUID.")
        raise SystemExit(watch_wddm(args.device_uuid) if args.wddm_only else
                         watch_nvml(args.device_uuid) if args.nvml_only else watch(args.device_index))
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error)}), flush=True)
        raise SystemExit(1)
