"""Independent, locally qualified Windows features that do not need the full H3 installation."""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import contextlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import threading

from feature_checks import EvidenceStore, matches_qualification
from windows_qualification import version_family
import windows_media
from windows_memory import WindowsSignals, host_memory, physical_memory_mb

PACK = Path(__file__).resolve().parents[1]
KINDS = ("hlg", "finish")
COMMIT_MB = 4096
MAX_SECONDS = 10
MAX_SIDE = 1280
MAX_PIXELS = 1280 * 720
FPS = 24
MAX_CLIPS = 12
CHECK = r"powershell -ExecutionPolicy Bypass -File scripts\check-windows-features.ps1"
TITLES = {"hlg": "HDR grading", "finish": "Tesseract Finish"}
# Bump when the shared server runner changes this profile's execution contract, not for UI/API presentation.
RUNNER_CONTRACT = 1
FEATURE_OPERATIONS = ("headroom", "watch", "check_media", "check_finish")


def saved(kind: str, suffix: str) -> dict:
    if suffix not in ("", "-attempt"):
        raise ValueError("invalid qualification record")
    return EvidenceStore(folder()).read(kind, "attempt" if suffix else "pass")


def save_attempt(kind: str, attempt: dict) -> None:
    EvidenceStore(folder()).write(kind, attempt, "attempt")


def save_pass(kind: str, receipt: dict) -> None:
    EvidenceStore(folder()).write(kind, receipt)


def folder() -> Path:
    return Path(os.environ.get("STUDIO_WINDOWS_FEATURE_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "ImaginationStudio" / "qualification")


def prerequisites(kind: str) -> str:
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name, path=windows_media.search_path(os.environ.get("PATH", ""))):
            return "Install the native media tools with scripts\\setup-windows-ffmpeg.ps1."
    if kind == "finish":
        import finish
        path, version = finish.first_working(finish.tsrct_places())
        if not path or version != finish.TSRCT_VERSION or not all(finish.font_files().values()):
            return "Install pinned Tesseract and fonts with scripts\\setup-windows-finish.ps1."
    return ""


@lru_cache(maxsize=32)
def source_signature(source: str, names: tuple[str, ...] | None = None, exclude: tuple[str, ...] = (),
                     dependencies: bool = False) -> str:
    tree = ast.parse(source)
    if names is not None:
        definitions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        definitions[target.id] = node
        selected = set(names)
        if not selected.issubset(definitions):
            raise ValueError("the rendering contract is missing an expected operation")
        if dependencies:
            pending = list(selected)
            while pending:
                used = {node.id for node in ast.walk(definitions[pending.pop()]) if isinstance(node, ast.Name)}
                extra = used.intersection(definitions).difference(selected)
                selected.update(extra)
                pending.extend(extra)
        tree.body = [node for node in tree.body if any(definitions[name] is node for name in selected)]
    else:
        tree.body = [node for node in tree.body if getattr(node, "name", None) not in exclude
                     and not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                              and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")]

    class WithoutDocstrings(ast.NodeTransformer):
        def generic_visit(self, node):
            super().generic_visit(node)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    if isinstance(node.body[0].value.value, str):
                        node.body = node.body[1:]
            return node

    return hashlib.sha256(ast.dump(WithoutDocstrings().visit(tree), include_attributes=False).encode()).hexdigest()


def _runtime_fingerprint(kind: str) -> dict:
    import numpy
    files = {}
    tools = {name: Path(shutil.which(name, path=windows_media.search_path(os.environ.get("PATH", ""))))
             for name in ("ffmpeg", "ffprobe")}
    tools["python"] = Path(sys.executable)
    if kind == "finish":
        import finish
        executable = Path(finish.first_working(finish.tsrct_places())[0])
        tools["tesseract"] = executable
        tools.update({f"tesseract/{path.name}": path for path in executable.parent.glob("*.dll")})
        tools.update({role: Path(path) for role, path in finish.font_files().items()})
    for name, path in tools.items():
        stat = path.stat()
        files[name] = [hashlib.sha256(str(path.resolve()).encode()).hexdigest(), stat.st_size, stat.st_mtime_ns]
    return {"files": files, "python": sys.version, "numpy": numpy.__version__, "machine": platform.machine(),
            "physical_mb": physical_memory_mb(), "host_mb": host_memory()[0],
            "scope": {"seconds": MAX_SECONDS, "side": MAX_SIDE, "pixels": MAX_PIXELS, "fps": FPS,
                      "clips": MAX_CLIPS, "job_commit_mb": COMMIT_MB}}


def fingerprint(kind: str) -> dict:
    found = _runtime_fingerprint(kind)
    found.update(schema=2, runner_contract=RUNNER_CONTRACT)
    sources = {"windows_features.py": (FEATURE_OPERATIONS, ()),
               "windows_process.py": (None, ()),
               "windows_memory.py": (("host_memory", "physical_memory_mb", "process_memory", "WindowsSignals"), ()),
               "windows_media.py": (("folder", "bin_folder", "search_path"), ()),
               "graphics.py": (("probe", "search_path"), ())}
    if kind == "finish":
        sources.update({"finish.py": (None, ("attach", "ready", "remote_host", "probe_remote")),
                        "suite.py": (("probe", "fcpxml", "edl"), ())})
    else:
        sources.update({"hlg.py": (None, ())})
    for name, (names, exclude) in sources.items():
        found["files"][f"app/{name}"] = source_signature((PACK / "app" / name).read_text(encoding="utf-8"), names, exclude,
                                                       dependencies=name == "suite.py")
    return found


def migrate_legacy_receipt(kind: str, previous_feature_source: Path) -> dict:
    """Reindex existing evidence only with an exact historical source proof and an unchanged render contract."""
    receipt = saved(kind, "")
    previous = receipt.get("fingerprint")
    if receipt.get("ok") is not True or not isinstance(previous, dict) or previous.get("schema"):
        raise ValueError("this is not a successful legacy qualification")
    proof_bytes = previous_feature_source.read_bytes()
    proof = proof_bytes.decode("utf-8")
    if source_signature(proof, FEATURE_OPERATIONS) != source_signature(Path(__file__).read_text(encoding="utf-8"), FEATURE_OPERATIONS):
        raise ValueError("the rendering operations changed; previous evidence cannot be reindexed")
    legacy = _runtime_fingerprint(kind)
    sources = ["windows_features.py", "windows_process.py", "windows_memory.py", "windows_media.py",
               "actions.py", "hlg.py", "server.py"] + (["finish.py", "suite.py"] if kind == "finish" else [])
    for name in sources:
        content = proof_bytes if name == "windows_features.py" else (PACK / "app" / name).read_bytes()
        legacy["files"][f"app/{name}"] = hashlib.sha256(content).hexdigest()
    legacy["files"]["checker"] = hashlib.sha256((PACK / "scripts" / "windows-features-check.py").read_bytes()).hexdigest()
    if legacy != previous:
        raise ValueError("the historical sources, runtime, hardware or scope do not exactly match the saved evidence")
    updated = {**receipt, "fingerprint": fingerprint(kind), "previous_fingerprint": previous,
               "fingerprint_migration": {"from": 1, "to": 2, "basis": "Exact historical source proof; render operations, runtime, hardware and scope unchanged"}}
    target = folder() / f"{kind}.json"
    pending = target.with_suffix(".tmp")
    pending.write_text(json.dumps(updated), encoding="utf-8")
    pending.replace(target)
    return updated


def status(kind: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"no independent Windows qualification for {kind}")
    base = {"available": False, "installed": False, "qualified": False, "state": "not_installed",
            "max_seconds": MAX_SECONDS, "max_width": MAX_SIDE, "max_height": 720, "max_pixels": MAX_PIXELS, "fps": FPS}
    try:
        why = prerequisites(kind)
        if why:
            return {**base, "why": why}
        base.update(installed=True, state="needs_qualification")
        marker = folder() / f"{kind}.json"
        if not marker.is_file():
            return {**base, "why": "Check this feature in Machine > Feature checks to enable it on this machine."}
        receipt = json.loads(marker.read_text(encoding="utf-8"))
        previous, current = receipt.get("fingerprint"), fingerprint(kind)
        if receipt.get("ok") is not True or not isinstance(previous, dict) or not matches_qualification(
                previous, current, compatibility(previous), compatibility(current)):
            return {**base, "why": "The runtime, hardware or recipe changed. Rerun its check in Machine > Feature checks."}
        return {**base, "available": True, "qualified": True, "state": "ready", "why": "",
                "runtime_updated": previous != current}
    except (OSError, ValueError, TypeError, AttributeError, SyntaxError) as error:
        return {**base, "state": "qualification_error",
                "why": f"Windows {kind} qualification could not be checked: {type(error).__name__}: {error}"}


def compatibility(value: dict) -> dict:
    keys = ("runner_contract", "machine", "physical_mb", "host_mb", "scope", "python", "numpy")
    if not all(key in value for key in keys):
        return value
    return {**{key: value[key] for key in keys if key not in ("python", "numpy")},
            "python_abi": version_family(value["python"], 2), "numpy_major": version_family(value["numpy"])}


def preflight(kind: str) -> None:
    from windows_runtime import usage_status
    found = usage_status(kind, status(kind))
    if not found["available"]:
        raise RuntimeError(found["why"])
    headroom(kind)


def headroom(kind: str) -> None:
    with WindowsSignals() as signals:
        committed, limit, _, low = signals.read()
    _, available = host_memory()
    floor = int(os.environ.get("SPARK_MEMORY_FLOOR_MB") or 0)
    if low or limit - committed < COMMIT_MB or available < max(floor, COMMIT_MB):
        raise RuntimeError(f"Windows {kind} needs {COMMIT_MB} MB of host and commit headroom"
                           f" (host available {available} MB, commit free {limit - committed} MB); try again after freeing memory.")


@contextlib.contextmanager
def watch(owned):
    stop, errors = threading.Event(), []
    with WindowsSignals() as signals:
        def monitor():
            while not stop.wait(.25):
                try:
                    committed, limit, _, low = signals.read()
                    floor = max(0, int(os.environ.get("SPARK_MEMORY_FLOOR_MB") or 0))
                    if low or limit - committed < 512 or (floor and host_memory()[1] < floor):
                        raise RuntimeError("Windows memory pressure stopped the owned feature job.")
                except (OSError, RuntimeError, ValueError) as error:
                    errors.append(str(error))
                    try:
                        owned.terminate()
                    except OSError as termination:
                        errors.append(str(termination))
                    return
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
    if errors:
        raise RuntimeError("; ".join(errors))


def check_media(info: dict) -> None:
    values = [info.get(key) for key in ("width", "height", "seconds", "fps")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("the Windows feature needs a readable clip with finite size, duration and frame rate")
    width, height, seconds, fps = values
    if (width > MAX_SIDE or height > 720 or width * height > MAX_PIXELS
            or seconds > MAX_SECONDS + 1e-6 or abs(fps - FPS) > .001):
        raise ValueError("The qualified Windows scope is at most 1280x720 pixels, ten seconds and 24 fps.")


def check_finish(plan: dict) -> None:
    if plan["size"] != "720p" or plan["fps"] != FPS or len(plan["clips"]) > MAX_CLIPS or plan.get("captions"):
        raise ValueError("Windows Finish is qualified at 720p, 24 fps, up to 12 clips, without transcription.")
    total = sum(clip["length"] for clip in plan["clips"]) + (3.5 if plan["title"] else 0)
    if not math.isfinite(total) or not 0 < total <= MAX_SECONDS:
        raise ValueError("Windows Finish is qualified for cuts up to ten seconds, including the title.")
    for role in ("song", "voice"):
        if plan.get(role):
            seconds = plan[role].get("seconds")
            if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS):
                raise ValueError("Windows Finish music and narration must be readable audio of at most ten seconds.")
    for clip in plan["clips"]:
        import graphics
        check_media(graphics.probe(Path(clip["path"])))
