"""Pinned per-user FFmpeg/ffprobe for native Windows ARM64; no system PATH changes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import zipfile

from windows_brain import fetch, verify_file

VERSION = "n8.1.3-27-g8dca1586f4"
ARCHIVE = f"ffmpeg-{VERSION}-winarm64-gpl-8.1.zip"
URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/{ARCHIVE}"
SIZE = 134963677
SHA256 = "40029da82e02abf0d12491e23666e710b7a8943d13501594cf38e6135b95efba"


def folder() -> Path:
    return Path(os.environ.get("STUDIO_FFMPEG_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "ImaginationStudio" / "ffmpeg" / VERSION)


def bin_folder() -> Path:
    return folder() / ARCHIVE.removesuffix(".zip") / "bin"


def search_path(current: str) -> str:
    binary = bin_folder()
    if sys.platform != "win32" or not all((binary / f"{name}.exe").is_file() for name in ("ffmpeg", "ffprobe")):
        return current
    return os.pathsep.join([str(binary), current])


def verify_runtime() -> dict:
    versions = {}
    for name in ("ffmpeg", "ffprobe"):
        path = bin_folder() / f"{name}.exe"
        with path.open("rb") as source:
            if source.read(2) != b"MZ":
                raise RuntimeError(f"{name} is not a Windows executable")
            source.seek(0x3c)
            source.seek(struct.unpack("<I", source.read(4))[0])
            if source.read(4) != b"PE\0\0" or struct.unpack("<H", source.read(2))[0] != 0xaa64:
                raise RuntimeError(f"{name} is not native Windows ARM64")
        result = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=10, check=True)
        line = result.stdout.splitlines()[0]
        if VERSION not in line:
            raise RuntimeError(f"{name} is not the pinned build")
        versions[name] = line
    return {"ok": True, "architecture": "ARM64", "versions": versions, "scope": "CPU media tools only"}


def install(download: bool) -> dict:
    if sys.platform != "win32" or platform.machine().lower() not in ("arm64", "aarch64"):
        raise RuntimeError("this pinned media runtime requires native Windows ARM64")
    root = folder()
    root.mkdir(parents=True, exist_ok=True)
    if download:
        if shutil.disk_usage(root).free < 1 << 30:
            raise RuntimeError("FFmpeg setup needs at least 1 GiB of free disk")
        fetch(URL, root / ARCHIVE, SIZE, SHA256)
    else:
        verify_file(root / ARCHIVE, SIZE, SHA256)
    with zipfile.ZipFile(root / ARCHIVE) as archive:
        for entry in archive.infolist():
            path = (root / entry.filename).resolve()
            if not path.is_relative_to(root.resolve()):
                raise RuntimeError("the FFmpeg archive contains a path outside its folder")
        if download:
            archive.extractall(root)
    return verify_runtime()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = install(not args.verify)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
