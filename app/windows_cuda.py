"""Native Windows CUDA plumbing shared by individually selected feature backends.

An NVIDIA RTX Spark N1X laptop (Windows 11 on Arm, sm_121 like a GB10) has a CUDA pool of about 113 GB under its
native WDDM driver. With the full install there (``oneshot-setup-rtx-spark-n1x.ps1 -Native``: the GPU venv, the pinned
ComfyUI, TensorFold 1.2.2's ``tf_h3.dll`` and, optionally, ``tensorfold-native.exe`` + ``bolt.dll``) the Studio runs the
same jobs as on a DGX Spark, without WSL: the Spark logic, with the bash scripts replaced by their Python ports
(``spark/windows_scripts.py`` for scripts/video.sh, studio.sh, scout.sh, image.sh and edit.sh, ``spark/comfy_win.py``
for spark/comfy.sh).

``active`` identifies the shared GPU Python/ComfyUI installation, not an app-wide capability tier.
H3's engine and measured pool requirement are checked separately by ``h3_why_not``; they must not
block unrelated picture, sound or CPU features. ``STUDIO_WINDOWS_TIER=preview`` remains a legacy
test/debug override. Nothing here runs on Linux or macOS.

Every job kind runs natively: the Library's actions on the authors' node packs, Character Swap, MMAudio foley, LoRA
training (musubi-tuner in a venv of its own) and Tesseract Finish (its windows-x86_64 build, through Windows' x64
emulation). A kind without a port would be refused when it is queued, with the reason (``unsupported``), never sent to
WSL or to a Linux script.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent
MIN_POOL_MB = 90_000  # FastH3 at 864x480 on the int8 engine peaks near 58 GB; a 48 GB box keeps the preview tier
# job kinds the native tier runs (the rest are refused with NOT_NATIVE's reason): the Spark's video, picture, song,
# sound and voice routes, and what the preview tier already runs (exports, graphics, beats)
NATIVE = {"video", "studio", "long", "scout", "edit", "export", "soundtrack", "voice", "music", "sfx", "graphics", "hlg",
          "turnaround", "upscale", "mask", "remove", "swap", "foley", "finish", "train"}
# none left: every job kind of the Spark path has a native port. A kind added to the Spark later and not ported is
# refused (unsupported) with the reason written here, never sent to WSL or to a Linux script.
NOT_NATIVE: dict[str, str] = {}
# the scripts the server and its modules run with bash on a Spark, and their native ports' commands
PORTED = {"video.sh": "video", "studio.sh": "studio", "scout.sh": "scout", "image.sh": "image", "edit.sh": "edit",
          "turnaround.sh": "turnaround"}
_STATE: dict = {}


def native_os() -> bool:
    return sys.platform == "win32" and os.environ.get("STUDIO_WINDOWS_TIER", "").strip().lower() != "preview"


def root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ImaginationStudio"


def config() -> dict:
    """The installer's record (cuda-tier.json): where the models and engines are, the CUDA pool it measured."""

    path = Path(os.environ.get("STUDIO_CUDA_TIER") or root() / "cuda-tier.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def configuration_error() -> str:
    path = Path(os.environ.get("STUDIO_CUDA_TIER") or root() / "cuda-tier.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as error:
        return f"The native CUDA settings could not be read ({type(error).__name__}); rerun native setup."
    return "" if isinstance(value, dict) else "The native CUDA settings are not an object; rerun native setup."


def _path(data: dict, key: str, default: Path) -> Path:
    value = data.get(key)
    return Path(os.path.expandvars(str(value))).expanduser() if value else default


def paths(data: dict | None = None) -> dict[str, Path]:
    data = config() if data is None else data
    base = root()
    bolt = _path(data, "bolt_bin", base / "bolt" / "native" / "bin" / "tensorfold-native.exe")
    drama = _path(data, "drama3_bin", base / "drama3" / "bin" / "drama3-generate.exe")
    music = _path(data, "music3_bin", base / "music3" / "bin" / "music3-generate.exe")
    return {"prefix": base, "python": _path(data, "python", base / "gpu-venv" / "Scripts" / "python.exe"),
            "comfy": base / "ComfyUI", "models": _path(data, "models", base / "models"),
            "tf_h3": _path(data, "tf_h3", base / "tf-h3" / "tf_h3.dll"),
            "bolt_bin": bolt, "bolt_lib": _path(data, "bolt_lib", bolt.parent),
            "drama3_bin": drama, "drama3_lib": _path(data, "drama3_lib", drama.parent / "tensorfold_drama3.dll"),
            "music3_bin": music, "train_python": _path(data, "train_python", base / "train" / "venv" / "Scripts" / "python.exe"),
            "cuda_bin": _path(data, "cuda_bin", Path("C:/cuda-13.4/bin/arm64"))}


def why_not() -> str:
    """Why the shared native CUDA backend is unavailable, independent of H3."""

    if sys.platform != "win32":
        return "not Windows"
    if os.environ.get("STUDIO_WINDOWS_TIER", "").strip().lower() == "preview":
        return "STUDIO_WINDOWS_TIER=preview"
    if why := configuration_error():
        return why
    data = config()
    found = paths(data)
    for key, what in (("python", "the GPU venv"),):
        if not found[key].is_file():
            return f"no {what} at {found[key]}"
    if not (found["comfy"] / "main.py").is_file():
        return f"no ComfyUI at {found['comfy']}"
    return ""


def h3_why_not() -> str:
    """The existing H3-specific engine/pool boundary; no memory limit is relaxed."""
    if why := why_not():
        return why
    data = config()
    found = paths(data)
    if not found["tf_h3"].is_file():
        return f"TensorFold's H3 engine is not installed at {found['tf_h3']}"
    try:
        pool = int(data.get("cuda_pool_mb") or 0)
    except (TypeError, ValueError):
        pool = 0
    if pool <= 0:
        return f"H3 requires an installer-measured CUDA pool of at least {MIN_POOL_MB} MB; no valid pool measurement is recorded yet"
    if pool < MIN_POOL_MB:
        return f"H3 requires an installer-measured CUDA pool of at least {MIN_POOL_MB} MB; the recorded pool is {pool} MB"
    return ""


def active() -> bool:
    """Whether this Studio runs the native Windows CUDA tier. Decided once a process; False off Windows at no cost."""

    if sys.platform != "win32":
        return False
    if "active" not in _STATE:
        _STATE["why"] = why_not()
        _STATE["active"] = not _STATE["why"]
        if _STATE["active"]:
            activate()
    return _STATE["active"]


def activate() -> None:
    """The environment the Spark code reads, pointed at the Windows install (set only where not set already, so a
    variable the person gave wins): PREFIX, SPARK_MODELS, TF_H3_LIB, the engines' files and the render python."""

    data = config()
    found = paths(data)
    defaults = {"PREFIX": found["prefix"], "SPARK_MODELS": found["models"], "TF_H3_LIB": found["tf_h3"],
                "SOUND_PYTHON": found["python"], "STUDIO_WINDOWS_CUDA": "1",
                "BOLT_BIN": found["bolt_bin"], "BOLT_LIB": found["bolt_lib"],
                "DRAMA3_CUDA_BIN": found["drama3_bin"], "DRAMA3_CUDA_LIB": found["drama3_lib"],
                "MUSIC3_CUDA_BIN": found["music3_bin"]}
    for key, name in (("bolt_model", "BOLT_MODEL"), ("bolt_draft", "BOLT_DRAFT_MODEL"), ("drama3_model", "DRAMA3_CUDA_MODEL"),
                      ("drama3_codec", "DRAMA3_CUDA_CODEC"), ("music3_model", "MUSIC3_MODEL")):
        if data.get(key):
            defaults[name] = _path(data, key, Path())
    for key, value in defaults.items():
        os.environ.setdefault(key, str(value))
    for key, value in (data.get("env") or {}).items():  # anything else the installer recorded (BOLT_CONTEXT, ...)
        if isinstance(key, str) and isinstance(value, (str, int, float)):
            os.environ.setdefault(key, str(value))


def python() -> str:
    """The GPU venv's python.exe: what runs the render scripts and spark_generate.py (never the app's own venv)."""

    return os.environ.get("STUDIO_GPU_PYTHON") or str(paths()["python"])


def unsupported(kind: str) -> str:
    """Why a job of this kind is refused on the native tier ("" when it runs)."""

    if kind in NATIVE:
        return ""
    why = NOT_NATIVE.get(kind, f"a {kind} job has not been ported to native Windows")
    return f"Not available on Windows yet: {why}. A DGX Spark or a Mac runs it."


def translate(argv: list[str]) -> list[str]:
    """A Spark command made for bash or a Linux venv, as the native tier runs it: ``bash scripts/X.sh ...`` becomes
    the Python port (``spark/windows_scripts.py X ...``), ``python3`` and ``$PREFIX/venv/bin/python`` the GPU venv's
    python.exe. A bash script without a port is a RuntimeError naming it (never handed to WSL's bash.exe)."""

    if not argv:
        return argv
    head = str(argv[0]).replace("\\", "/")
    if head.endswith("/train/venv/bin/python"):  # musubi-tuner's own venv (spark/setup-train.sh; train\venv here)
        return [str(paths()["train_python"]), *argv[1:]]
    if head == "bash":
        script = Path(str(argv[1])) if len(argv) > 1 else Path("")
        if script.parent.name == "scripts" and script.name in PORTED:
            return [python(), "-X", "utf8", str(PACK / "spark" / "windows_scripts.py"), PORTED[script.name], *argv[2:]]
        raise RuntimeError(f"{script.name or 'that script'} has no native Windows port yet: this job does not run here")
    if head == "python3" or head.endswith("/venv/bin/python"):
        return [python(), *argv[1:]]
    return argv


def setup_hint(parts: str) -> str:
    """The command that installs these parts natively (scripts/setup-windows-cuda.ps1 resumes: what is there stays)."""

    return f"powershell -ExecutionPolicy Bypass -File scripts\\setup-windows-cuda.ps1 -SkipToolchain -Parts {parts}"


def pid_alive(pid: int) -> bool:
    """Whether process ``pid`` runs. ``os.kill(pid, 0)`` asks that on POSIX; on Windows signal 0 is CTRL_C_EVENT, which
    os.kill sends to a console process group (or fails with WinError 11), so Windows is asked with OpenProcess."""

    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: a process is there, another user's
    try:
        code = wintypes.DWORD()
        return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        api.CloseHandle(handle)


def comfy_argv(action: str) -> list[str]:
    """spark/comfy_win.py (the native port of spark/comfy.sh): start, stop, status."""

    return [python(), "-X", "utf8", str(PACK / "spark" / "comfy_win.py"), action]


def status() -> dict:
    """What the page and /api/state say about the tier."""

    on = active()
    found = paths()
    return {"active": on, "why": _STATE.get("why", why_not()) if not on else "",
            "pool_mb": config().get("cuda_pool_mb") if on else None,
            "engines": {"tf_h3": found["tf_h3"].is_file(), "bolt": found["bolt_bin"].is_file(),
                        "drama3": found["drama3_bin"].is_file(), "music3": found["music3_bin"].is_file()} if on else {},
            "not_native": sorted(NOT_NATIVE) if on else [],
            "h3_available": on and not h3_why_not(), "h3_why": h3_why_not() if on else why_not()}
