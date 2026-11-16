"""Organize existing media (issue #257): takes made before the readable folders, moved into them when their owner
asks. Nothing here runs by itself.

New takes go to ``<root>/<YYYY-MM>/<date>_<time>_<title>_<token>/`` (take_names.py). This moves the older ones there:
voice lines and songs that lie flat in ``voice/`` and ``music/``, sound effects, Foley takes and pictures in a folder
named by their job's id, and clips that lie flat in ``renders/`` (with a long video's parts). It is careful rather
than complete:

- a take a film, a timeline, a Slate, a Suite project or a node graph names by its path is left where it is;
- so is a take of a job that is queued or running, and one whose new place is taken;
- what a take record says of its own files follows them, and every move is written down (``organize/moved.json``),
  so a path that was moved still opens (server.inside reads the list) and the last run can be undone.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

import take_names

JOB = re.compile(r"\d{4}-\d{6}-[A-Za-z0-9]{4,12}")  # a job's id: MMDD-HHMMSS-token
STORES = ("projects", "slate", "suite", "nodes", "graphics", "motion")  # where a path may be named by other work
STORE_LIMIT = 64 << 20


def named_elsewhere(home: Path) -> str:
    """Every JSON a film, a Slate, a Suite project or a graph keeps, as one text: a take's path in it is a reference."""
    found, size = [], 0
    for store in STORES:
        root = home / store
        if not root.is_dir():
            continue
        for file in root.rglob("*.json"):
            try:
                if file.is_file() and file.stat().st_size < (8 << 20) and size < STORE_LIMIT:
                    text = file.read_text(encoding="utf-8", errors="replace")
                    size += len(text)
                    found.append(text)
            except OSError:
                continue
    return "\n".join(found).replace("\\\\", "/").replace("\\/", "/")


def _record(file: Path) -> dict:
    try:
        said = json.loads(file.read_text(encoding="utf-8"))
        return said if isinstance(said, dict) else {}
    except (OSError, ValueError):
        return {}


def _words(text) -> str:
    return re.sub(r"<\|[^|>]*\|>|\[[^\]]*\]", " ", str(text or ""))


def _when(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def groups(home: Path, clips: dict | None = None) -> list[dict]:
    """Every older take as {"what", "id", "files": [(old, new), ...], "folders": [old folder, ...]} with paths
    relative to ``home``. ``clips`` is path -> {"title", "prompt"} for clips (the takes log)."""
    clips = clips or {}
    out = []

    def rel(path: Path) -> str:
        return path.relative_to(home).as_posix()

    def add(what: str, job: str, pairs: list[tuple[Path, Path]], folders: list[Path] = ()) -> None:
        if pairs:
            out.append({"what": what, "id": job, "files": [(rel(a), rel(b)) for a, b in pairs], "folders": [rel(f) for f in folders]})

    # voice lines and songs that lie flat: <root>/<id>.wav with <id>.take.json (and a song's <id>.yue2 folder)
    for root, what in (("voice", "voice line"), ("music", "song")):
        base = home / root
        for record in sorted(base.glob("*.take.json")) if base.is_dir() else []:
            stem = record.name[:-len(".take.json")]
            said = _record(record)
            job = str(said.get("id") or stem)
            title = _words(said.get("text")) if root == "voice" else said.get("title") or said.get("caption") or ""
            folder, name = take_names.folder(base, job, title, "voice" if root == "voice" else "song", now=_when(record))
            pairs = [(file, folder / (name + file.name[len(stem):])) for file in sorted(base.glob(stem + ".*")) if file.is_file()]
            score = base / f"{stem}.yue2"
            if score.is_dir() and not score.is_symlink():
                pairs += [(file, folder / f"{name}.yue2" / file.relative_to(score)) for file in sorted(score.rglob("*")) if file.is_file()]
            add(what, job, pairs, [score] if score.is_dir() else [])
    # sound effects, Foley takes and pictures in a folder named by the job's id
    for root, what, fallback in (("sound/sfx", "sound effect", "sfx"), ("sound/foley", "Foley take", "foley"), ("scouts", "picture set", "picture")):
        base = home / root
        for old in sorted(base.iterdir()) if base.is_dir() else []:
            if not old.is_dir() or old.is_symlink() or not JOB.fullmatch(old.name):
                continue
            files = sorted(file for file in old.rglob("*") if file.is_file())
            if not any(file.suffix.lower() in (".wav", ".mp4", ".png") for file in files):
                continue  # only logs: not a take
            said = next((_record(file) for file in files if file.name.endswith(".take.json")), {})
            title = said.get("prompt") or ("foley " + take_names.title_of(said.get("source")) if fallback == "foley" else "")
            folder, name = take_names.folder(base, old.name, str(title), fallback, now=_when(files[0]))
            pairs = []
            for file in files:
                inner = file.relative_to(old)
                head = file.name.split(".", 1)[0]
                if len(inner.parts) == 1 and head in take_names.MEDIA_NAMES:
                    inner = Path(name + file.name[len(head):])
                elif len(inner.parts) == 1 and file.name.startswith("scout_") and file.suffix.lower() == ".png":
                    inner = Path(name + file.name[len("scout"):])
                pairs.append((file, folder / inner))
            add(what, old.name, pairs, [old])
    # clips that lie flat in renders: <id>.mp4, <id>-hlg.mp4 ..., and a long video's parts in renders/<id>/
    base = home / "renders"
    seen = {}
    for file in sorted(base.glob("*.*")) if base.is_dir() else []:
        found = JOB.match(file.name)
        if file.is_file() and found and file.suffix.lower() in (".mp4", ".mov", ".png", ".json"):
            seen.setdefault(found.group(0), []).append(file)
    for job, files in seen.items():
        if not any(file.suffix.lower() in (".mp4", ".mov") for file in files):
            continue
        said = clips.get(f"renders/{job}.mp4") or next((clips[rel(file)] for file in files if rel(file) in clips), {})
        folder, name = take_names.folder_of(base, job, said.get("title"), said.get("prompt"), "clip", now=_when(files[0]))
        pairs = [(file, folder / (folder.name + file.name[len(job):])) for file in files]
        parts = base / job
        folders = []
        if parts.is_dir() and not parts.is_symlink():
            pairs += [(file, folder / file.relative_to(parts)) for file in sorted(parts.rglob("*")) if file.is_file()]
            folders = [parts]
        add("clip", job, pairs, folders)
    return out


def plan(home: Path, clips: dict | None = None, busy: set[str] = frozenset()) -> dict:
    """What a run would do: {"moves": [group, ...], "skipped": [{"what", "id", "why"}], "files", "takes"}."""
    elsewhere = named_elsewhere(home)
    moves, skipped = [], []
    for group in groups(home, clips):
        why = ""
        if group["id"] in busy:
            why = "its job is queued or running"
        elif any((home / new).exists() for _, new in group["files"]):
            why = "a file is already at its new place"
        elif any(old in elsewhere for old, _ in group["files"] if not old.endswith((".json", ".txt"))):
            why = "a film, a timeline or another project names it by its path"
        (skipped.append({"what": group["what"], "id": group["id"], "why": why}) if why else moves.append(group))
    kinds = {}
    for group in moves:
        kinds[group["what"]] = kinds.get(group["what"], 0) + 1
    return {"moves": moves, "skipped": skipped, "takes": len(moves), "files": sum(len(g["files"]) for g in moves), "kinds": kinds}


def _index(home: Path) -> Path:
    return home / "organize" / "moved.json"


def moved(home: Path) -> dict:
    """old path -> new path for everything a run moved (and has not been undone)."""
    said = _record(_index(home))
    return {str(a): str(b) for a, b in said.items()} if said else {}


def _save(file: Path, value) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    part = file.with_name(file.name + ".part")
    part.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
    part.replace(file)


def _rewrite(home: Path, records: list[str], mapping: dict) -> None:
    """A take record names its own files by path: those follow the move (and come back with an undo)."""
    for one in records:
        file = home / one
        said = _record(file)
        changed = {key: mapping[value] for key, value in said.items() if isinstance(value, str) and value in mapping}
        if changed:
            _save(file, {**said, **changed})


def apply(home: Path, found: dict) -> dict:
    """Move what ``plan`` found. A take whose first file cannot be moved is left whole; one that fails part way is put
    back. Returns {"takes", "files", "failed": [{"id", "why"}]} and writes the list an undo reads."""
    done, failed, count, before = {}, [], 0, moved(home)
    for group in found["moves"]:
        made = []
        try:
            for old, new in group["files"]:
                (home / new).parent.mkdir(parents=True, exist_ok=True)
                os.replace(home / old, home / new)
                made.append((old, new))
        except OSError as error:
            for old, new in reversed(made):  # put back what was moved of this take
                try:
                    os.replace(home / new, home / old)
                except OSError:
                    pass
            failed.append({"id": group["id"], "why": str(error)[-160:]})
            continue
        mapping = dict(made)
        _rewrite(home, [new for _, new in made if new.endswith(".take.json")], mapping)
        for folder in group["folders"]:
            _prune(home / folder)
        done.update(mapping)
        count += 1
        # written down take by take: a run that is cut short can still be undone, and its paths still open
        _save(home / "organize" / "last.json", {"at": time.time(), "moved": done})
        _save(_index(home), {**before, **done})
    _save(home / "organize" / "last.json", {"at": time.time(), "moved": done})
    _save(_index(home), {**before, **done})
    return {"takes": count, "files": len(done), "failed": failed}


def _prune(folder: Path) -> None:
    """An emptied folder, bottom up; one with a file left in it stays."""
    if not folder.is_dir() or folder.is_symlink():
        return
    for inner in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)) + [folder]:
        try:
            inner.rmdir()
        except OSError:
            pass


def undo(home: Path) -> dict:
    """The last run, taken back: every file to where it was. {"files", "missing": [paths not found any more]}."""
    last = _record(home / "organize" / "last.json").get("moved") or {}
    back, missing, index = {}, [], moved(home)
    for old, new in last.items():
        try:
            (home / old).parent.mkdir(parents=True, exist_ok=True)
            if (home / old).exists():
                raise FileExistsError(old)
            os.replace(home / new, home / old)
            back[new] = old
            index.pop(old, None)
        except OSError:
            missing.append(new)
    _rewrite(home, [old for old in back.values() if old.endswith(".take.json")], back)
    for new in {Path(new).parent for new in back}:
        _prune(home / new)
    _save(_index(home), index)
    _save(home / "organize" / "last.json", {"at": time.time(), "moved": {}})
    return {"files": len(back), "missing": missing, "back": {old: new for new, old in back.items()}}
