"""Readable names for a take's folder and files (issue #257): the month it was made, then its date, time, a short
title and the job's own token, the same stem for the folder and the files in it.

    sound/sfx/2026-11/2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a/
        2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a.wav
        2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a.take.json

Deterministic and mechanical: the first words of what the person wrote, made safe for every file system, never a
model's summary. The names are chosen once, when the job is built, and never changed afterwards. Older takes stay
where they are: the Library reads both layouts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import unicodedata

WORDS, LIMIT = 6, 48  # the title part: at most six words and 48 characters
# names Windows keeps for devices, with or without an extension
RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def slug(text: str, fallback: str = "take") -> str:
    """The first words of ``text`` as a file-system-safe part of a name: lower case ASCII letters and digits joined by
    hyphens, at most six words and 48 characters; ``fallback`` when nothing usable is left."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", plain)[:WORDS]
    out = ""
    for word in words:
        longer = f"{out}-{word}" if out else word
        if len(longer) > LIMIT:
            break
        out = longer
    out = out or (words[0][:LIMIT] if words else "")
    if not out or out in RESERVED:
        out = re.sub(r"[^a-z0-9]+", "-", str(fallback).lower()).strip("-") or "take"
    return out


def when(job_id: str, now: float | None = None) -> tuple[str, str]:
    """(``YYYY-MM-DD``, ``HHMMSS``) of a job from its id (``MMDD-HHMMSS-token``, made when it was queued). The id has
    no year: it is this year's, or last year's for a job queued in a month still ahead of now (December, read in
    January). An id of another shape takes today's date and the time now."""
    now = time.time() if now is None else now
    here = time.localtime(now)
    found = re.match(r"(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:-|$)", str(job_id))
    if found:
        month, day, hour, minute, second = (int(part) for part in found.groups())
        if 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60:
            year = here.tm_year - (1 if month > here.tm_mon else 0)
            return f"{year:04d}-{month:02d}-{day:02d}", f"{hour:02d}{minute:02d}{second:02d}"
    return time.strftime("%Y-%m-%d", here), time.strftime("%H%M%S", here)


def token(job_id: str) -> str:
    """What tells this job from another made in the same second: the id after its date and time (``ee3e3a``,
    ``adc366-vsa``), or the whole id made safe when it has another shape."""
    found = re.match(r"\d{4}-\d{6}-(.+)$", str(job_id))
    return re.sub(r"[^A-Za-z0-9-]+", "-", found.group(1) if found else str(job_id)).strip("-")[:24] or "job"


def stem(job_id: str, title: str, fallback: str = "take", now: float | None = None) -> tuple[str, str]:
    """(the month folder ``YYYY-MM``, the stem a take's folder and files share)."""
    day, clock = when(job_id, now)
    return day[:7], f"{day}_{clock}_{slug(title, fallback)}_{token(job_id)}"


def folder(root: Path, job_id: str, title: str, fallback: str = "take", now: float | None = None) -> tuple[Path, str]:
    """(the take's own folder under ``root``, its stem). The same answer every time for the same job: a job's command
    is built when it is queued and again when it runs, and both must name one place. Two jobs never share it, since
    the stem ends in the job's own token."""
    month, name = stem(job_id, title, fallback, now)
    return root / month / name, name


def find(root: Path, job_id: str, suffix: str) -> Path | None:
    """A take's file under ``root`` by the job's id and the file's ending (``.take.json``, ``.wav``, ``.mp4``), in
    either layout: beside the others as ``<root>/<id><suffix>`` (takes made before the folders), or in the take's own
    folder, whose name starts with the job's date and time and ends in its token. None when it is not on disk."""
    flat = root / f"{job_id}{suffix}"
    if flat.is_file():
        return flat
    name = stem(job_id, "")[1]
    clock = "_".join(name.split("_")[:2])[4:]  # "-MM-DD_HHMMSS": the id has no year, so any year's folder
    pattern = f"????{clock}_*_{name.rsplit('_', 1)[1]}"
    for found in sorted(root.glob(f"*/{pattern}/{pattern}{suffix}")):
        if found.is_file():
            return found
    return None


def clip(home: Path, job_id: str, title: str | None, prompt: str | None) -> Path:
    """Where a clip made from this job goes when no place was named for it: its own folder under ``renders``, by
    month, named by the job's title (or the first words of its prompt) and its token. Asked for when the job is
    built, by the queue when another clip waits for it, and by a long video for its parts: one answer every time."""
    folder, name = folder_of(home / "renders", job_id, title, prompt, "clip")
    return folder / f"{name}.mp4"


def folder_of(root: Path, job_id: str, title: str | None, prompt: str | None, fallback: str) -> tuple[Path, str]:
    plain = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode("ascii")
    words = title if re.search(r"[A-Za-z0-9]", plain) else prompt  # a title with no usable word (or none) leaves the prompt's
    return folder(root, job_id, words or "", fallback)


def pictures(home: Path, job_id: str, params: dict) -> Path:
    """The folder a job's pictures go to: its own under ``scouts``, by month, named by the first words of what was
    asked for and the job's token. A command built only to be checked ("check") names the old flat place."""
    if job_id == "check":
        return home / "scouts" / job_id
    several = params.get("prompts")
    words = params.get("prompt") or (several[0] if isinstance(several, list) and several else "")
    return folder(home / "scouts", job_id, str(words or ""), "picture")[0]


def named(outputs: list[str]) -> list[str]:
    """A picture job's files under their readable names: in a take's own folder, ``scout_s7.png`` (what the render
    scripts write on every platform) becomes ``<folder name>_s7.png``, so a picture copied out of its folder still
    says what it is. The contact sheet and any other file keep their names. Returns the paths as they are now."""
    out = []
    for path in map(Path, outputs):
        own = bool(re.fullmatch(r"\d{4}-\d{2}", path.parent.parent.name)) and path.parent.parent.parent.name == "scouts"
        if own and path.name.startswith("scout_") and path.suffix.lower() == ".png" and path.is_file():
            target = path.with_name(path.parent.name + path.name[len("scout"):])
            path.replace(target)
            path = target
        out.append(str(path))
    return out


def title_of(path) -> str:
    """The words a file is known by: the title part of a take's stem (``<date>_<time>_<title>_<token>``), or the
    file's own name without its ending when it is not one."""
    name = Path(str(path or "")).stem
    found = re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}_(.+)_[A-Za-z0-9-]+", name)
    return found.group(1).replace("-", " ") if found else name


def derived(home: Path, job_id: str, what: str, source=None, tag: str = "", suffix: str = ".mp4") -> Path:
    """Where a clip made from another clip goes (an upscale, an HLG grade, a swap, a title card): its own folder
    under ``renders`` by month, named by what was done and the clip it was done to. ``tag`` tells two files of one
    job apart (``-hlg``, ``-sdr``). A command built only to be checked ("check") names the old flat place."""
    if job_id == "check":
        return home / "renders" / f"{job_id}{tag}{suffix}"
    place, name = folder(home / "renders", job_id, f"{what} {title_of(source)}".strip(), what)
    return place / f"{name}{tag}{suffix}"


MEDIA_NAMES = ("sound", "foley")  # the fixed names some writers give a take's files: they take the folder's stem


def settle(home: Path, outputs: list[str], job_id: str, title: str, fallback: str = "take") -> list[str]:
    """A finished job whose files are in a flat folder named by its id (``<root>/<job id>/sound.wav``: the bounded
    Windows writers, which check and guard that folder while they run) gets its readable place now that it is done:
    the folder moves to ``<root>/<YYYY-MM>/<stem>/``, ``sound.*`` and ``foley.*`` become ``<stem>.*``, a picture
    ``scout_s7.png`` becomes ``<stem>_s7.png``, and the take records in it name the new paths. Other files (logs,
    measurements, a contact sheet) move along under their own names. Returns ``outputs`` (paths relative to
    ``home``) as they are now. Anything unexpected (another layout, a folder in the way, a locked file) leaves
    everything where it was: a take in its old place is still a good take."""
    if not outputs:
        return outputs
    first = Path(outputs[0])
    old = home / first.parent
    if first.parent.name != job_id or not old.is_dir() or old.is_symlink() or len(first.parts) < 3:
        return outputs
    new, name = folder(old.parent, job_id, title, fallback)
    if new.exists():
        return outputs
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)
    except OSError:
        return outputs
    moved = {}
    for file in sorted(path for path in new.iterdir() if path.is_file()):
        head = file.name.split(".", 1)[0]
        target = file
        if head in MEDIA_NAMES:
            target = file.with_name(name + file.name[len(head):])
        elif file.name.startswith("scout_") and file.suffix.lower() == ".png":
            target = file.with_name(name + file.name[len("scout"):])
        if target != file and not target.exists():
            try:
                file.replace(target)
            except OSError:
                target = file
        else:
            target = file
        moved[(old / file.name).relative_to(home).as_posix()] = target.relative_to(home).as_posix()
    for record in new.glob("*.take.json"):  # what a record says of its own files follows them
        try:
            said = json.loads(record.read_text(encoding="utf-8"))
            if isinstance(said, dict):
                changed = {key: moved[value] for key, value in said.items() if isinstance(value, str) and value in moved}
                if changed:
                    part = record.with_name(record.name + ".part")
                    part.write_text(json.dumps({**said, **changed}, indent=1) + "\n", encoding="utf-8")
                    part.replace(record)
        except (OSError, ValueError):
            continue
    return [moved.get(Path(path).as_posix(), path) for path in outputs]
