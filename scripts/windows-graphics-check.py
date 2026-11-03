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
    found, errors = set(), []

    @callback_type
    def collect(handle, _):
        if api.IsWindowVisible(handle):
            name = ctypes.create_unicode_buffer(256)
            if not api.GetClassNameW(handle, name, len(name)) and api.IsWindow(handle):
                errors.append(ctypes.WinError(ctypes.get_last_error()))
                return False
            if name.value == "Chrome_WidgetWin_1":
                found.add(handle)
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
    marker.unlink(missing_ok=True)
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

        def resolve(value, kinds, what):
            source = Path(value)
            if source.parent != root or not source.is_file() or source.suffix.lower() not in kinds:
                raise graphics.Refused(f"Invalid graphics check fixture: {what}")
            return source

        cases = [
            {"template": "title", "title": "Windows graphics", "subtitle": "Bounded render check", "seconds": 10},
            {"template": "lower-third", "name": "Graphics check", "role": "Local renderer", "video": str(clip)},
            {"template": "slideshow", "pictures": list(map(str, pictures)), "seconds_each": 5},
        ]
        for params in cases:
            made = graphics.plan({**params, "size": "720p"}, resolve)
            folder, target = root / params["template"], root / (params["template"] + ".mp4")
            graphics.write(folder, made)
            before = visible_browser_windows()
            result = run([sys.executable, "-X", "utf8", str(Path(graphics.__file__)), "run", str(folder),
                          "--", *graphics.command(folder, target)], graphics.environment())
            if visible_browser_windows() - before:
                raise RuntimeError("New visible browser windows appeared during rendering; graphics remains unqualified")
            info = graphics.probe(target)
            if (info["width"], info["height"]) != (1280, 720) or abs(info["seconds"] - 10) > 1 / 24:
                raise RuntimeError("The graphics output does not match the 720p, ten-second profile")
            if bool(info["audio"]) != (params["template"] == "lower-third"):
                raise RuntimeError("The graphics output lost its source sound or added an unexpected track")
            results.append({"template": params["template"], **result, "seconds": info["seconds"]})
    if graphics.windows_fingerprint() != fingerprint:
        raise RuntimeError("The graphics runtime changed during the check; repeat it")
    receipt = {"fingerprint": fingerprint, "results": results, "fps": graphics.FPS, "size": [1280, 720], "visible_windows_added": 0}
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
