"""The studio's access key: one persistent secret per studio folder, for reaching the studio from another device.

On the computer running the studio nothing is asked: a browser or tool opened as localhost works as before. From
anywhere else (a phone on Tailscale, another computer on the network) every request to /api/ and /files/ carries the
key, ``<studio folder>/access-key`` (made once, mode 0600, kept across restarts and updates), as

- the cookie ``studio_key_<port>``: set by opening ``/?key=<key>`` once (Settings → Access key shows a QR code and a
  link with it for a phone) or by typing the key in the page's prompt (POST /api/key);
- the header ``X-Studio-Key`` (or ``Authorization: Bearer``), from a tool on another machine.

``scripts/studio-key.sh`` prints the key and the addresses. ``STUDIO_ACCESS_KEY=off`` turns the check off (the tests
do); Settings → Access key, or deleting the file and restarting, makes a new key and signs every device out."""
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


def rotate(home: Path) -> str:
    """A new key in place of the old one (every browser and phone signed in so far asks again); the new key."""

    made = secrets.token_urlsafe(24)
    part = path(home).with_name(f"{FILE}.{os.getpid()}.part")
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(made + "\n")
    os.replace(part, path(home))
    return made


# ---------- this computer itself: no key ----------
# The key guards remote access (a phone, Tailscale, another computer on the network). A browser on the computer running
# the studio, opened as localhost, needs none: the request comes from a loopback address *and* names a loopback host.
# `tailscale serve` also connects from a loopback address, but under the computer's .ts.net name, so it is asked.
LOOPBACK_CLIENTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def local(client_host: str | None, host_header: str, own_addresses=(), own_names=()) -> bool:
    """Whether a request is this computer's own browser or tool: a loopback client and a loopback Host, or a client
    on one of this computer's own network addresses (only this computer can complete a connection from one) naming
    this computer by one of those addresses or its own name (one word, or .local). A proxy here, such as `tailscale
    serve`, names a .ts.net Host and is asked for the key either way."""

    name = host_header.strip().lower()
    name = name[1:].split("]", 1)[0] if name.startswith("[") else name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    name = name.rstrip(".")
    if client_host in LOOPBACK_CLIENTS:
        return name in LOOPBACK_HOSTS
    if not client_host or client_host not in own_addresses:
        return False
    if name in own_addresses:
        return True
    label, _, rest = name.partition(".")
    return rest in ("", "local") and label in {one.lower() for one in own_names if one}


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
