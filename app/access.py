"""The studio's access key: one persistent secret per studio folder, for reaching the studio from another device.

On the computer running the studio nothing is asked: a browser or tool opened as localhost works as before. From
anywhere else (a phone on Tailscale, another computer on the network) every request to /api/ and /files/ carries the
key, ``<studio folder>/access-key`` (made once, mode 0600, kept across restarts and updates), as

- the cookie ``studio_key_<port>``: set by opening ``/?key=<key>`` once (Settings → Access key shows a QR code and a
  link with it for a phone) or by typing the key in the page's prompt (POST /api/key);
- the header ``X-Studio-Key`` (or ``Authorization: Bearer``), from a tool on another machine.

``scripts/studio-key.sh`` prints the key and the addresses. ``STUDIO_ACCESS_KEY=off`` turns the check off (the tests
do); ``STUDIO_TRUST_TAILNET=1`` lets the owner's own tailnet in without it, and ``STUDIO_TRUST_NETWORKS`` the owner's
own private networks (``tailnet`` below); Settings → Access key, or deleting the file and restarting, makes a new key and signs every device out."""
from __future__ import annotations

import hmac
import ipaddress
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


def windows_private(target: Path) -> None:
    """Protected Windows DACL: only the file's owner has access, before a secret is written."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = api.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
    convert.restype = wintypes.BOOL
    protect = api.SetFileSecurityW
    protect.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    protect.restype = wintypes.BOOL
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not protect(str(target), 0x80000004, descriptor):  # protected DACL, without inherited grants
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free(descriptor)


def ensure(home: Path) -> str:
    """The folder's key, made now when there is none: 0600 from the start, written whole or not at all."""

    known = read(home)
    if known:
        if os.name == "nt":
            windows_private(path(home))
            return known
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
        if os.name == "nt":
            windows_private(part)
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
        if os.name == "nt":
            windows_private(part)
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
    """Whether a request is this computer's own browser or tool. A loopback client passes naming localhost, one of
    this computer's own network addresses or its own `.local` name (which resolves to loopback on the machine
    itself); a client on one of the computer's own network addresses (only this computer can complete a connection
    from one) passes naming one of those addresses or its own name (one word, or .local). A proxy here, such as
    `tailscale serve`, names a .ts.net Host or a tailnet short name and is asked for the key."""

    name = host_header.strip().lower()
    name = name[1:].split("]", 1)[0] if name.startswith("[") else name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    name = name.rstrip(".")
    loopback = client_host in LOOPBACK_CLIENTS
    if loopback and name in LOOPBACK_HOSTS:
        return True
    if not loopback and (not client_host or client_host not in own_addresses):
        return False
    if name in own_addresses:
        return True
    label, _, rest = name.partition(".")
    return (rest == "local" or (rest == "" and not loopback)) and label in {one.lower() for one in own_names if one}


# ---------- a trusted tailnet: no key, only when the owner asks (STUDIO_TRUST_TAILNET=1) ----------
# Off by default, and then nothing below runs. On, it lets in (1) a device that reaches the studio straight on a
# Tailscale address (WireGuard ties every tailnet address to one authenticated device, so the client address is the
# device's own), including this computer opening its own Tailscale address, and (2) this computer's own browser under
# its one-word name over loopback (the name resolves to 127.0.0.1 here), but only when the request carries no proxy
# header: `tailscale serve` and `tailscale funnel` connect from loopback too and forward the request with
# X-Forwarded-* and Tailscale-* headers, and those stay asked (a funnel request comes from the internet).
# STUDIO_TRUST_NETWORKS (trusted_networks) adds the owner's own private networks, such as the home LAN: a device
# connecting from one of them needs no key either. Unset, nothing of it runs.
TAILNET_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded", "x-real-ip")


def trust_tailnet() -> bool:
    return os.environ.get("STUDIO_TRUST_TAILNET", "").strip().lower() in ("1", "on", "yes", "true")


def trusted_networks() -> list:
    """STUDIO_TRUST_NETWORKS (comma-separated, e.g. 192.168.1.0/24): the owner's own networks whose devices need no
    key. Only private networks count (RFC 1918, unique local IPv6, Tailscale's range): a public one, or a typo such as
    0.0.0.0/0, is left out, so the setting can never open the studio to the internet."""

    found = []
    for one in os.environ.get("STUDIO_TRUST_NETWORKS", "").split(","):
        try:
            network = ipaddress.ip_network(one.strip(), strict=False)
        except ValueError:
            continue
        if network.is_private and not network.is_loopback and network.prefixlen >= (8 if network.version == 4 else 7) \
                or any(network.subnet_of(tail) for tail in TAILNET_NETWORKS if tail.version == network.version):
            found.append(network)
    return found


def in_networks(address: str | None, networks) -> bool:
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == network.version and ip in network for network in networks)


def on_tailnet(address: str | None) -> bool:
    return in_networks(address, TAILNET_NETWORKS)


def proxied(headers: dict) -> bool:
    """Whether a request (headers in lower case) was passed on by a proxy, such as `tailscale serve` or `funnel`."""

    return any(name in headers for name in PROXY_HEADERS) or any(name.startswith("tailscale-") for name in headers)


def tailnet(client_host: str | None, host_header: str, headers: dict, own_names=()) -> bool:
    """Whether STUDIO_TRUST_TAILNET or STUDIO_TRUST_NETWORKS lets this request in without the key (see above)."""

    if in_networks(client_host, trusted_networks()):
        return True
    if not trust_tailnet():
        return False
    if on_tailnet(client_host):
        return True
    if client_host not in LOOPBACK_CLIENTS or proxied(headers):
        return False
    name = host_header.strip().lower()
    name = name[1:].split("]", 1)[0] if name.startswith("[") else name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    name = name.rstrip(".")
    return "." not in name and name in {one.lower() for one in own_names if one}


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
