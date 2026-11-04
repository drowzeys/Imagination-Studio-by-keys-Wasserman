"""Portable Slate projects: relative media, a screenplay and readable shot lists."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import ntpath
from pathlib import Path
import re
import shutil
import tempfile
import textwrap
import uuid
import zipfile

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageOps

from suite import MEDIA, UPLOAD_LIMIT, ZIP_BYTES, ZIP_FILES, read_json, safe_name

SCHEMA = "imagination-studio-slate-package-v1"
SCRIPT = (".fountain", ".txt", ".md", ".scriptbreak")
SINGLE = {"image", "last_image", "lastImage", "mediaPath", "reference_video", "path", "clip", "video",
          "audio", "song", "sheet", "thumbnail", "kept", "sourceScript"}
MULTIPLE = {"images", "references", "reference_videos", "reference_audios", "ref_images", "ref_videos", "ref_audios"}
LOCAL = {"job", "jobId", "turn", "turnPinned", "plate", "platePinned", "exportJob",
         "storyboardJob", "storyboardPinned", "storyboardBefore", "storyboardIndex",
         "placement", "sparks", "localEndpoint", "localModel"}
HEADERS = ("scene", "shot", "intent", "duration_s", "fps", "aspect", "size", "angle", "lens", "movement",
           "target_model", "prompt", "h3_prompt", "take", "image")


def clean(project: dict) -> dict:
    """Keep film content, but not another computer's connections or running-job bindings."""
    if not isinstance(project, dict):
        raise HTTPException(400, "this is not a Slate film")
    def walk(value):
        if isinstance(value, list):
            return [walk(one) for one in value]
        if not isinstance(value, dict):
            return value
        out = {}
        for key, one in value.items():
            if key in LOCAL:
                continue
            if key == "source" and isinstance(one, dict):
                out[key] = {k: v for k, v in one.items() if k in ("take", "shot", "scene", "engine", "model", "elapsed")}
            elif key == "brain" and isinstance(one, str) and one.startswith("local:"):
                out[key] = "auto"
            else:
                out[key] = walk(one)
        return out
    result = walk(project)
    if not isinstance(result.get("scenes"), list) or not isinstance(result.get("defaults"), dict):
        raise HTTPException(400, "this is not a Slate film")
    for key in ("characters", "locations"):
        if not isinstance(result.get(key, []), list) or any(not isinstance(one, dict) for one in result.get(key, [])):
            raise HTTPException(400, f"a film's {key} must be a list of sheets")
        for sheet in result.get(key, []):
            if not isinstance(sheet.get("images", []), list) or any(not isinstance(path, str) for path in sheet.get("images", [])):
                raise HTTPException(400, "sheet images must be a list of media paths")
    for _, shot in shots(result):
        if not isinstance(shot.get("spec") or {}, dict) or not isinstance(shot.get("tf") or {}, dict):
            raise HTTPException(400, "a shot's spec and tf settings must be objects")
        if not isinstance(shot.get("takes", []), list) or any(not isinstance(one, dict) for one in shot.get("takes", [])):
            raise HTTPException(400, "a shot's takes must be a list of take records")
        if not isinstance(shot.get("images", []), list) or any(not isinstance(one, str) for one in shot.get("images", [])):
            raise HTTPException(400, "a shot's images must be a list of media paths")
    if isinstance(result.get("importedFrom"), dict):
        result["importedFrom"].pop("import", None)
    return result


def media_refs(value, replace, key=""):
    if key in ("image", "last_image", "lastImage", "mediaPath", "reference_video", "sourceScript"):
        if value not in (None, "") and not isinstance(value, str):
            raise HTTPException(400, f"{key} must be a media/script path")
    if isinstance(value, dict):
        return {name: media_refs(one, replace, name) for name, one in value.items()}
    if isinstance(value, list):
        return [media_refs(one, replace, key) for one in value]
    if isinstance(value, str) and value and (key in SINGLE or key in MULTIPLE):
        suffix = Path(value.replace("\\", "/")).suffix.lower()
        if key == "sourceScript" or suffix in MEDIA or key in ("image", "mediaPath", "reference_video", "images"):
            return replace(value, key == "sourceScript")
    return value


def file_path(home: Path, value: str, suffixes: tuple) -> Path:
    if not isinstance(value, str) or not value:
        raise HTTPException(400, "a media/script reference must be a nonempty path")
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if "\0" in value or ntpath.isabs(value) and not path.is_absolute():
        raise HTTPException(400, f"a portable file must be inside this Studio: {value[:160]}")
    path = (home / path).resolve()
    root = home.resolve()
    if (not path.is_relative_to(root) or path == root
            or any(part.startswith(".") for part in path.relative_to(root).parts)
            or path.suffix.lower() not in suffixes):
        raise HTTPException(400, f"a portable file must be visible media inside this Studio: {value[:160]}")
    return path


def within(home: Path, value: str, suffixes: tuple) -> Path:
    path = file_path(home, value, suffixes)
    if not path.is_file():
        raise HTTPException(400, f"a referenced file is missing; restore it or remove its reference: {value[:160]}")
    return path


def screenplay(project: dict, home: Path) -> Path | None:
    if project.get("sourceScript"):
        path = file_path(home, project["sourceScript"], SCRIPT)
        return path if path.is_file() else None
    source = project.get("importedFrom") or {}
    ident = source.get("import") if isinstance(source, dict) else None
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident):
        return None
    folder = home / "uploads" / "suite" / ident
    found = [folder / ("upload" + suffix) for suffix in SCRIPT if (folder / ("upload" + suffix)).is_file()]
    if not found:
        candidates = sorted((folder / "files").rglob("*.fountain")) if (folder / "files").is_dir() else []
        if len(candidates) != 1:
            return None
        found = candidates
    return within(home, found[0].relative_to(home).as_posix(), SCRIPT)


def shots(project: dict):
    for scene in project.get("scenes", []):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots", []), list):
            raise HTTPException(400, "a film's scenes contain shot lists")
        for shot in scene.get("shots", []):
            if not isinstance(shot, dict):
                raise HTTPException(400, "a shot must be an object")
            yield scene, shot


def representative(shot: dict) -> str:
    return (shot.get("tf") or {}).get("image") or next(iter(shot.get("images") or []), "")


def shot_lists(project: dict) -> tuple[str, str]:
    import slate
    table = io.StringIO(newline="")
    writer = csv.writer(table, lineterminator="\n")
    writer.writerow(HEADERS)
    markdown = ["# " + str(project.get("name") or "Untitled film"), ""]
    for scene in project["scenes"]:
        markdown += ["## " + str(scene.get("name") or "Scene"), str(scene.get("synopsis") or ""), ""]
        for shot in scene.get("shots", []):
            spec, tf = shot.get("spec") or {}, shot.get("tf") or {}
            writer.writerow([scene.get("name", ""), shot.get("name", ""), shot.get("intent", ""),
                             *[spec.get(key, "") for key in ("durationSec", "fps", "aspectRatio", "size", "angle", "lens", "movement")],
                             shot.get("targetModel") or "minimax-h3", shot.get("prompt", ""), tf.get("h3", ""),
                             slate.best_take(shot) or "", representative(shot)])
            markdown += ["### " + str(shot.get("name") or "Shot"), "", str(shot.get("intent") or ""), "",
                         str(shot.get("prompt") or ""), ""]
            if representative(shot):
                markdown += [f"![Storyboard]({representative(shot)})", ""]
    return table.getvalue(), "\n".join(markdown)


def contact_sheets(project: dict, sources: dict[str, Path], directory: Path) -> list[Path]:
    items = list(shots(project))
    pages = []
    for start in range(0, len(items), 12):
        group = items[start:start + 12]
        canvas = Image.new("RGB", (768, ((len(group) + 1) // 2) * 296), "white")
        draw = ImageDraw.Draw(canvas)
        for offset, (scene, shot) in enumerate(group):
            x, y = (offset % 2) * 384, (offset // 2) * 296
            path = representative(shot)
            if path:
                try:
                    with Image.open(sources[path]) as image:
                        tile = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGB"), (384, 216))
                except (OSError, ValueError, Image.DecompressionBombError) as error:
                    raise HTTPException(400, "a storyboard image cannot be read: " + path) from error
                canvas.paste(tile, (x + (384 - tile.width) // 2, y + (216 - tile.height) // 2))
            else:
                draw.rectangle((x, y, x + 383, y + 215), fill="#eeeeee")
                draw.text((x + 12, y + 96), "No storyboard still", fill="black")
            caption = f"{start + offset + 1}. {scene.get('name') or 'Scene'} / {shot.get('name') or 'Shot'}"
            caption += " | " + str(shot.get("intent") or shot.get("prompt") or "").replace("\n", " ")
            lines = textwrap.wrap(caption, width=54)[:4]
            draw.multiline_text((x + 8, y + 222), "\n".join(lines), fill="black", spacing=3)
        path = directory / f"contact-sheet-{len(pages) + 1:03d}.jpg"
        canvas.save(path, quality=90)
        pages.append(path)
    return pages


def package(project: dict, home: Path) -> dict:
    if not isinstance(project, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(project.get("id", ""))):
        raise HTTPException(400, "bad portable project id")
    source_script = screenplay(project, home)
    data = clean(copy.deepcopy(project))
    data.pop("sourceScript", None)
    data["portablePackage"] = SCHEMA
    names, files = {}, {}

    def collect(value, script=False):
        source = within(home, value, SCRIPT if script else MEDIA)
        if source not in names:
            name = f"media/{len(names) + 1:04d}{source.suffix.lower()}"
            names[source] = name
            files[name] = source
        return names[source]

    data = media_refs(data, collect)
    notes = ["Model weights, local connection settings and running-job bindings are not included."]
    if source_script:
        name = "script/original" + source_script.suffix.lower()
        data["sourceScript"] = name
        files[name] = source_script
    else:
        notes.append("No original screenplay file is available; project scene/shot text is included.")
    exports = home / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".slate-package-", dir=exports) as temporary:
        staging = Path(temporary)
        payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        if len(payload) > 20 * 1024 ** 2:
            raise HTTPException(400, "the project exceeds the suite's 20 MB JSON limit")
        (staging / "project.json").write_bytes(payload)
        text_csv, text_md = shot_lists(data)
        (staging / "shots.csv").write_text(text_csv, encoding="utf-8", newline="")
        (staging / "shots.md").write_text(text_md, encoding="utf-8")
        for name in ("project.json", "shots.csv", "shots.md"):
            files[name] = staging / name
        for path in contact_sheets(data, files, staging):
            files[path.name] = path
        total = sum(path.stat().st_size for path in files.values())
        if len(files) + 1 > ZIP_FILES or total > min(ZIP_BYTES, UPLOAD_LIMIT) - (1 << 20):
            raise HTTPException(400, "this film exceeds the suite's portable ZIP file/size limits")
        if shutil.disk_usage(exports).free < total + (1 << 20):
            raise HTTPException(400, "there is not enough free disk for the portable ZIP")
        filename = f"{project['id']}-{uuid.uuid4().hex[:8]}.slate.zip"
        target = exports / filename
        partial = target.with_suffix(".partial")
        manifest = {"schema": SCHEMA, "project": "project.json", "source_script": data.get("sourceScript"),
                    "notes": notes, "files": {}}
        try:
            with zipfile.ZipFile(partial, "x", zipfile.ZIP_STORED) as bundle:
                for name, source in files.items():
                    before = source.stat()
                    digest, written = hashlib.sha256(), 0
                    with source.open("rb") as reader, bundle.open(name, "w", force_zip64=True) as writer:
                        while chunk := reader.read(1 << 20):
                            written += len(chunk)
                            if written > before.st_size:
                                raise HTTPException(409, "a media file changed during export; retry")
                            digest.update(chunk)
                            writer.write(chunk)
                    if written != before.st_size or source.stat().st_mtime_ns != before.st_mtime_ns:
                        raise HTTPException(409, "a media file changed during export; retry")
                    manifest["files"][name] = {"size": written, "sha256": digest.hexdigest()}
                bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            if partial.stat().st_size > UPLOAD_LIMIT:
                raise HTTPException(400, "the portable ZIP exceeds the upload limit")
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return {"path": target.relative_to(home).as_posix(), "filename": filename, "notes": notes,
            "media_files": len(names), "shots": sum(1 for _ in shots(data))}


def unpacked_project(manifest: dict, files: dict[str, Path], base: str, home: Path) -> dict:
    if manifest.get("schema") != SCHEMA or manifest.get("project") != "project.json":
        raise HTTPException(400, "unsupported portable Slate package schema")
    if not isinstance(manifest.get("notes", []), list) or any(not isinstance(note, str) for note in manifest.get("notes", [])):
        raise HTTPException(400, "portable package notes must be a list of text")
    entries = manifest.get("files")
    if not isinstance(entries, dict) or "project.json" not in entries or "manifest.json" in entries:
        raise HTTPException(400, "the portable package has an invalid file manifest")
    prefix = base.rstrip("/") + "/" if base else ""
    actual = {name[len(prefix):]: path for name, path in files.items() if name.startswith(prefix)}
    if set(actual) != {*entries, "manifest.json"}:
        raise HTTPException(400, "portable ZIP files do not match its manifest")
    for name, expected in entries.items():
        if (not isinstance(name, str) or safe_name(name) != name or ":" in name
                or any(part.startswith(".") or part.endswith((" ", ".")) for part in name.split("/"))):
            raise HTTPException(400, "the portable manifest has an invalid relative filename")
        if not isinstance(expected, dict) or type(expected.get("size")) is not int or expected["size"] < 0:
            raise HTTPException(400, "the portable manifest has an invalid file size")
        path = actual[name]
        if path.stat().st_size != expected["size"]:
            raise HTTPException(400, "a portable file has the wrong size: " + name)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest() != expected.get("sha256"):
            raise HTTPException(400, "a portable file failed SHA-256 verification: " + name)
    data = clean(read_json(actual["project.json"]))
    if data.get("sourceScript") != manifest.get("source_script"):
        raise HTTPException(400, "the portable screenplay reference does not match its manifest")

    def imported(value, script=False):
        if (safe_name(value) != value or value not in entries
                or Path(value).suffix.lower() not in (SCRIPT if script else MEDIA)):
            raise HTTPException(400, "a portable project reference is not included in its ZIP: " + value[:160])
        return actual[value].resolve().relative_to(home.resolve()).as_posix()
    data = media_refs(data, imported)
    if manifest.get("source_script") and "sourceScript" not in data:
        raise HTTPException(400, "the screenplay reference is missing from the portable project")
    data.pop("portablePackage", None)
    return data
