"""Readable names for a take's folder and files (issue #257): the month it was made, then its date, time, a short
title and the job's own token, the same stem for the folder and the files in it.

    sound/sfx/2026-11/2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a/
        2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a.wav
        2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a.take.json

Deterministic and mechanical: the first words of what the person wrote, made safe for every file system, never a
model's summary. The names are chosen once, when the job is built, and never changed afterwards. Older takes stay
where they are: the Library reads both layouts."""
from __future__ import annotations

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
