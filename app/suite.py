"""The filmmaker suite: files from Sam Wasserman's other apps taken in, and the Studio's work handed on to them.

    GET  /api/suite                        the apps, what each one is, and what goes between it and the Studio
    POST /api/suite/import                 a file of one of them (multipart "file", optional "app"): read into shots,
                                           cast and places, its pictures and clips kept under uploads/suite/<id>/
    GET  /api/suite/imports                what was taken in
    POST /api/suite/imports/{id}/slate     a Slate film made from an import
    POST /api/suite/imports/{id}/motion    a Blockout export handed to Motion Previs as a production pack
    GET  /api/export/timeline/{project}.fcpxml | .edl   a Production timeline for DaVinci Resolve or any editor
    GET  /api/export/dailies.zip?jobs=a,b  finished clips with their settings, for Circle Take's ingest
    GET  /api/export/callsheet-entry       the line Call Sheet's data/apps.json needs to list the Studio

Every format read here is the one that app writes (its README and its export code); nothing in an import runs, and
a ZIP is taken apart by hand: no absolute paths, no "..", no links, and a cap on its files and their size.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

UPLOAD_LIMIT = 2 * 1024 ** 3      # one uploaded file
ZIP_FILES, ZIP_BYTES = 2000, 4 * 1024 ** 3   # what one ZIP may hold, unpacked
FPS = 24
MEDIA = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".m4v", ".webm", ".wav", ".mp3", ".m4a", ".aac", ".flac")
TEXT = (".json", ".txt", ".md", ".fountain", ".scriptbreak", ".csv", ".srt", ".fdx")
STILLS = (".png", ".jpg", ".jpeg", ".webp")
CLIPS = (".mp4", ".mov", ".m4v", ".webm")


def local(request: Request, json_body: bool = False) -> None:
    """The same check as the updater's: this computer (or its Tailscale proxy), the Studio's own page, and JSON for a
    JSON post. A multipart import passes ``json_body`` False."""

    host = request.url.hostname or ""
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient") or not (
            host in ("localhost", "127.0.0.1", "::1", "testserver") or host.endswith(".ts.net")):
        raise HTTPException(403, "Do this on this computer or through its Tailscale address.")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
        raise HTTPException(403, "Do this from the Studio page.")
    if json_body and request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Send this from the Studio page.")


# ---------- reading what was uploaded ----------

def shot(number: int, **given) -> dict:
    """One shot in the import's common shape."""

    out = {"id": f"s{number:03d}", "title": "", "scene": "", "action": "", "camera": "", "dialogue": [], "seconds": None,
           "image": None, "reference_video": None, "notes": ""}
    out.update({key: value for key, value in given.items() if key in out and value not in (None, "")})
    try:
        out["seconds"] = round(float(out["seconds"]), 3) if out["seconds"] not in (None, "") else None
    except (TypeError, ValueError):
        out["seconds"] = None
    out["dialogue"] = [{"character": str(line.get("character") or "").strip(), "line": str(line.get("line") or "").strip()}
                       for line in out["dialogue"] or [] if isinstance(line, dict) and str(line.get("line") or "").strip()]
    for key in ("title", "scene", "action", "camera", "notes"):
        out[key] = str(out[key] or "").strip()
    return out


def safe_name(name: str) -> str | None:
    """A ZIP entry's path when it stays inside the folder it is unpacked into, else None."""

    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def unpack(archive: Path, folder: Path) -> dict[str, Path]:
    """The ZIP's media and text files into ``folder`` by their own paths, refusing the whole ZIP when any entry would
    land outside it or is a link. Returns {path in the ZIP: file on disk}; macOS's __MACOSX copies are left out."""

    try:
        bundle = zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        raise HTTPException(400, "this is not a ZIP file that can be read")
    with bundle:
        entries = [item for item in bundle.infolist() if not item.is_dir()]
        if len(entries) > ZIP_FILES or sum(item.file_size for item in entries) > ZIP_BYTES:
            raise HTTPException(400, f"this ZIP holds more than {ZIP_FILES} files or {ZIP_BYTES // 1024 ** 3} GB")
        for item in entries:
            if safe_name(item.filename) is None or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise HTTPException(400, f"this ZIP has a file that would land outside its folder: {item.filename[:120]}")
        out, root = {}, folder.resolve()
        for item in entries:
            name = safe_name(item.filename)
            if name.startswith("__MACOSX/") or Path(name).name.startswith("._") or Path(name).suffix.lower() not in MEDIA + TEXT:
                continue
            target = (folder / name).resolve()
            if root not in target.parents:
                raise HTTPException(400, "this ZIP has a file that would land outside its folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with bundle.open(item) as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > item.file_size + 1024:  # a header that lies about the size
                        raise HTTPException(400, "this ZIP's sizes do not match its contents")
                    sink.write(chunk)
            out[name] = target
        return out


def read_text(path: Path, limit: int = 20 * 1024 ** 2) -> str:
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(400, f"{path.name} is over {limit // 1024 ** 2} MB of text")
    return raw.decode("utf-8-sig", errors="replace")


def read_json(path: Path):
    try:
        return json.loads(read_text(path))
    except ValueError as error:
        raise HTTPException(400, f"{path.name} is not valid JSON: {error}")


# ---------- Fountain ----------

SCENE_HEADING = re.compile(r"^(?:\.(?!\.)|(?:INT|EXT|EST|INT\./EXT|INT/EXT|I/E)[ .])", re.I)
TRANSITION = re.compile(r"^(?:[A-Z ]+ TO:|>.*[^<])$")


def fountain(text: str) -> dict:
    """A Fountain screenplay: each scene heading opens a scene, action paragraphs become its shots' action and
    dialogue goes with the shot it is spoken in. Speakers are the cast; scene headings name the locations."""

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)  # boneyard
    text = re.sub(r"\[\[.*?\]\]", "", text, flags=re.S)  # notes
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title, at = "", 0
    if lines and re.match(r"^[A-Za-z ]+:", lines[0]):  # the title page runs to the first blank line
        while at < len(lines) and lines[at].strip():
            key, _, value = lines[at].partition(":")
            if key.strip().lower() == "title":
                title = value.strip() or (lines[at + 1].strip() if at + 1 < len(lines) else "")
            at += 1
    shots, cast, places, scene = [], {}, {}, ""
    current = None

    def flush():
        nonlocal current
        if current and (current["action"] or current["dialogue"]):
            shots.append(shot(len(shots) + 1, **current))
        current = None

    def open_shot():
        nonlocal current
        if current is None:
            current = {"scene": scene, "action": "", "dialogue": [], "title": ""}
        return current

    blocks, block = [], []
    for line in lines[at:]:
        if line.strip():
            block.append(line.rstrip())
        elif block:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    for block in blocks:
        first = block[0].strip()
        if first.startswith(("=", "#")) or first.startswith("~"):
            continue  # synopses, sections and lyrics
        if SCENE_HEADING.match(first) and not first.startswith(".."):
            flush()
            scene = re.sub(r"\s*#[^#]+#\s*$", "", first.lstrip(".")).strip().upper()
            place = re.sub(r"^(?:INT\./EXT|INT/EXT|I/E|INT|EXT|EST)[.\s]+", "", scene, flags=re.I)
            place = re.split(r"\s+-\s+", place)[0].strip()
            if place:
                places.setdefault(place.title(), {"name": place.title(), "description": scene})
            if len(block) == 1:
                continue
            block = block[1:]  # action written straight under the heading
            first = block[0].strip()
        if TRANSITION.match(first) and len(block) == 1:
            flush()
            continue
        speaker = re.sub(r"\s*\(.*?\)\s*$", "", first.lstrip("@")).strip().rstrip("^").strip()
        is_cue = len(block) > 1 and (first.startswith("@") or (speaker.upper() == speaker and re.search(r"[A-Z]", speaker)
                                                               and not first.startswith("!")))
        if is_cue:
            words = " ".join(line.strip() for line in block[1:] if not (line.strip().startswith("(") and line.strip().endswith(")")))
            if words:
                name = speaker.title()
                cast.setdefault(name, {"name": name, "description": "", "image": None})
                open_shot()["dialogue"].append({"character": name, "line": words})
            continue
        flush()  # an action paragraph is a new shot
        action = " ".join(line.strip().lstrip("!") for line in block)
        open_shot()["action"] = action
        current["title"] = action[:60]
    flush()
    for one in shots:
        one["seconds"] = one["seconds"] or 5
    return {"app": "fountain", "title": title, "shots": shots, "cast": list(cast.values()), "locations": list(places.values()), "warnings": []}



# ---------- the suite's own formats ----------

APPS = [
    {"id": "scriptbreak", "name": "ScriptBreak", "what": "Screenplay breakdown: scenes, cast, bibles, shot lists and prompt packs.",
     "imports": ["project file (.scriptbreak)", "video or frame prompt pack (.md)", "export bundle (.zip)"], "exports": []},
    {"id": "cork-board", "name": "Cork Board", "what": "Index-card story structure on an endless cork wall.",
     "imports": ["project (.json)", "outline (.fountain)"], "exports": []},
    {"id": "master-canvas", "name": "Master Canvas", "what": "An infinite canvas for pre-production: shot cards, prompts and references.",
     "imports": ["project (master-canvas-project.json)", "handoff package (.zip)"], "exports": []},
    {"id": "storyboard-reference", "name": "Storyboard Reference Studio", "what": "Reference frames pulled from clips, reframed, with a prompt each.",
     "imports": ["board export folder, zipped (prompts.json and NN_label/still.png)"], "exports": []},
    {"id": "blockout", "name": "Blockout", "what": "Grey-box 3D previs with real camera optics and marks-based blocking.",
     "imports": ["shot export folder, zipped (metadata.json, <shot>_reference.mp4, stills, prompt.txt)"],
     "exports": ["Motion Previs pack from a Blockout shot"]},
    {"id": "motion-previs-studio", "name": "Motion Previs Studio", "what": "Pose, depth and camera move read from a clip, as a production pack.",
     "imports": ["production pack (.zip)"], "exports": []},
    {"id": "slate", "name": "Slate", "what": "The prompt studio for AI filmmaking: scenes and shots with sectioned prompts.",
     "imports": ["project.json"], "exports": ["a Slate film from any import"]},
    {"id": "fountain", "name": "Fountain screenplay", "what": "Any screenplay saved as Fountain.", "imports": [".fountain"], "exports": []},
    {"id": "circle-take", "name": "Circle Take", "what": "AI dailies: takes bound to shots, quality checks, circled takes.",
     "imports": [], "exports": ["dailies package (.zip): clips named by shot with a provenance sidecar each"]},
    {"id": "stem-studio", "name": "Stem Studio", "what": "A mixed soundtrack split into dialogue, music and effects stems.",
     "imports": [], "exports": ["timeline clips carry their sound into the FCPXML and EDL"]},
    {"id": "call-sheet", "name": "Call Sheet", "what": "The suite's menu-bar launcher and dashboard.",
     "imports": [], "exports": ["an entry for its apps list"]},
    {"id": "editor", "name": "DaVinci Resolve or Final Cut Pro", "what": "A Production timeline in an editor.",
     "imports": [], "exports": ["FCPXML 1.11", "CMX 3600 EDL"]},
]


def scriptbreak(data: dict) -> dict:
    """A .scriptbreak project ({app: "scriptbreak", state}): its scenes and their shot lists, cast and locations from
    its bibles. Lengths there are in eighths of a page, so shots have no seconds."""

    state = data.get("state") if isinstance(data.get("state"), dict) else data
    bibles = state.get("bibles") or {}
    shots, places, warnings = [], {}, []
    for scene in state.get("scenes") or []:
        heading = (scene.get("slug") or "").strip() or f"Scene {scene.get('num') or ''}".strip()
        name = (scene.get("location") or scene.get("master") or "").strip()
        if name:
            places.setdefault(name.title(), {"name": name.title(), "description": heading})
        listed = scene.get("shots") or [{"num": f"{scene.get('num') or ''}A", "desc": scene.get("synopsis") or ""}]
        for one in listed:
            camera = ", ".join(bit for bit in (one.get("size"), one.get("angle"), one.get("move"), one.get("lens") and f"{one['lens']} lens") if bit)
            action = (one.get("desc") or "").strip()
            if scene.get("synopsis") and scene.get("synopsis") not in action:
                action = (action + ". " if action else "") + scene["synopsis"].strip()
            shots.append(shot(len(shots) + 1, title=f"SHOT {one.get('num') or len(shots) + 1}", scene=heading, action=action, camera=camera,
                              notes=f"{scene.get('eighths')}/8 pg" if scene.get("eighths") else ""))
    for name, sheet in (bibles.get("locations") or {}).items():
        places[name.title()] = {"name": name.title(), "description": (sheet or {}).get("desc") or places.get(name.title(), {}).get("description", "")}
    cast = [{"name": name.title(), "description": (sheet or {}).get("desc") or "", "image": None} for name, sheet in (bibles.get("characters") or {}).items()]
    known = {one["name"].lower() for one in cast}
    for scene in state.get("scenes") or []:
        for name in scene.get("characters") or []:
            if name.lower() not in known:
                known.add(name.lower())
                cast.append({"name": name.title(), "description": "", "image": None})
    if shots:
        warnings.append("ScriptBreak measures scenes in eighths of a page: each shot takes the film's default length.")
    return {"app": "scriptbreak", "title": state.get("title") or data.get("project") or "", "shots": shots, "cast": cast,
            "locations": list(places.values()), "warnings": warnings}


def scriptbreak_pack(text: str) -> dict:
    """A ScriptBreak prompt pack (.md, video or frame pack): the bibles' bullets and each scene's shot bullets."""

    title = re.match(r"#\s+(.+?)\s+—\s+.*Prompt Pack", text)
    if not title or "## SHOTS TO GENERATE" not in text and "## FRAMES TO GENERATE" not in text:
        raise HTTPException(400, "this Markdown file is not a ScriptBreak prompt pack")
    part, scene, action, shots, cast, places = "", "", "", [], [], []
    for line in text.splitlines():
        if line.startswith("## "):
            part = line[3:].strip().upper()
            continue
        bullet = re.match(r"-\s+\*\*(.+?):\*\*\s*(.*)", line)
        if part in ("CHARACTER BIBLE", "LOCATION BIBLE") and bullet:
            (cast if part.startswith("CHARACTER") else places).append(
                {"name": bullet.group(1).strip().title(), "description": bullet.group(2).strip(), **({"image": None} if part.startswith("CHARACTER") else {})})
            continue
        if not part.endswith("TO GENERATE"):
            continue
        heading = re.match(r"###\s+Scene\s+(\S+)\s+—\s+(.+?)(?:\s{2,}\(.*\))?\s*$", line)
        if heading:
            scene, action = heading.group(2).strip(), ""
            continue
        if line.startswith("Action:"):
            action = line[7:].strip()
            continue
        one = re.match(r"-\s+\*\*(?:SHOT|FRAME)\s+(\S+)\*\*\s+—\s+(.*)", line)
        if one:
            body = one.group(2).strip()
            camera, _, desc = body.partition(": ") if ": " in body else ("", "", body)
            if desc.startswith("(no shot list"):
                desc = ""
            shots.append(shot(len(shots) + 1, title=f"SHOT {one.group(1)}", scene=scene, camera=camera,
                              action=". ".join(bit for bit in (desc, action) if bit)))
    return {"app": "scriptbreak", "title": title.group(1).strip(), "shots": shots, "cast": cast, "locations": places,
            "warnings": ["A prompt pack has no lengths: each shot takes the film's default length."] if shots else []}


def cork_board(data: dict) -> dict:
    """A Cork Board project: every board's columns in order, a shot for each card."""

    characters = {one.get("id"): one for one in data.get("characters") or [] if isinstance(one, dict)}
    locations = {one.get("id"): one for one in data.get("locations") or [] if isinstance(one, dict)}
    cards = data.get("cards") or {}
    shots, warnings = [], []
    boards = data.get("boards") or []
    if len(boards) > 1:
        warnings.append(f"This project has {len(boards)} boards: their cards follow each other in board order.")
    for board in boards:
        for column in board.get("columns") or []:
            for card_id in column.get("cards") or []:
                card = cards.get(card_id)
                if not isinstance(card, dict) or card.get("status") == "cut":
                    continue
                place = (locations.get(card.get("locationId")) or {}).get("name") or ""
                heading = " ".join(bit for bit in (card.get("intExt") and card["intExt"] + ".", place.upper(), card.get("timeOfDay") and "- " + card["timeOfDay"]) if bit)
                who = [characters[i]["name"] for i in card.get("characterIds") or [] if i in characters and characters[i].get("name")]
                shots.append(shot(len(shots) + 1, title=card.get("title") or "", scene=heading or column.get("title") or "",
                                  action=card.get("synopsis") or card.get("title") or "",
                                  notes="; ".join(bit for bit in (card.get("notes"), who and "With " + ", ".join(who)) if bit)))
    cast = [{"name": one.get("name") or "Unnamed", "image": None,
             "description": "; ".join(bit for bit in (one.get("role"), one.get("want") and "wants " + one["want"], one.get("need") and "needs " + one["need"]) if bit)}
            for one in characters.values()]
    places = [{"name": one.get("name") or "Unnamed", "description": " ".join(bit for bit in (one.get("kind"), one.get("notes")) if bit)} for one in locations.values()]
    if shots:
        warnings.append("Cork Board measures cards in pages: each shot takes the film's default length.")
    return {"app": "cork-board", "title": data.get("title") or "", "shots": shots, "cast": cast, "locations": places, "warnings": warnings}


def seconds_of(text) -> float | None:
    found = re.match(r"\s*(\d+(?:\.\d+)?)\s*s?\s*$", str(text or ""))
    return float(found.group(1)) if found else None


def master_canvas(data: dict) -> dict:
    """A Master Canvas project (master-canvas-project.json): its shot and media cards in canvas order (rows of 80,
    then left to right), each under the "Scene N" its title or tags name, else the scene card above it. The project
    file carries no pictures: those come with the handoff package."""

    nodes = [one for one in data.get("nodes") or [] if isinstance(one, dict)]
    place = lambda one: (round(float(one.get("y") or 0) / 80), float(one.get("x") or 0))
    headers = sorted([one for one in nodes if one.get("type") in ("scene", "section")], key=place)
    shots, places = [], {}
    for one in sorted([one for one in nodes if one.get("type") in ("shot", "media")], key=place):
        named = re.search(r"\bScene\s*(\d+)", f"{one.get('title') or ''} {one.get('tags') or ''}", re.I)
        above = [head for head in headers if place(head) <= place(one)]
        scene = f"Scene {named.group(1)}" if named else (above[-1].get("title") if above else "")
        camera = ", ".join(bit for bit in (one.get("shotSize"), one.get("cameraAngle"), one.get("cameraMovement"), one.get("lensFeel")) if bit)
        if one.get("location"):
            places.setdefault(one["location"], {"name": one["location"], "description": ""})
        shots.append(shot(len(shots) + 1, title=one.get("title") or "", scene=scene, camera=camera,
                          action=one.get("prompt") or one.get("subjectAction") or "", seconds=seconds_of(one.get("duration")), notes=one.get("notes") or ""))
    cast = [{"name": one.get("title") or "Unnamed", "description": one.get("notes") or one.get("prompt") or "", "image": None}
            for one in nodes if one.get("type") == "character"]
    warnings = ["A Master Canvas project file holds no pictures: import its handoff package to bring them."] if shots else []
    return {"app": "master-canvas", "title": data.get("title") or "", "shots": shots, "cast": cast, "locations": list(places.values()), "warnings": warnings}


def master_canvas_handoff(manifest: dict, base: Path, files: dict, home: Path) -> dict:
    """A Master Canvas handoff package: project_manifest.json's shots in their global order, each with the picture
    at its sourcePath inside the ZIP."""

    shots, places, warnings = [], {}, []
    titles = {one.get("sceneKey"): one.get("title") or one.get("sceneKey") for one in manifest.get("scenes") or [] if isinstance(one, dict)}
    listed = [one for one in manifest.get("shots") or [] if isinstance(one, dict)]
    for one in sorted(listed, key=lambda item: (item.get("globalOrder") if isinstance(item.get("globalOrder"), (int, float)) else 1e9)):
        picture = files.get(base_join(base, one.get("sourcePath")))
        camera = ", ".join(bit for bit in (one.get("shotSize"), one.get("cameraAngle"), one.get("cameraMovement"), one.get("lensFeel")) if bit)
        if one.get("location"):
            places.setdefault(one["location"], {"name": one["location"], "description": ""})
        if one.get("sourcePath") and picture is None:
            warnings.append(f"{one.get('orderLabel') or one.get('title')}: its picture {one['sourcePath']} is not in the package.")
        shots.append(shot(len(shots) + 1, title=one.get("title") or one.get("orderLabel") or "",
                          scene=titles.get(one.get("sceneKey")) or one.get("sceneKey") or "", camera=camera,
                          action=one.get("prompt") or one.get("subjectAction") or "", seconds=seconds_of(one.get("duration")),
                          notes=one.get("notes") or "", image=relative_to(picture, home) if picture and picture.suffix.lower() in STILLS else None,
                          reference_video=relative_to(picture, home) if picture and picture.suffix.lower() in CLIPS else None))
    text = (manifest.get("continuity") or {}).get("characters") if isinstance(manifest.get("continuity"), dict) else ""
    cast = [{"name": bit.split(":")[0].strip(), "description": bit.partition(":")[2].strip(), "image": None}
            for bit in re.split(r"[\n;]+", text or "") if bit.strip()]
    return {"app": "master-canvas", "title": manifest.get("title") or "", "shots": shots, "cast": cast, "locations": list(places.values()), "warnings": warnings}


def storyboard(prompts: dict, base: Path, files: dict, home: Path) -> dict:
    """A Storyboard Reference board export: prompts.json, and NN_label/still.png beside it for each frame."""

    shots, warnings = [], []
    for one in sorted([one for one in prompts.get("frames") or [] if isinstance(one, dict)], key=lambda item: item.get("index") or 0):
        still = files.get(base_join(base, f"{one.get('folder')}/still.png"))
        if still is None:
            warnings.append(f"Frame {one.get('index')}: {one.get('folder')}/still.png is not in the ZIP.")
        shots.append(shot(len(shots) + 1, title=one.get("label") or one.get("folder") or "", scene=prompts.get("project") or "",
                          action=one.get("prompt") or "", notes=one.get("notes") or "", image=relative_to(still, home)))
    return {"app": "storyboard-reference", "title": prompts.get("project") or "", "shots": shots, "cast": [], "locations": [], "warnings": warnings}


def blockout(exports: list[tuple[Path, dict]], files: dict, home: Path) -> dict:
    """Blockout shot exports: each folder's metadata.json, prompt.txt, <shot>_reference.mp4, <shot>_depth.mp4 and
    stills/<shot>_first.png. The cast is the subjects named in the blocking."""

    shots, cast, motion = [], {}, None
    for base, meta in exports:
        info = meta.get("shot") or {}
        name = str(info.get("name") or "")
        inside = {key[len(str(base)) + 1:] if str(base) != "." else key: path for key, path in files.items()
                  if str(base) == "." or key.startswith(str(base) + "/")}
        find = lambda *names: next((inside[n] for n in names if n in inside), None)
        reference = find(f"{name}_reference.mp4", "reference.mp4")
        depth = find(f"{name}_depth.mp4", "depth.mp4")
        first = find(f"stills/{name}_first.png", f"{name}_first.png")
        prompt = find("prompt.txt")
        marks = meta.get("cameraMarks") or []
        lens = sorted({m.get("focalLength") for m in marks if m.get("focalLength")})
        camera = ", ".join(bit for bit in (info.get("rig") and f"{info['rig']} rig", lens and "-".join(f"{x:g}" for x in lens) + "mm lens",
                                           info.get("aspect"), info.get("sensor")) if bit)
        scene = base.parts[-3] if len(base.parts) >= 3 else ""
        shots.append(shot(len(shots) + 1, title=name and f"Shot {name}", scene=scene.replace("-", " "), camera=camera,
                          action=read_text(prompt).strip() if prompt else "", seconds=info.get("duration"),
                          image=relative_to(first, home), reference_video=relative_to(reference, home),
                          notes="; ".join(str(w.get("issue") or "") for w in meta.get("warnings") or [] if isinstance(w, dict))))
        for subject in meta.get("subjects") or []:
            if subject.get("name"):
                cast.setdefault(subject["name"].title(), {"name": subject["name"].title(), "description": subject.get("asset") or "", "image": None})
        if motion is None and reference:
            motion = {"reference": reference, "depth": depth, "meta": meta, "subjects": bool(meta.get("subjects"))}
    return {"app": "blockout", "title": exports[0][1].get("shot", {}).get("name") and f"Blockout {exports[0][1]['shot']['name']}" or "Blockout",
            "shots": shots, "cast": list(cast.values()), "locations": [], "warnings": [], "_motion": motion}


def blockout_pack(motion: dict, target: Path, source: str) -> None:
    """A Blockout shot as a Motion Previs production pack (bundle_manifest.json in Motion Previs Studio's layout,
    reference.mp4 and depth.mp4 by their pack names), for the Motion Previs tab's own import. Grey placeholder
    figures carry the blocking and the camera; with no subjects only the camera move is kept."""

    info = motion["meta"].get("shot") or {}
    seconds = float(info.get("duration") or 0)
    mode = "actor-motion" if motion["subjects"] else "camera-only"
    files = {"reference": "reference.mp4", "depth": "depth.mp4" if motion.get("depth") else None}
    manifest = {"app": "Blockout", "version": str((motion["meta"].get("generator") or {}).get("schema") or 1),
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sourceName": source,
                "range": {"start": 0, "end": seconds, "duration": seconds}, "sampleFps": info.get("fps") or FPS,
                "planning": {"shotTitle": info.get("name") or "", "subjectMode": mode, "projectTitle": "Blockout"}, "files": files}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as bundle:
        bundle.writestr("bundle_manifest.json", json.dumps(manifest, indent=2))
        bundle.write(motion["reference"], "reference.mp4")
        if motion.get("depth"):
            bundle.write(motion["depth"], "depth.mp4")


def slate_project(data: dict) -> dict:
    """A Slate project.json: its scenes and shots as they stand (the film itself goes to Slate unchanged)."""

    shots = []
    for scene in data.get("scenes") or []:
        for one in scene.get("shots") or []:
            spec = one.get("spec") or {}
            camera = ", ".join(bit for bit in (spec.get("size"), spec.get("angle"), spec.get("movement"), spec.get("lens")) if bit)
            shots.append(shot(len(shots) + 1, title=one.get("name") or "", scene=scene.get("name") or "", action=one.get("prompt") or "",
                              camera=camera, seconds=spec.get("durationSec"), notes=one.get("intent") or ""))
    cast = [{"name": one.get("name") or "Unnamed", "description": one.get("notes") or "", "image": None} for one in data.get("characters") or []]
    places = [{"name": one.get("name") or "Unnamed", "description": one.get("description") or ""} for one in data.get("locations") or []]
    return {"app": "slate", "title": data.get("name") or "", "logline": data.get("logline") or "", "shots": shots, "cast": cast,
            "locations": places, "warnings": []}


def motion_pack(manifest: dict, files: dict, home: Path) -> dict:
    plan = manifest.get("planning") or {}
    reference = next((path for key, path in files.items() if key.split("/")[-1] == "reference.mp4"), None)
    one = shot(1, title=plan.get("shotTitle") or manifest.get("sourceName") or "Shot", scene=plan.get("sceneTitle") or "",
               action=plan.get("creativeIntent") or "", seconds=(manifest.get("range") or {}).get("duration"),
               reference_video=relative_to(reference, home), notes=plan.get("visualStyle") or "")
    return {"app": "motion-previs-studio", "title": plan.get("projectTitle") or manifest.get("sourceName") or "", "shots": [one],
            "cast": [], "locations": [], "warnings": []}


def base_join(base: Path, name) -> str:
    joined = safe_name(str(Path(base) / str(name or ""))) if name else None
    return joined or ""


def relative_to(path: Path | None, home: Path) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve().relative_to(home.resolve()))
    except ValueError:
        return None


def kind_of_json(data) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("app") == "scriptbreak" and isinstance(data.get("state"), dict):
        return "scriptbreak"
    if isinstance(data.get("boards"), list) and isinstance(data.get("cards"), dict):
        return "cork-board"
    if data.get("schema") == "master-canvas-handoff-v1":
        return "master-canvas-handoff"
    if isinstance(data.get("nodes"), list) and ("continuity" in data or "assets" in data):
        return "master-canvas"
    if isinstance(data.get("scenes"), list) and isinstance(data.get("defaults"), dict):
        return "slate"
    if isinstance(data.get("frames"), list) and "project" in data and all(isinstance(f, dict) and "folder" in f for f in data["frames"]):
        return "storyboard-reference"
    if (data.get("generator") or {}).get("app") == "Blockout" if isinstance(data.get("generator"), dict) else False:
        return "blockout"
    if isinstance(data.get("files"), dict) and "sampleFps" in data:
        return "motion-previs-studio"
    return None


def read_upload(upload: Path, name: str, hint: str, folder: Path, home: Path) -> dict:
    """What the uploaded file is, read into the import's shape. ``hint`` (the form's "app") only breaks a tie."""

    suffix = upload.suffix.lower()
    if suffix == ".fountain" or (suffix == ".txt" and hint in ("fountain", "")):
        return fountain(read_text(upload))
    if suffix == ".md":
        return scriptbreak_pack(read_text(upload))
    if suffix in (".json", ".scriptbreak"):
        data = read_json(upload)
        kind = "scriptbreak" if suffix == ".scriptbreak" else kind_of_json(data)
        if kind == "scriptbreak":
            return scriptbreak(data)
        if kind == "cork-board":
            return cork_board(data)
        if kind == "master-canvas":
            return master_canvas(data)
        if kind == "slate":
            shutil.copyfile(upload, folder / "project.json")
            return slate_project(data)
        if kind == "master-canvas-handoff":
            return master_canvas_handoff(data, Path("."), {}, home)
        if kind == "storyboard-reference":
            found = storyboard(data, Path("."), {}, home)
            found["warnings"].insert(0, "prompts.json alone has no stills: zip the whole board folder to bring them.")
            return found
        if kind == "blockout":
            return blockout([(Path("."), data)], {}, home)
        raise HTTPException(400, "this JSON file is not a project of ScriptBreak, Cork Board, Master Canvas, Slate, Storyboard Reference or Blockout")
    files = unpack(upload, folder / "files")
    by_name = {key: path for key, path in files.items()}
    jsons = {key: path for key, path in files.items() if key.endswith(".json") and path.stat().st_size < 20 * 1024 ** 2}
    kinds = {}
    for key, path in jsons.items():
        try:
            kinds[key] = kind_of_json(json.loads(read_text(path)))
        except (ValueError, HTTPException):
            continue
    pick = lambda wanted: sorted((key for key, kind in kinds.items() if kind == wanted), key=lambda key: (key.count("/"), key))
    order = ["master-canvas-handoff", "storyboard-reference", "blockout", "motion-previs-studio", "scriptbreak", "slate", "cork-board", "master-canvas"]
    if hint:
        order.sort(key=lambda kind: not kind.startswith(hint.split("-")[0]))
    for kind in order:
        found = pick(kind)
        if not found:
            continue
        data = json.loads(read_text(by_name[found[0]]))
        base = Path(found[0]).parent
        if kind == "master-canvas-handoff":
            return master_canvas_handoff(data, base, by_name, home)
        if kind == "storyboard-reference":
            return storyboard(data, base, by_name, home)
        if kind == "blockout":
            out = blockout([(Path(key).parent, json.loads(read_text(by_name[key]))) for key in found], by_name, home)
            motion = out.pop("_motion", None)
            if motion:
                blockout_pack(motion, folder / "motion-pack.zip", name)
                out["motion"] = True
            return out
        if kind == "motion-previs-studio":
            shutil.copyfile(upload, folder / "motion-pack.zip")
            return motion_pack(data, by_name, home)
        if kind == "scriptbreak":
            return scriptbreak(data)
        if kind == "slate":
            shutil.copyfile(by_name[found[0]], folder / "project.json")
            return slate_project(data)
        if kind == "cork-board":
            return cork_board(data)
        return master_canvas(data)
    named = [key for key in files if key.endswith(".scriptbreak")]
    if named:
        return scriptbreak(json.loads(read_text(files[named[0]])))
    named = [key for key in files if key.endswith(".fountain")]
    if named:
        return fountain(read_text(files[named[0]]))
    raise HTTPException(400, "nothing in this ZIP is a file of the suite's apps: no prompts.json, metadata.json, project_manifest.json, "
                             "bundle_manifest.json, project.json or .scriptbreak")


# ---------- a Production timeline for an editor ----------

def probe(path: Path) -> dict:
    """A clip's rate, size, length and whether it has sound, from ffprobe (with a limit); {} when it cannot be read."""

    tool = shutil.which("ffprobe") or next((p for p in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe") if Path(p).is_file()), None)
    if not tool:
        return {}
    try:
        done = subprocess.run([tool, "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
                               "-of", "json", str(path)], capture_output=True, text=True, timeout=10)
        data = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    streams = data.get("streams") or []
    video = next((one for one in streams if one.get("codec_type") == "video"), {})
    rate = None
    try:
        num, den = (int(x) for x in str(video.get("r_frame_rate") or "").split("/"))
        rate = (num, den) if num > 0 and den > 0 else None
    except ValueError:
        pass
    try:
        seconds = float((data.get("format") or {}).get("duration") or 0) or None
    except (TypeError, ValueError):
        seconds = None
    return {"rate": rate, "width": video.get("width"), "height": video.get("height"), "seconds": seconds,
            "audio": any(one.get("codec_type") == "audio" for one in streams), "video": bool(video)}


def clip_spans(clips: list[dict], rate: tuple[int, int]) -> list[dict]:
    """Each timeline clip as whole frames: where it starts in its file and how long it runs (out 0: the whole clip)."""

    num, den = rate
    out, at = [], 0
    for clip in clips:
        start, end = max(0.0, float(clip.get("in") or 0)), float(clip.get("out") or 0)
        length = (end - start) if end > start else float(clip.get("seconds") or clip.get("duration") or 5)
        first = round(start * num / den)
        frames = max(1, round(length * num / den))
        out.append({**clip, "first": first, "frames": frames, "offset": at})
        at += frames
    return out


def fcpxml(name: str, clips: list[dict], rate: tuple[int, int] = (FPS, 1), size: tuple[int, int] = (1920, 1080),
           song: dict | None = None, where=None) -> str:
    """FCPXML 1.11, one sequence: every clip in order with its in point and length, its picture and sound, and a song
    (``song``: path, seconds) under the whole of it. DaVinci Resolve and Final Cut Pro read it; media by file:// URL,
    this machine's own unless ``where`` gives each file's URL on the computer the editor works on."""

    where = where or (lambda path: Path(path).resolve().as_uri())

    num, den = rate
    t = lambda frames: f"{frames * den}/{num}s" if frames else "0s"
    spans = clip_spans(clips, rate)
    total = sum(one["frames"] for one in spans)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE fcpxml>", '<fcpxml version="1.11">', "  <resources>",
           f'    <format id="r1" frameDuration="{den}/{num}s" width="{size[0]}" height="{size[1]}" colorSpace="1-1-1 (Rec. 709)"/>']
    assets, refs = {}, []
    for one in spans:
        key = str(one["path"])
        if key not in assets:
            assets[key] = f"r{len(assets) + 2}"
            length = max(round(float(one.get("file_seconds") or 0) * num / den), one["first"] + one["frames"])
            audio = ' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if one.get("audio", True) else ""
            out.append(f'    <asset id="{assets[key]}" name={quoteattr(Path(key).stem)} start="0s" duration="{t(length)}" hasVideo="1" '
                       f'format="r1"{audio}>')
            out.append(f'      <media-rep kind="original-media" src={quoteattr(where(key))}/>')
            out.append("    </asset>")
        refs.append(assets[key])
    if song:
        song_frames = max(1, round(float(song.get("seconds") or total * den / num) * num / den))
        out.append(f'    <asset id="rs" name={quoteattr(Path(song["path"]).stem)} start="0s" duration="{t(song_frames)}" hasAudio="1" '
                   'audioSources="1" audioChannels="2" audioRate="48000">')
        out.append(f'      <media-rep kind="original-media" src={quoteattr(where(song["path"]))}/>')
        out.append("    </asset>")
    out += ["  </resources>", "  <library>", f"    <event name={quoteattr(name)}>", f"      <project name={quoteattr(name)}>",
            f'        <sequence format="r1" duration="{t(total)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">',
            "          <spine>"]
    for index, (one, ref) in enumerate(zip(spans, refs)):
        tag = f'            <asset-clip ref="{ref}" name={quoteattr(Path(str(one["path"])).stem)} offset="{t(one["offset"])}" ' \
              f'start="{t(one["first"])}" duration="{t(one["frames"])}" format="r1" tcFormat="NDF"' + (' audioRole="dialogue"' if one.get("audio", True) else "")
        if song and index == 0:
            song_frames = min(max(1, round(float(song.get("seconds") or 0) * num / den)) if song.get("seconds") else total, total)
            out.append(tag + ">")
            out.append(f'              <asset-clip ref="rs" lane="-1" name={quoteattr(Path(song["path"]).stem)} offset="{t(one["first"])}" '
                       f'start="0s" duration="{t(song_frames)}" audioRole="music"/>')
            out.append("            </asset-clip>")
        else:
            out.append(tag + "/>")
    out += ["          </spine>", "        </sequence>", "      </project>", "    </event>", "  </library>", "</fcpxml>", ""]
    return "\n".join(out)


def timecode(frames: int, fps: int) -> str:
    return f"{frames // (3600 * fps):02d}:{frames // (60 * fps) % 60:02d}:{frames // fps % 60:02d}:{frames % fps:02d}"


def edl(name: str, clips: list[dict], rate: tuple[int, int] = (FPS, 1)) -> str:
    """CMX 3600: one event a clip, picture and both sound channels (AA/V), record time from 01:00:00:00, the file's
    name and path in the comments an editor relinks by."""

    fps = max(1, round(rate[0] / rate[1]))
    spans = clip_spans(clips, rate)
    title = re.sub(r"[^\x20-\x7e]", "", name)[:70] or "Studio timeline"
    out = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    start = 3600 * fps
    for number, one in enumerate(spans, start=1):
        path = Path(str(one["path"]))
        reel = re.sub(r"[^A-Za-z0-9]", "", path.stem)[:8].upper() or "AX"
        channel = "AA/V" if one.get("audio", True) else "V"
        source_in, record_in = one["first"], start + one["offset"]
        out.append(f"{number:03d}  {reel:<8} {channel:<5} C        {timecode(source_in, fps)} {timecode(source_in + one['frames'], fps)} "
                   f"{timecode(record_in, fps)} {timecode(record_in + one['frames'], fps)}")
        out.append(f"* FROM CLIP NAME: {path.name}")
        out.append(f"* SOURCE FILE: {path.resolve()}")
        out.append("")
    return "\r\n".join(out) + "\r\n"


# ---------- an import as a Slate film ----------

def slate_id(prefix: str) -> str:
    """An id in Slate's own form (slId in slate.js): prefix, the time in base 36, five random characters."""

    stamp, digits = int(time.time() * 1000), "0123456789abcdefghijklmnopqrstuvwxyz"
    text = ""
    while stamp:
        stamp, rest = divmod(stamp, 36)
        text = digits[rest] + text
    return f"{prefix}-{text}-{uuid.uuid4().hex[:5]}"


def as_slate(found: dict, home: Path, blank) -> dict:
    """A Slate project.json (app/slate.py's shape, as slate.js writes it) from an import: its cast as the bible's
    characters, its places as locations, its scenes and shots in order with a sectioned prompt each. A shot's notes
    go to its intent, which never reaches the prompt."""

    project = blank(found.get("title") or "Imported film")
    project["logline"] = found.get("logline") or ""
    stamp = project["createdAt"]
    defaults = project["defaults"]
    characters = []
    for one in found.get("cast") or []:
        image = one.get("image") if one.get("image") and (home / one["image"]).is_file() else None
        characters.append({"id": slate_id("char"), "name": one.get("name") or "Unnamed", "age": "", "gender": "", "ethnicity": "",
                           "faceFeatures": "", "hair": "", "clothing": "", "expression": "", "eyeDirection": "", "mood": "",
                           "environment": "", "keyLightSide": "Key light from left", "lightingMood": "Natural soft light",
                           "scenario": "cinematic", "notes": one.get("description") or "", "images": [image] if image else []})
    locations = [{"id": slate_id("loc"), "name": one.get("name") or "Unnamed",
                  "interiorExterior": "interior" if re.match(r"\s*INT\b", one.get("description") or "", re.I) else "exterior",
                  "description": one.get("description") or "", "timeOfDay": "", "weather": "", "architecture": "", "textures": "",
                  "practicalLights": "", "notes": "", "images": []} for one in found.get("locations") or []]
    by_name = {one["name"].lower(): one for one in characters}
    scenes: list[dict] = []
    for one in found.get("shots") or []:
        heading = one.get("scene") or "Scene 1"
        if not scenes or scenes[-1]["name"] != heading:
            scenes.append({"id": slate_id("scene"), "name": heading, "synopsis": "", "shots": []})
        words = f"{one.get('action') or ''} {one.get('title') or ''}".lower()
        cast = [sheet["id"] for name, sheet in by_name.items()
                if name and (re.search(r"\b" + re.escape(name) + r"\b", words) or any((line.get("character") or "").lower() == name for line in one.get("dialogue") or []))]
        place = next((loc["id"] for loc in locations if loc["name"].lower() == heading.lower() or loc["name"].lower() in heading.lower()), "")
        sections = [("Subject", one.get("action") or one.get("title")), ("Camera", one.get("camera"))]
        prompt = "\n\n".join(f"# {name}\n{text.strip()}" for name, text in sections if text and text.strip())
        image = one.get("image") if one.get("image") and (home / one["image"]).is_file() else None
        lines = [{"characterId": (by_name.get((line.get("character") or "").lower()) or {}).get("id"), "voiceId": None,
                  "text": line["line"], "language": "English", "sung": False} for line in one.get("dialogue") or []]
        scenes[-1]["shots"].append({
            "id": slate_id("shot"), "name": one.get("title") or f"Shot {len(scenes[-1]['shots']) + 1}", "intent": one.get("notes") or "",
            "spec": {"durationSec": one.get("seconds") or defaults["durationSec"], "fps": defaults["fps"], "aspectRatio": defaults["aspectRatio"],
                     "lens": None, "movement": None, "size": None, "angle": None},
            "prompt": prompt, "lockedLines": [], "mutedLines": [], "beatSheet": None, "targetModel": "minimax-h3", "maxChars": None,
            "variants": [], "history": [], "takes": [], "createdAt": stamp, "updatedAt": stamp,
            "tf": {"start": "image" if image else "text", "cast": cast, "location": place, "lines": lines, "soundscape": "", "music": "N/A",
                   "cue": "", "seed": 1, "image": image, "h3": "", "h3Auto": True}})
    project.update(characters=characters, locations=locations, scenes=scenes)
    project["importedFrom"] = {"app": found.get("app"), "import": found.get("id")}
    return project



# ---------- dailies for Circle Take, an entry for Call Sheet ----------

GENERATOR = "Imagination Studio"
SHOT_CODE = re.compile(r"^(?:shot[-_ ]?|sh(?=\d)|s(?=\d))?(\d{1,4}[a-zA-Z]?)(?:[-_ .:]|$)", re.I)  # Circle Take's own (types.ts)


def model_of(job: dict) -> str:
    params = job.get("params") or {}
    if job.get("kind") in ("scout", "edit"):
        return str(params.get("image_model") or "qwen")
    if params.get("references") or params.get("reference_videos"):
        return "minimax-h3-ref2va"
    return "fasth3" if str(params.get("quality") or "").startswith("fh") else "minimax-h3"


def provenance(job: dict, shot_name: str | None, size: tuple | None) -> dict:
    """Circle Take's sidecar: it reads prompt, seed, model and generator at the top level (numbers as text); the
    rest is kept for people and for a later version of it."""

    params = job.get("params") or {}
    return {"prompt": params.get("prompt") or params.get("image_prompt") or "", "seed": params.get("seed"), "model": model_of(job),
            "generator": GENERATOR, "engine": job.get("engine"), "passes": job.get("passes") or params.get("steps"),
            "quality": params.get("quality"), "preset": params.get("preset"),
            "resolution": f"{size[0]}x{size[1]}" if size else None, "frames": params.get("frames"), "seconds": params.get("seconds"),
            "loras": params.get("loras") or [], "job": job.get("id"), "kind": job.get("kind"), "title": job.get("title"),
            "shot": shot_name, "spark": job.get("spark"), "qa": (job.get("qa") or {}).get("passed") if isinstance(job.get("qa"), dict) else None,
            "created": job.get("created")}


def dailies_package(takes: list, presets: dict, measure, home: Path | None = None):
    """A ZIP for Circle Take's ingest: each clip named <shot>_take<NN> when its title opens with a shot number (12A,
    shot-12A), so Circle Take binds it to that shot, else take_<job> for its unassigned tray; <same name>.json beside
    it is the provenance sidecar Circle Take reads."""

    folder = (home or Path(takes[0][1][0]).parents[1]) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"dailies-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.zip"
    counts, listed = {}, []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as bundle:
        for job, media in takes:
            clips = [path for path in media if path.suffix.lower() in CLIPS]
            if not clips:
                continue
            code = SHOT_CODE.match(str(job.get("title") or "").strip())
            shot_name = code.group(1).upper() if code else None
            for clip in clips[:1]:
                if shot_name:
                    counts[shot_name] = counts.get(shot_name, 0) + 1
                    base = f"{shot_name}_take{counts[shot_name]:02d}"
                else:
                    base = f"take_{re.sub(r'[^A-Za-z0-9-]', '-', job['id'])}"
                seen = measure(clip)
                preset = (job.get("params") or {}).get("preset")
                size = (seen["width"], seen["height"]) if seen.get("width") else (tuple(presets[preset][1]) if preset in presets else None)
                bundle.write(clip, f"dailies/{base}{clip.suffix.lower()}")
                bundle.writestr(f"dailies/{base}.json", json.dumps(provenance(job, shot_name, size), indent=1))
                listed.append({"file": f"{base}{clip.suffix.lower()}", "job": job["id"], "shot": shot_name})
        if not listed:
            target.unlink(missing_ok=True)
            raise HTTPException(400, "none of these jobs made a clip: Circle Take takes videos")
        bundle.writestr("dailies/README.txt", "Circle Take dailies from " + GENERATOR + ".\n\nIngest this folder in Circle Take. A clip named "
                        "<shot>_takeNN goes to that shot; take_<job> goes to the unassigned tray. Each clip's .json beside it is its "
                        "prompt, seed and model.\n")
    return FileResponse(target, media_type="application/zip", filename=target.name)


def callsheet_entry() -> dict:
    """One element of Call Sheet's data/apps.json (its SuiteApp type): the Studio is not a release of
    wassermanproductions, so it has no repo for Call Sheet's update check, and no recents reader."""

    return {"id": "backlot-studio", "name": "Imagination Studio", "bundle": "Imagination Studio", "repo": None,
            "stage": "Generate", "tagline": "Pictures, clips, voices and songs rendered on this machine"}


# ---------- the routes ----------

def find_route(routes, path: str):
    """A route's function by its path, in the app's routes and the routers included in it (FastAPI keeps an included
    router whole, as one entry, in newer versions)."""

    for route in routes:
        if getattr(route, "path", None) == path and getattr(route, "endpoint", None):
            return route.endpoint
        inner = getattr(route, "original_router", None) or getattr(route, "router", None)
        if inner is not None and inner is not route and getattr(inner, "routes", None) is not None:
            found = find_route(inner.routes, path)
            if found:
                return found
    return None


def attach(home: Path, jobs: dict, lock: threading.Lock, presets: dict, app=None) -> APIRouter:
    """``jobs`` and ``lock`` are the queue's (a dailies package reads finished jobs), ``presets`` the Studio's sizes and
    ``app`` the web app, whose Motion Previs import route takes a Blockout export (called as a function, not over HTTP)."""

    router = APIRouter()
    imports = home / "uploads" / "suite"

    def import_folder(import_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", import_id):
            raise HTTPException(400, "bad import id")
        folder = imports / import_id
        if not (folder / "import.json").is_file():
            raise HTTPException(404, "no such import")
        return folder

    @router.get("/api/suite")
    def apps() -> list[dict]:
        return APPS

    @router.post("/api/suite/import")
    async def take_in(request: Request, file: UploadFile = File(...), app_id: str = Form("", alias="app")) -> dict:
        local(request)
        name = Path(file.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in (".zip", ".json", ".md", ".fountain", ".scriptbreak", ".txt"):
            raise HTTPException(400, "the suite's files are .zip, .json, .scriptbreak, .md, .fountain or .txt")
        import_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        folder = imports / import_id
        folder.mkdir(parents=True)
        upload = folder / ("upload" + suffix)
        try:
            written = 0
            with open(upload, "wb") as sink:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > UPLOAD_LIMIT:
                        raise HTTPException(400, f"one file is at most {UPLOAD_LIMIT // 1024 ** 3} GB")
                    sink.write(chunk)
            found = read_upload(upload, name, (app_id or "").strip().lower(), folder, home)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        found = {"id": import_id, "app": found["app"], "title": found.get("title") or Path(name).stem, "file": name,
                 "created": time.time(), "shots": found.get("shots") or [], "cast": found.get("cast") or [],
                 "locations": found.get("locations") or [], "warnings": found.get("warnings") or [],
                 **{key: found[key] for key in ("logline", "motion") if found.get(key)}}
        (folder / "import.json").write_text(json.dumps(found, indent=1))
        return found

    @router.get("/api/suite/imports")
    def listed() -> list[dict]:
        out = []
        for file in sorted(imports.glob("*/import.json"), key=lambda f: -f.stat().st_mtime) if imports.is_dir() else []:
            try:
                data = json.loads(file.read_text())
            except ValueError:
                continue
            out.append({"id": data.get("id"), "app": data.get("app"), "title": data.get("title"), "file": data.get("file"),
                        "created": data.get("created"), "shots": len(data.get("shots") or []), "cast": len(data.get("cast") or []),
                        "locations": len(data.get("locations") or []), "slate": data.get("slate"), "motion_pack": data.get("motion_pack")})
        return out

    @router.get("/api/suite/imports/{import_id}")
    def one_import(import_id: str) -> dict:
        return json.loads((import_folder(import_id) / "import.json").read_text())

    @router.post("/api/suite/imports/{import_id}/slate")
    def to_slate(import_id: str, request: Request) -> dict:
        local(request, json_body=True)
        import slate  # the Studio's Slate (app/slate.py): its project shape and its folder
        folder = import_folder(import_id)
        found = json.loads((folder / "import.json").read_text())
        if found.get("app") == "slate" and (folder / "project.json").is_file():
            given = json.loads((folder / "project.json").read_text())  # a Slate film comes in as it is
            project = {**slate.blank(given.get("name") or "Imported film"), **given}
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(project.get("id", ""))) or slate.project_file(project["id"]).is_file():
                project["id"] = slate.new_id()
        else:
            project = as_slate(found, home, slate.blank)
        slate.write(project)
        found["slate"] = project["id"]
        (folder / "import.json").write_text(json.dumps(found, indent=1))
        return {"project": project["id"], "name": project["name"], "scenes": len(project["scenes"]),
                "shots": sum(len(scene.get("shots") or []) for scene in project["scenes"])}

    @router.post("/api/suite/imports/{import_id}/motion")
    async def to_motion(import_id: str, request: Request) -> dict:
        local(request, json_body=True)
        folder = import_folder(import_id)
        found = json.loads((folder / "import.json").read_text())
        pack = folder / "motion-pack.zip"
        if found.get("app") not in ("blockout", "motion-previs-studio") or not pack.is_file():
            raise HTTPException(400, "only a Blockout export with a reference clip, or a Motion Previs pack, goes to Motion Previs")
        endpoint = find_route(getattr(app, "routes", []), "/api/motion/packs/import")
        if endpoint is None:
            raise HTTPException(503, "Motion Previs is not part of this app")
        with open(pack, "rb") as handle:
            described = await endpoint(file=UploadFile(handle, filename=f"{found.get('title') or 'blockout'}.zip"))
        found["motion_pack"] = described["id"]
        (folder / "import.json").write_text(json.dumps(found, indent=1))
        return {"pack": described["id"], "layers": described.get("layers"), "mode": described.get("mode")}

    @router.get("/api/export/timeline/{name}")
    def timeline(name: str, root: str = ""):
        project_id, _, kind = name.rpartition(".")
        if kind not in ("fcpxml", "edl", "zip") or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
            raise HTTPException(404, "ask for <project>.fcpxml, <project>.edl or <project>.zip")
        if kind == "zip" and root and not re.fullmatch(r"(/|[A-Za-z]:[\\/])[^\x00-\x1f]{0,400}", root):
            raise HTTPException(400, "the folder is a full path on the editing computer, like /Users/you/Movies")
        file = home / "projects" / project_id / "project.json"
        if not file.is_file():
            raise HTTPException(404, "no such project")
        project = json.loads(file.read_text())
        clips, rate, size = [], None, None
        for clip in project.get("timeline") or []:
            source = (home / str(clip.get("path") or "")).resolve()
            if home.resolve() not in source.parents or not source.is_file():
                raise HTTPException(400, f"a timeline clip is not in the studio folder: {clip.get('path')}")
            seen = probe(source)
            rate = rate or seen.get("rate")
            size = size or ((seen["width"], seen["height"]) if seen.get("width") else None)
            clips.append({"path": source, "in": clip.get("in"), "out": clip.get("out"), "duration": clip.get("duration"),
                          "seconds": seen.get("seconds") if not clip.get("duration") else None,
                          "file_seconds": seen.get("seconds"), "audio": seen["audio"] if seen.get("video") else True})
        if not clips:
            raise HTTPException(400, "the timeline is empty")
        title = project.get("name") or project_id
        if kind == "edl":
            return Response(edl(title, clips, rate or (FPS, 1)), media_type="text/plain",
                            headers={"Content-Disposition": f'attachment; filename="{project_id}.edl"'})
        song = None
        said = project.get("soundtrack")
        take = said.get("take") if isinstance(said, dict) else said
        if take and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(take)):
            try:
                meta = json.loads((home / "music" / f"{take}.take.json").read_text())
                path = (home / meta["path"]).resolve()
                if home.resolve() in path.parents and path.is_file():
                    song = {"path": path, "seconds": probe(path).get("seconds") or meta.get("seconds")}
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if kind == "zip":
            return timeline_package(project_id, title, clips, rate or (FPS, 1), size or (1920, 1080), song, root)
        return Response(fcpxml(title, clips, rate or (FPS, 1), size or (1920, 1080), song), media_type="application/xml",
                        headers={"Content-Disposition": f'attachment; filename="{project_id}.fcpxml"'})

    def timeline_package(project_id, title, clips, rate, size, song, root):
        """The timeline with its media, for an editor on another computer: unzipped into ``root`` the FCPXML finds every
        clip where it lands. Without a folder the clips are named relative to the FCPXML, for relinking by hand."""
        names, used = {}, set()
        for path in [one["path"] for one in clips] + ([song["path"]] if song else []):
            key = str(Path(path))
            if key in names:
                continue
            stem, suffix, n = Path(path).stem, Path(path).suffix, 1
            name = stem + suffix
            while name in used:
                n += 1
                name = f"{stem}-{n}{suffix}"
            used.add(name)
            names[key] = name
        base = root.rstrip("/\\") + "/" + project_id if root else ""
        def where(path):
            name = names[str(Path(path))]
            if not base:
                return "media/" + name
            if re.match(r"[A-Za-z]:", base):  # a Windows folder
                return "file:///" + quote(base.replace("\\", "/") + "/media/" + name)
            return "file://" + quote(base + "/media/" + name)
        exports = home / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        target = exports / f"{project_id}-with-media-{time.strftime('%m%d-%H%M%S')}.zip"
        readme = (f"{title}\n\nUnzip this folder" + (f" into {root}" if root else "") + ", then in DaVinci Resolve choose File > Import > Timeline "
                  f"and pick {project_id}.fcpxml.\n" + ("" if root else "The clips are in media/: if Resolve shows them offline, relink them to that folder.\n"))
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as bundle:
            bundle.writestr(f"{project_id}/{project_id}.fcpxml", fcpxml(title, clips, rate, size, song, where))
            bundle.writestr(f"{project_id}/README.txt", readme)
            for key, name in names.items():
                bundle.write(key, f"{project_id}/media/{name}")
        return FileResponse(target, media_type="application/zip", filename=f"{project_id}-with-media.zip")

    @router.get("/api/export/dailies.zip")
    def dailies(jobs_: str = Query("", alias="jobs")):
        wanted = [one.strip() for one in jobs_.split(",") if one.strip()]
        if not wanted:
            raise HTTPException(400, "give the jobs: ?jobs=<id>,<id>")
        with lock:
            chosen = [dict(jobs[one]) for one in wanted if one in jobs]
        missing = [one for one in wanted if one not in {job["id"] for job in chosen}]
        if missing:
            raise HTTPException(404, "no such job: " + ", ".join(missing[:5]))
        takes = []
        for job in chosen:
            if job.get("status") != "done" or not job.get("outputs"):
                raise HTTPException(400, f"job {job['id']} has no finished clip")
            media = [(home / path).resolve() for path in job["outputs"]]
            media = [path for path in media if home.resolve() in path.parents and path.is_file()]
            if not media:
                raise HTTPException(400, f"job {job['id']}'s files are no longer in the studio folder")
            takes.append((job, media))
        return dailies_package(takes, presets, probe, home)

    @router.get("/api/export/callsheet-entry")
    def callsheet() -> dict:
        return callsheet_entry()

    return router
