"""Verify the bounded Windows graphics profile without downloading a model or browser."""
from __future__ import annotations

import json
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import graphics
import windows_media
from windows_process import OwnedJob
from PIL import Image


def window_process_path(window) -> Path:
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    pid = wintypes.DWORD()
    if not user.GetWindowThreadProcessId(window, ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(path.value).resolve()
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def visible_browser_windows() -> set[int]:
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.EnumWindows.restype = wintypes.BOOL
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsWindowVisible.restype = wintypes.BOOL
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsWindow.restype = wintypes.BOOL
    api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetClassNameW.restype = ctypes.c_int
    browser = Path(graphics.browser()).resolve()
    found, errors = set(), []

    @callback_type
    def collect(handle, _):
        if api.IsWindowVisible(handle):
            name = ctypes.create_unicode_buffer(256)
            if not api.GetClassNameW(handle, name, len(name)) and api.IsWindow(handle):
                errors.append(ctypes.WinError(ctypes.get_last_error()))
                return False
            if name.value == "Chrome_WidgetWin_1":
                try:
                    if window_process_path(handle) == browser:
                        found.add(handle)
                except OSError as error:
                    if api.IsWindow(handle):
                        errors.append(error)
                        return False
        return True

    if not api.EnumWindows(collect, 0):
        raise errors[0] if errors else ctypes.WinError(ctypes.get_last_error())
    return found


def run(command, environment=None) -> dict:
    owned = OwnedJob(job_commit_mb=graphics.WINDOWS_COMMIT_MB)
    started = time.monotonic()
    try:
        process = owned.spawn(command, env={**os.environ, **(environment or {})}, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        try:
            output, _ = process.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            owned.terminate()
            process.communicate(timeout=10)
            raise RuntimeError("The owned graphics check exceeded five minutes") from None
        if process.returncode:
            raise RuntimeError(f"Graphics check exited {process.returncode}:\n{output[-6000:]}")
        return {"elapsed_s": round(time.monotonic() - started, 2),
                "peak_commit_mb": round(owned.commitment()["peak_job_commit_bytes"] / 1024 ** 2, 1)}
    finally:
        owned.close()


def check() -> dict:
    if not graphics.WINDOWS:
        raise RuntimeError("This check requires native Windows")
    os.environ["PATH"] = windows_media.search_path(os.environ.get("PATH", ""))
    node, _ = graphics.node_bin()
    if not node or not graphics.browser() or graphics.installed_version() != graphics.HYPERFRAMES:
        raise RuntimeError("Run setup-windows-graphics.ps1 first; no browser or model is downloaded by this check")
    if why := graphics.windows_probe_error():
        raise RuntimeError(why + "; run setup-windows-graphics.ps1 before checking")
    fingerprint = graphics.windows_fingerprint()
    marker = graphics.install_dir() / "windows-checked.json"
    results = []
    with tempfile.TemporaryDirectory(prefix="studio-graphics-check-") as scratch:
        root = Path(scratch)
        clip = root / "clip.mp4"
        run([str(windows_media.bin_folder() / "ffmpeg.exe"), "-nostdin", "-v", "error", "-f", "lavfi",
             "-i", "color=c=navy:s=1280x720:r=24:d=10", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=10",
             "-af", "volume=0.03", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)])
        pictures = [root / "one.png", root / "two.png"]
        for path, color in zip(pictures, ("navy", "darkred")):
            Image.new("RGB", (1280, 720), color).save(path)
        song = root / "song.wav"
        run([str(windows_media.bin_folder() / "ffmpeg.exe"), "-nostdin", "-v", "error", "-i", str(clip),
             "-vn", "-c:a", "pcm_s16le", str(song)])
        other_clip = root / "other.mp4"
        run([str(windows_media.bin_folder() / "ffmpeg.exe"), "-nostdin", "-v", "error", "-f", "lavfi",
             "-i", "color=c=darkred:s=1280x720:r=24:d=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(other_clip)])

        def resolve(value, kinds, what):
            source = Path(value)
            if source.parent != root or not source.is_file() or source.suffix.lower() not in kinds:
                raise graphics.Refused(f"Invalid graphics check fixture: {what}")
            return source

        cases = [
            {"template": "title", "title": "Windows graphics", "subtitle": "Bounded render check", "seconds": 10},
            {"template": "lower-third", "name": "Graphics check", "role": "Local renderer", "video": str(clip)},
            {"template": "kinetic", "text": "Words move in time", "song": str(song), "beats": [0, 2.7, 5.4, 8.2]},
            {"template": "slideshow", "pictures": list(map(str, pictures)), "seconds_each": 5},
            {"template": "music-video", "clips": [str(clip), str(other_clip)], "song": str(song), "seconds": 10, "bpm": 120, "transition": "cut"},
            {"template": "explainer", "sections": [{"heading": "The idea", "text": "Words with a picture.", "picture": str(pictures[0])},
                                                 {"heading": "The result", "text": "Another section.", "picture": str(pictures[1])}],
             "seconds_each": 5, "song": str(song)},
        ]
        for params in cases:
            made = graphics.plan({**params, "size": "720p"}, resolve)
            folder, target = root / params["template"], root / (params["template"] + ".mp4")
            graphics.write(folder, made)
            before = visible_browser_windows()
            result = run([sys.executable, "-X", "utf8", str(Path(graphics.__file__)), "run", str(folder),
                          "--", *graphics.command(folder, target)], graphics.environment())
            if visible_browser_windows() - before:
                raise RuntimeError(f"New visible browser windows appeared during {params['template']}; graphics remains unqualified")
            info = graphics.probe(target)
            if (info["width"], info["height"]) != (1280, 720) or abs(info["seconds"] - 10) > 1 / 24 or info["fps"] != 24:
                raise RuntimeError("The graphics output does not match the 720p, 24 fps, ten-second profile")
            if bool(info["audio"]) != (params["template"] == "lower-third" or bool(params.get("song"))):
                raise RuntimeError("The graphics output lost its source sound or added an unexpected track")
            results.append({"template": params["template"], **result, "seconds": info["seconds"]})
    if graphics.windows_fingerprint() != fingerprint:
        raise RuntimeError("The graphics runtime changed during the check; repeat it")
    receipt = {"fingerprint": fingerprint, "compatibility": graphics.windows_compatibility(),
               "results": results, "fps": graphics.FPS, "size": [1280, 720], "visible_windows_added": 0}
    pending = marker.with_suffix(".tmp")
    pending.write_text(json.dumps(receipt), encoding="utf-8")
    pending.replace(marker)
    return {"ok": True, "size": [1280, 720], "fps": graphics.FPS, "results": results, "visible_windows_added": 0}


if __name__ == "__main__":
    try:
        print(json.dumps(check()))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
