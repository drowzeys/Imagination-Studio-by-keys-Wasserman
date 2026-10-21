"""Another machine's Studio app, read over its own API: a finished job, and a clip it made.

A film is often rendered on several machines, each with its own app (workers of a DGX Spark fabric, a Mac beside
them), while the Slate project and the tools a check needs (ffmpeg, a speech-to-text model) are on one. What this
reads is what that app's page reads: `/api/jobs/<id>` and `/files/<path>`. Nothing is sent but the request.
Each app asks for its own access key (app/access.py): a key given for an address is kept in memory (``remember``,
never in a job's saved settings) and sent with every request to it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SUFFIXES = (".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp")
LIMIT = 4 << 30  # the largest file taken


def address(given: str) -> str:
    """`http://host:port` of an app, from what a person types (`spark-5:7870`, `http://10.0.0.5:7870/`); ValueError
    with the reason for anything else."""

    text = (given or "").strip().rstrip("/")
    if text and not re.match(r"^[a-z]+://", text):
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.username:
        raise ValueError("another app's address is http://host:port, as its own page is opened")
    return f"{parts.scheme}://{parts.netloc}"


KEYS: dict[str, str] = {}  # access keys by address, from the person (remember) or a Sparks tunnel


def remember(base: str, key: str | None) -> None:
    """The access key of the app at ``base``, given by the person with its address."""

    key = (key or "").strip()
    if key:
        KEYS[address(base)] = key


def opened(url: str, base: str, key: str | None, timeout: float):
    key = key or KEYS.get(address(base), "")
    request = urllib.request.Request(url, headers={"X-Studio-Key": key} if key else {})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise OSError(f"{address(base)} asks for its access key: give it with the address (on that computer, "
                          "bash scripts/studio-key.sh prints it)") from None
        raise


def job(base: str, job_id: str, timeout: float = 20, key: str | None = None) -> dict:
    """A job as that app reports it; LookupError when it has none, OSError when the app does not answer."""

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id or ""):
        raise ValueError("a job id is letters, digits and dashes")
    try:
        with opened(f"{address(base)}/api/jobs/{job_id}", base, key, timeout) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise LookupError(f"{address(base)} has no job {job_id} (its app forgets its jobs when it restarts: give the clip's path)") from None
        raise OSError(f"{address(base)} answered {error.code}") from None


def fetch(base: str, path: str, target: Path, timeout: float = 60, limit: int = LIMIT, key: str | None = None) -> Path:
    """The file `path` of that app's studio folder, written to ``target`` (through a .part file). ValueError for a
    path that is not a clip or a picture, LookupError when that app has no such file."""

    path = (path or "").strip().lstrip("/")
    if not path or ".." in Path(path).parts or Path(path).suffix.lower() not in SUFFIXES:
        raise ValueError("a clip's path is one inside the other app's studio folder, like renders/1013-131100-5eb633.mp4")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with opened(f"{address(base)}/files/{urllib.parse.quote(path)}", base, key, timeout) as reply, open(part, "wb") as out:
            done = 0
            while chunk := reply.read(1 << 20):
                done += len(chunk)
                if done > limit:
                    raise ValueError(f"the file is over {limit >> 30} GB")
                out.write(chunk)
        if not done:
            raise LookupError(f"{address(base)} sent an empty file for {path}")
        part.replace(target)
    except urllib.error.HTTPError as error:
        if error.code in (400, 404):
            raise LookupError(f"{address(base)} has no file {path}") from None
        raise OSError(f"{address(base)} answered {error.code}") from None
    finally:
        part.unlink(missing_ok=True)
    return target
