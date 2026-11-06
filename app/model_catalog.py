"""Shared declarative native-model metadata; standard library only, no hardware or network probes."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

PATH = Path(__file__).with_name("model_catalog.json")


@lru_cache(maxsize=1)
def catalogue() -> dict:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("models"), dict):
        raise ValueError("The native model catalog schema is invalid.")
    for key, model in data["models"].items():
        if not isinstance(model.get("label"), str) or not isinstance(model.get("part"), str):
            raise ValueError(f"Invalid model metadata: {key}.")
        for route in model.get("runtime", {}).values():
            need = route.get("required_free_gib")
            if need is not None and (type(need) not in (int, float) or need <= 0):
                raise ValueError(f"Invalid runtime memory requirement: {key}.")
            if route.get("basis") not in ("policy", "unknown"):
                raise ValueError(f"Invalid runtime requirement basis: {key}.")
    for rows in data["downloads"].values():
        for row in rows:
            if not row.get("repo") or not row.get("folder") or not isinstance(row.get("files"), list):
                raise ValueError("Invalid catalog download manifest.")
            if any(not isinstance(name, str) or name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/")
                   for name in row["files"]):
                raise ValueError("Catalog download paths must stay inside the model folder.")
    for metadata in data.get("file_sizes", {}).values():
        if any(type(size) is not int or size < 0 for size in metadata["files"].values()):
            raise ValueError("Catalog byte sizes must be nonnegative integers.")
    return data


def model(key: str) -> dict:
    return catalogue()["models"][key]


def required_free_gib(key: str, route: str = "cuda") -> float:
    value = model(key)["runtime"][route]["required_free_gib"]
    if type(value) not in (int, float) or value <= 0:
        raise ValueError(f"No runtime admission requirement is established for {key}/{route}.")
    return float(value)


def parts() -> dict:
    return {key: [(row["repo"], row["folder"], list(row["files"])) for row in rows]
            for key, rows in catalogue()["downloads"].items()}


def checkpoints() -> dict:
    return {folder: (row["repo"], row["revision"]) for folder, row in catalogue()["checkpoints"].items()}


def checked_downloads() -> dict:
    return {part: [(row["repo"], row["revision"], row["file"], row["folder"], row["target"], row["sha256"])
                   for row in rows] for part, rows in catalogue().get("checked_downloads", {}).items()}


def image_specs() -> dict:
    return {key: (row["label"], row["folder"], row["transformer"], row["part"])
            for key, row in catalogue()["models"].items() if row.get("kind") == "image"}


def download_rows(key: str) -> list[dict]:
    """Selected components, including shared text/decoder files, never another image variant."""
    spec = model(key)
    if spec.get("checkpoint_parts"):
        return [{"repo": catalogue()["checkpoints"][folder]["repo"],
                 "revision": catalogue()["checkpoints"][folder]["revision"], "files": None}
                for folder in spec["checkpoint_parts"]]
    rows = []
    for part in spec.get("download_parts", [spec["part"]]):
        for source in catalogue()["downloads"].get(part, []):
            names = list(source["files"])
            if spec.get("kind") == "image":
                names = [name for name in names if not name.startswith("diffusion_models/")
                         or name == "diffusion_models/" + spec["transformer"]]
            if names:
                rows.append({**source, "revision": source.get("revision", "main"), "files": names})
        for source in catalogue().get("checked_downloads", {}).get(part, []):
            rows.append({"repo": source["repo"], "revision": source["revision"],
                         "folder": source["folder"], "files": [source["file"]]})
    return rows


def size_info(key: str) -> dict:
    sizes = catalogue().get("file_sizes", {})
    total, known, count = 0, 0, 0
    for row in download_rows(key):
        entry = sizes.get(row["repo"] + "@" + row["revision"], {})
        names = row["files"] if row["files"] is not None else entry.get("snapshot_files")
        if names is None:
            count += 1
            continue
        for name in names:
            count += 1
            size = entry.get("files", {}).get(name)
            if type(size) is int and size >= 0:
                total += size
                known += 1
    return {"bytes": total if count and known == count else None, "known_bytes": total,
            "known_files": known, "total_files": count, "basis": "Hugging Face repository file metadata",
            "checked_at": catalogue().get("sizes_checked_at")}
