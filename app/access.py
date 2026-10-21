"""The studio's access key: one persistent secret per studio folder, asked of every request for the studio's data.

The page is a key away from everything it can do (render, read the Library, run an agent with the user's sign-ins),
so a request to /api/ or /files/ carries the key, from any client, this computer's own included: a page served to a
local process or to a `tailscale serve` user is no proof of who asks (Jupyter's reasoning). The key is
``<studio folder>/access-key`` (made once, mode 0600, kept across restarts and updates). It is given as

- the cookie ``studio_key_<port>`` the page keeps: set by opening ``/?key=<key>`` once (the launcher and the Mac app
  open the page that way) or by typing the key in the page's own prompt (POST /api/key), on a phone too;
- the header ``X-Studio-Key`` (or ``Authorization: Bearer``), from the studio's own tools, which read the file:
  the launcher, the MCP server, studio-check, a Mac or Spark leading workers (each worker's file, read over ssh).

``scripts/studio-key.sh`` prints the key and the address that opens the page with it. ``STUDIO_ACCESS_KEY=off``
turns the check off (the tests do, and a person may, at their own risk); a new key is made by deleting the file and
restarting the app, which signs every browser out."""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

FILE = "access-key"
HEADER = "x-studio-key"
COOKIE = "studio_key_"  # + the port: two apps on one computer (a worker's tunnel, a second studio) keep their own
YEAR = 400 * 24 * 3600  # a browser's longest cookie


def enabled() -> bool:
    return os.environ.get("STUDIO_ACCESS_KEY", "on").strip().lower() not in ("off", "0", "no", "false")


def path(home: Path) -> Path:
    return Path(home) / FILE


def read(home: Path) -> str:
    """The key of the studio folder ``home``, "" when it has none yet (its app has not started)."""

    try:
        return path(home).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure(home: Path) -> str:
    """The folder's key, made now when there is none: 0600 from the start, written whole or not at all."""

    known = read(home)
    if known:
        try:
            if path(home).stat().st_mode & 0o077:
                path(home).chmod(0o600)  # a copy that came with looser rights
        except OSError:
            pass
        return known
    Path(home).mkdir(parents=True, exist_ok=True)
    made = secrets.token_urlsafe(24)
    part = path(home).with_name(f"{FILE}.{os.getpid()}.part")
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(made + "\n")
    try:
        os.link(part, path(home))  # the first of two starting apps wins; the other reads its key
    except FileExistsError:
        pass
    finally:
        part.unlink(missing_ok=True)
    return read(home)


def matches(given: str | None, key: str) -> bool:
    return bool(given) and bool(key) and hmac.compare_digest(given.encode(), key.encode())


def given(headers: dict, port: str | int) -> list[str]:
    """What a request offers as the key (headers in lower case): the header, a bearer token, this app's cookie."""

    offered = [headers.get(HEADER, "")]
    bearer = headers.get("authorization", "")
    if bearer[:7].lower() == "bearer ":
        offered.append(bearer[7:].strip())
    for part in headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE + str(port):
            offered.append(value.strip())
    return [one for one in offered if one]


def allowed(headers: dict, port: str | int, key: str) -> bool:
    return any(matches(one, key) for one in given(headers, port))


# answered without the key: the route that takes it from the page's prompt, and the updater's health check, which
# says only the running server's update session and is asked by a launcher started before the key (an update from
# such a version would otherwise be taken for a failed start and rolled back)
OPEN = ("/api/key", "/api/updates/health")


def needs_key(path_: str) -> bool:
    """The studio's data and actions; the page itself, its scripts and pictures load without the key."""

    return (path_.startswith("/api/") or path_.startswith("/files/")) and path_.rstrip("/") not in OPEN


def cookie(port: str | int, key: str, secure: bool) -> str:
    return (f"{COOKIE}{port}={key}; Path=/; Max-Age={YEAR}; HttpOnly; SameSite=Lax" + ("; Secure" if secure else ""))


def headers_for(home: Path) -> dict:
    """The header a tool of this computer sends to the app of the studio folder ``home``."""

    key = read(home)
    return {"X-Studio-Key": key} if key else {}
