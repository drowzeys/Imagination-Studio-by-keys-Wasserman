"""Offline Windows installation audit: never downloads, renders or starts a model/browser."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
COMPONENTS = ("ui", "gpu", "comfy", "storyboard", "sound", "brain", "media", "graphics")
METADATA = """
import importlib.metadata as m, json, platform, sys
required = json.loads(sys.argv[1])
found = {}
for name in required:
    try:
        found[name] = m.version(name)
    except m.PackageNotFoundError:
        found[name] = None
print(json.dumps({"platform": sys.platform, "machine": platform.machine(),
                  "python": list(sys.version_info[:2]), "isolated": sys.prefix != sys.base_prefix,
                  "versions": found}))
"""


def pinned_requirements(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--extra-index-url "):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9.+_-]+)", line)
        if not match:
            raise ValueError(f"{path.name}: expected an exact package pin, got {line!r}")
        result[match[1]] = match[2]
    return result


def dependencies(python: Path, manifest: Path, *, arm64: bool) -> dict:
    required = pinned_requirements(manifest)
    done = subprocess.run([str(python), "-I", "-X", "utf8", "-c", METADATA, json.dumps(required)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    found = json.loads(done.stdout)
    if found["platform"] != "win32" or found["python"] != [3, 13] or not found["isolated"]:
        raise RuntimeError("The installed environment must be an isolated Windows Python 3.13")
    if arm64 and found["machine"].lower() not in ("arm64", "aarch64"):
        raise RuntimeError("The GPU environment must be native ARM64, not an emulated Python")
    wrong = [f"{name}: expected {version}, found {found['versions'].get(name) or 'missing'}"
             for name, version in required.items() if found["versions"].get(name) != version]
    if wrong:
        raise RuntimeError("; ".join(wrong))
    subprocess.run([str(python), "-I", "-m", "pip", "check"], capture_output=True, text=True,
                   encoding="utf-8", timeout=30, check=True)
    return {"packages": len(required), "python": "3.13", "architecture": found["machine"],
            "scope": "distribution metadata and pip check only; torch was not imported"}


def check(component: str, root: Path) -> dict:
    if component in ("ui", "gpu", "comfy"):
        environment = "ui" if component == "ui" else "gpu"
        manifests = {"ui": "requirements-windows.txt", "gpu": "requirements-windows-gpu.txt",
                     "comfy": "requirements-windows-comfy-constraints.txt"}
        manifest = ROOT / "app" / manifests[component]
        result = dependencies(root / f"{environment}-venv" / "Scripts" / "python.exe", manifest, arm64=environment == "gpu")
        if component == "comfy":
            import windows_storyboard
            windows_storyboard.tools().checker().verify_source(root / "ComfyUI")
            result["scope"] += "; ComfyUI pin and clean checkout verified without launching it"
        return result
    if component == "storyboard":
        import windows_storyboard
        windows_storyboard.verify_weights(windows_storyboard.folder())
        return {"scope": "cached SD-Turbo SHA-256 verified; no inference",
                "qualification": windows_storyboard.status()}
    if component == "sound":
        import windows_sfx
        import windows_sound
        result = windows_sfx.verify(windows_sound.folder())
        return {**result, "qualification": windows_sound.status()}
    if component == "brain":
        import windows_brain
        cached = root / "llama-cpp" / windows_brain.RUNTIME_VERSION
        if not cached.is_dir():
            raise RuntimeError("The cached small-brain installation is missing")
        windows_brain.install(cached, False)
        return {"scope": "cached Qwen3 and runtime archives verified; no brain process started"}
    if component == "media":
        import windows_media
        if not windows_media.folder().is_dir():
            raise RuntimeError("The pinned media installation is missing")
        return windows_media.install(False)
    if component == "graphics":
        import graphics
        import windows_media
        # Discovery uses a per-process PATH in the app, not the machine's global PATH.
        original = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = windows_media.search_path(original)
            state = graphics.ready()
        finally:
            os.environ["PATH"] = original
        if not state["ok"]:
            raise RuntimeError(state["why"])
        package = json.loads((graphics.install_dir() / "node_modules" / "gsap" / "package.json").read_text(encoding="utf-8"))
        if package.get("version") != graphics.GSAP:
            raise RuntimeError("GSAP is not the pinned version")
        expected = json.loads((ROOT / "scripts" / "windows-graphics" / "package-lock.json").read_text(encoding="utf-8"))
        installed = json.loads((graphics.install_dir() / "package-lock.json").read_text(encoding="utf-8"))
        if installed != expected:
            raise RuntimeError("The installed graphics lockfile differs from the checked dependency lock")
        return {"node_version": state["node_version"], "hyperframes": state["hyperframes"],
                "scope": "installed pins, patched probe and existing render receipt checked; browser not launched"}
    raise ValueError(f"Unknown component: {component}")


def audit(components: list[str], root: Path) -> dict:
    results = []
    for component in dict.fromkeys(components):
        try:
            result = check(component, root)
            results.append({"component": component, "ok": True, **result})
        except subprocess.CalledProcessError as error:
            said = (error.stderr or error.stdout or str(error)).strip()
            results.append({"component": component, "ok": False, "error": said})
        except (ImportError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
            results.append({"component": component, "ok": False, "error": str(error)})
    return {"ok": all(row["ok"] for row in results), "offline": True,
            "scope": "installed files/dependencies only; does not qualify or requalify render routes", "components": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", nargs="+", choices=COMPONENTS, default=["ui"])
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print(json.dumps({"ok": False, "error": "This installation check requires Windows"}), file=sys.stderr)
        return 1
    result = audit(args.components, Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
