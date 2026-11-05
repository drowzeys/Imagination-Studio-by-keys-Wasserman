"""Preview/remove only exclusive native model weight files. No directory recursion or backend termination."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import stat

import model_catalog
import windows_runtime

WEIGHTS = {".safetensors", ".pt", ".pth", ".bin", ".gguf"}


def manifest(key: str) -> set[Path]:
    """Relative paths for ownership comparison, including checkpoint metadata and checked-file aliases."""
    spec = model_catalog.model(key)
    result = set()
    if spec.get("checkpoint_parts"):
        for folder in spec["checkpoint_parts"]:
            source = model_catalog.catalogue()["checkpoints"][folder]
            sizes = model_catalog.catalogue()["file_sizes"].get(source["repo"] + "@" + source["revision"], {})
            names = sizes.get("snapshot_files")
            if names is None:
                raise ValueError(f"No complete checkpoint file inventory for {key}; removal is unavailable.")
            result.update(Path(folder) / name for name in names)
        if key == "voice":
            result.add(Path("drama-3") / "codec-v1.safetensors")
        return result
    for part in spec.get("download_parts", [spec["part"]]):
        for _, folder, names in model_catalog.parts().get(part, []):
            for name in names:
                if spec.get("kind") == "image" and name.startswith("diffusion_models/") and name != "diffusion_models/" + spec["transformer"]:
                    continue
                result.add(Path(folder) / name)
        result.update(Path(folder) / target for _, _, _, folder, target, _ in model_catalog.checked_downloads().get(part, []))
    return result


def safe_file(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("A catalog path escapes the model folder.")
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("A model link points outside the model folder; it was not removed.")
    current = candidate
    while current != root:
        if current.exists() or current.is_symlink():
            attributes = current.lstat().st_file_attributes if os.name == "nt" else 0
            if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("Linked/junction model files are not removed automatically.")
        current = current.parent
    return candidate


def plan(key: str, root: Path) -> dict:
    for variable in ({"voice": ("DRAMA3_CUDA_MODEL", "DRAMA3_CUDA_CODEC"),
                      "music": ("MUSIC3_MODEL",), "bolt": ("BOLT_MODEL", "BOLT_DRAFT_MODEL",)}.get(key, ())):
        if os.environ.get(variable):
            path = Path(os.environ[variable]).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError(f"{variable} points outside the selected model folder. Review that installation manually.")
    own = manifest(key)
    shared = set()
    for other in model_catalog.catalogue()["models"]:
        if other != key:
            shared.update(manifest(other).intersection(own))
    exclusive = []
    for relative in sorted(own - shared):
        if relative.suffix.lower() not in WEIGHTS:
            continue
        path = safe_file(root, relative)
        if path.is_file():
            found = path.stat()
            # Hard links can be loaders' aliases or other installs: preserve rather than claim disk reclamation.
            if found.st_nlink != 1:
                raise ValueError(f"{path.name} has hard-linked copies; review those manually before removing this model.")
            exclusive.append({"path": str(path), "bytes": found.st_size, "mtime": found.st_mtime_ns})
    return {"model": key, "files": exclusive, "bytes": sum(row["bytes"] for row in exclusive),
            "shared_files_preserved": len(shared)}


def require_stopped():
    ports = {int(os.environ.get("PORT", "7870")), int(os.environ.get("SPARK_COMFY_PORT", "8190"))}
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
        except ConnectionRefusedError:
            continue
        except OSError as error:
            raise RuntimeError("Could not establish whether Studio is stopped; no weights were removed.") from error
        raise RuntimeError("Stop Studio and its model backends through their owning launcher before uninstalling weights.")


def remove(key: str, root: Path, expected: dict):
    require_stopped()
    lock = Path(os.environ.get("STUDIO_GPU_LOCK", Path.home() / "studio-gpu.lock.d"))
    if lock.exists():
        raise RuntimeError("The GPU lock is held. Stop the owning model engine first; it was not terminated.")
    if plan(key, root) != expected:
        raise RuntimeError("Model files changed after the preview; nothing was removed.")
    for row in expected["files"]:
        path = Path(row["path"])
        safe_file(root, path.relative_to(root))
        found = path.stat()
        if (found.st_size, found.st_mtime_ns, found.st_nlink) != (row["bytes"], row["mtime"], 1):
            raise RuntimeError("A model file changed during removal. Remaining files were not removed.")
        path.unlink()
        print("Removed " + path.name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(model_catalog.catalogue()["models"]), required=True)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = (args.models or windows_runtime.model_root()).absolute()
        preview = plan(args.model, root)
        if args.plan_only:
            print(json.dumps(preview))
            return 0
        print(f"Exclusive model weights: {len(preview['files'])} files, {preview['bytes'] / 1e9:.2f} GB.")
        print(f"Preserving {preview['shared_files_preserved']} shared catalog files, metadata, engines, environments and media.")
        for row in preview["files"]:
            print("  " + row["path"])
        if not preview["files"]:
            print("No exclusive weight files to remove.")
            return 0
        if input("Type the model id '" + args.model + "' to remove these files: ").strip() != args.model:
            print("Cancelled; nothing removed.")
            return 0
        remove(args.model, root, preview)
        return 0
    except (OSError, ValueError, RuntimeError, EOFError) as error:
        print("Model removal refused: " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
