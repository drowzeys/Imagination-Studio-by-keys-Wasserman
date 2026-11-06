"""Retain the actual ComfyUI process, not the Windows venv launcher, within its existing owned job."""
import ctypes
from ctypes import wintypes
import os

from windows_memory import process_memory


def kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    return api


def creation_time(handle) -> int:
    values = [ctypes.c_ulonglong() for _ in range(4)]
    if not kernel().GetProcessTimes(handle, *[ctypes.byref(value) for value in values]):
        raise ctypes.WinError(ctypes.get_last_error())
    return values[0].value


def identity() -> dict:
    return {"pid": os.getpid(), "created": creation_time(wintypes.HANDLE(-1))}


class BackendHandle:
    def __init__(self, api, handle):
        self.api, self.handle = api, handle

    def read(self) -> dict:
        if not self.handle:
            raise RuntimeError("The owned backend handle is closed.")
        return process_memory(int(self.handle))

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None

    def wait(self, seconds: int = 10):
        if self.handle and self.api.WaitForSingleObject(self.handle, seconds * 1000) != 0:
            raise RuntimeError("The actual owned backend did not finish shutdown within its deadline.")


def open_owned(job, reported: dict) -> BackendHandle:
    if type(reported.get("pid")) is not int or reported["pid"] <= 0 or type(reported.get("created")) is not int:
        raise ValueError("The backend did not report a valid process identity.")
    api = kernel()
    handle = api.OpenProcess(0x1000 | 0x10 | 0x100000, False, reported["pid"])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with job.lock:
            if not job.handle:
                raise RuntimeError("The owned job is closed.")
            if creation_time(handle) != reported["created"]:
                raise RuntimeError("The backend PID was reused; its identity no longer matches.")
            contained = wintypes.BOOL()
            if not job.api.IsProcessInJob(handle, job.handle, ctypes.byref(contained)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not contained.value:
                raise RuntimeError("The responding backend is not inside this owned job.")
        return BackendHandle(api, handle)
    except BaseException:
        api.CloseHandle(handle)
        raise
