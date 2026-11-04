"""Handle-owned Windows subprocess trees shared by diagnostics and CLI agents."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
import threading
import time

from windows_memory import process_memory


class OwnedJob:
    """Keep cleanup tied to the launched process handle, not a port or reusable PID."""

    def __init__(self, process_commit_mb: int = 0, job_commit_mb: int = 0, breakaway: bool = False):
        if any(type(value) is not int or value < 0 for value in (process_commit_mb, job_commit_mb)):
            raise ValueError("Windows job commit limits must be nonnegative integers")
        self.lock = threading.RLock()
        size = ctypes.c_size_t

        class Limits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", size), ("max_working_set", size),
                ("active_process_limit", wintypes.DWORD), ("affinity", size),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Limits), ("io", Counters), ("process_memory", size), ("job_memory", size),
                ("peak_process_memory", size), ("peak_job_memory", size),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
                ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
                ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD),
            ]

        self.accounting_type = Accounting
        self.extended_type = Extended
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.api.IsProcessInJob.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if breakaway:
            # JOB_OBJECT_LIMIT_BREAKAWAY_OK: a child started with CREATE_BREAKAWAY_FROM_JOB leaves this job (the native
            # CUDA tier's ComfyUI, which spark/comfy_win.py keeps warm in a job of its own); nothing else changes
            limits.basic.flags |= 0x800
        if process_commit_mb:
            limits.basic.flags |= 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            limits.process_memory = process_commit_mb << 20
        if job_commit_mb:
            limits.basic.flags |= 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY
            limits.job_memory = job_commit_mb << 20
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process):
        # Popen retains this exact Windows process handle until it is closed.
        with self.lock:
            if not self.handle:
                raise RuntimeError("the owned job is closed")
            if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())

    def spawn(self, argv, **kwargs):
        flags = kwargs.pop("creationflags", 0) | 0x4  # CREATE_SUSPENDED
        process = subprocess.Popen(argv, creationflags=flags, **kwargs)
        try:
            self.attach(process)
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [wintypes.HANDLE]
            resume.restype = wintypes.LONG
            # Python closes the primary thread handle; resume through its retained process handle.
            status = resume(wintypes.HANDLE(int(process._handle)))
            if status < 0:
                raise OSError(f"could not resume the owned Windows process (NTSTATUS {status & 0xffffffff:#x})")
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            self.close()
            raise
        process._studio_job = self
        return process

    def terminate(self):
        with self.lock:
            if self.handle and not self.api.TerminateJobObject(self.handle, 0):
                raise ctypes.WinError(ctypes.get_last_error())

    def commitment(self) -> dict:
        with self.lock:
            if not self.handle:
                raise RuntimeError("the owned job is closed")
            limits = self.extended_type()
            if not self.api.QueryInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return {"peak_process_commit_bytes": limits.peak_process_memory, "peak_job_commit_bytes": limits.peak_job_memory,
                    "process_commit_limit_bytes": limits.process_memory, "job_commit_limit_bytes": limits.job_memory}

    def process_accounting(self, process) -> dict:
        with self.lock:
            if not self.handle:
                raise RuntimeError("the owned job is closed")
            contained = wintypes.BOOL()
            handle = wintypes.HANDLE(int(process._handle))
            if not self.api.IsProcessInJob(handle, self.handle, ctypes.byref(contained)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not contained.value:
                raise RuntimeError("refusing to attribute another process's memory to this job")
            return process_memory(int(process._handle))

    def close(self):
        with self.lock:
            if not self.handle:
                return
            if not self.api.TerminateJobObject(self.handle, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 10
            while True:
                accounting = self.accounting_type()
                if not self.api.QueryInformationJobObject(
                    self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not accounting.active_processes:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("the owned Windows job did not drain within 10 seconds")
                time.sleep(0.05)
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None
