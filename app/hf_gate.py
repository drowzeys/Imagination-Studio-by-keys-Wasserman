"""Gated weights on Hugging Face: what stands between Install and the file, and the one thing the person does.

A repository's author may gate its files: "auto" (accept the licence on the page and the files open at once) or
"manual" (the author approves each request). Hugging Face has no way to accept a licence for someone: the person
does it on the model's page with their own account. Everything around it the studio does: it asks the page whether
the repo is gated (`/api/models/<repo>`, no sign-in needed), takes the person's Hugging Face read token once and keeps
it where Hugging Face's own tools keep it (`~/.cache/huggingface/token`, 0600, so `hf download` and the setup scripts
use it too; HF_TOKEN in the environment wins), checks access with a one-byte read of the file, and the page installs
by itself as soon as access is granted. The token goes to huggingface.co only and is never shown, logged or put in
the studio's settings.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

HUB = "https://huggingface.co"
TIMEOUT = 20
REPO_TTL = 600
TOKEN_PAGE = HUB + "/settings/tokens/new?tokenType=read"
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
lock = threading.Lock()
gates: dict = {}  # repo -> (monotonic time, gated: False | "auto" | "manual")


def token_file() -> Path:
    return Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))).expanduser() / "token"


def token() -> str:
    """The person's own Hugging Face token: HF_TOKEN, else the one Hugging Face's tools saved."""

    found = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if found:
        return found
    try:
        return token_file().read_text().strip()
    except OSError:
        return ""


def repo_of(url: str) -> str | None:
    """owner/name of a huggingface.co file address (…/owner/name/resolve/…), else None."""

    parts = urlsplit(url or "")
    if parts.scheme != "https" or parts.hostname != "huggingface.co":
        return None
    bits = [b for b in parts.path.split("/") if b]
    repo = "/".join(bits[:2]) if len(bits) >= 2 and bits[0] not in ("datasets", "spaces", "api") else None
    return repo if repo and REPO.fullmatch(repo) else None


def call(url: str, auth: str = "", method: str = "GET", extra: dict | None = None):
    headers = {"User-Agent": "ImaginationStudio", **(extra or {})}
    if auth:
        headers["Authorization"] = "Bearer " + auth
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=TIMEOUT)


def gated(repo: str) -> str | bool | None:
    """False, "auto" or "manual" as the hub says (kept ten minutes); None when it does not answer."""

    with lock:
        cached = gates.get(repo)
    if cached and time.monotonic() - cached[0] < REPO_TTL:
        return cached[1]
    try:
        with call(f"{HUB}/api/models/{repo}") as reply:
            value = json.loads(reply.read(1 << 20)).get("gated", False)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    value = value if value in ("auto", "manual") else False
    with lock:
        gates[repo] = (time.monotonic(), value)
    return value


def whoami(auth: str) -> str | None:
    try:
        with call(f"{HUB}/api/whoami-v2", auth) as reply:
            return str(json.loads(reply.read(1 << 20)).get("name") or "") or None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def access(url: str, auth: str) -> str:
    """granted | sign_in (no token, or a token the hub refuses) | accept | pending (the author has not approved yet)."""

    if not auth:
        return "sign_in"
    try:
        with call(url, auth, extra={"Range": "bytes=0-0"}):
            return "granted"
    except urllib.error.HTTPError as error:
        said = ""
        try:
            said = error.read(4096).decode("utf-8", "replace")
        except OSError:
            pass
        if error.code == 401:
            return "sign_in"
        if error.code == 403:
            return "pending" if re.search(r"awaiting|review|pending", said, re.I) else "accept"
        raise


def gate_of(url: str) -> dict:
    """What Install needs for one file: {gated, repo, page, access, user}. A file that is not on the hub, or not gated,
    is {gated: False, access: "open"}."""

    repo = repo_of(url)
    if not repo:
        return {"gated": False, "access": "open"}
    kind = gated(repo)
    if kind is False:
        return {"gated": False, "access": "open", "repo": repo}
    out = {"gated": kind or "unknown", "repo": repo, "page": f"{HUB}/{repo}", "token_page": TOKEN_PAGE}
    auth = token()
    try:
        out["access"] = access(url, auth)
    except (urllib.error.URLError, OSError) as error:
        out["access"] = "unknown"
        out["detail"] = f"Hugging Face did not answer: {getattr(error, 'reason', error)}"[:200]
    if auth and out["access"] != "sign_in":
        out["user"] = whoami(auth)
    if auth and out["access"] == "sign_in":
        out["detail"] = "Hugging Face did not accept the saved token: make a new read token."
    if kind is None and out["access"] == "granted":
        out["gated"] = False  # the hub's answer about the repo was lost, but the file opens: nothing to do
    return out


def save_token(given) -> dict:
    """A read token checked with the hub, then kept where Hugging Face's tools keep it."""

    given = str(given or "").strip()
    if not re.fullmatch(r"hf_[A-Za-z0-9]{20,100}", given):
        raise HTTPException(400, "a Hugging Face token starts with hf_: make a read token and paste it")
    name = whoami(given)
    if not name:
        raise HTTPException(400, "Hugging Face did not accept that token: make a new read token")
    target = token_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".new")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(given + "\n")
    os.chmod(part, 0o600)
    part.replace(target)
    return {"user": name, "signed_in": True, "where": "~/" + str(target.relative_to(Path.home())) if Path.home() in target.parents else str(target)}


def status() -> dict:
    auth = token()
    name = whoami(auth) if auth else None
    return {"signed_in": bool(name), "user": name, "from_environment": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")),
            "token_page": TOKEN_PAGE, "saved_token_refused": bool(auth) and not name}


def attach(guard=None) -> APIRouter:
    """The routes; `guard(request)` is the app's same-origin check for writes when it has one."""

    router = APIRouter()

    @router.get("/api/hf")
    def hf_status() -> dict:
        return status()

    @router.post("/api/hf/token")
    def hf_token(body: dict, request: Request) -> dict:
        if guard:
            guard(request)
        return save_token(body.get("token"))

    return router
