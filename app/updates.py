"""Source updates for clean Git installations. The launcher applies them only after the app exits.

No setup scripts, model downloads, dependency installs or merge conflict resolution run here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request

INTERVAL = 6 * 60 * 60
GIT_TIMEOUT = 45


class UpdateError(Exception):
    pass


def git(pack: Path, *args: str, timeout: int = GIT_TIMEOUT) -> str:
    try:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(pack), *args],
                                capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oConnectTimeout=10"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("Git could not finish. Check your connection and Git installation, then try again.") from error
    if result.returncode:
        raise UpdateError("Git could not " + args[0] + ". Check repository access and try again.")
    return result.stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temp, 0o600)
    temp.replace(path)


def repository(url: str) -> str:
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", url)
    if not match:
        raise UpdateError("In-app updates need a GitHub installation with an HTTPS or SSH origin.")
    return match[1]


def branch_name(pack: Path, name: str) -> str:
    try:
        valid = bool(name) and not name.startswith("-") and git(pack, "check-ref-format", "--branch", name, timeout=10) == name
    except UpdateError:
        valid = False
    if not valid:
        raise UpdateError("The branch to follow (" + name[:80] + ") is not a valid branch name.")
    return name


def default_branch(pack: Path, fetch: bool) -> str:
    if fetch:
        remote_head = git(pack, "ls-remote", "--symref", "origin", "HEAD")
        match = re.search(r"^ref: refs/heads/(.+)\tHEAD$", remote_head, re.M)
        branch = match[1] if match else ""
    else:
        branch = git(pack, "symbolic-ref", "refs/remotes/origin/HEAD").removeprefix("refs/remotes/origin/")
    if not branch or branch.startswith("-") or git(pack, "check-ref-format", "--branch", branch) != branch:
        raise UpdateError("The repository's default branch could not be identified.")
    return branch


def upstream_of(pack: Path, attached: str) -> tuple[str, str] | None:
    """The checked-out branch's upstream as (remote, branch), or None when it tracks nothing."""
    try:
        remote = git(pack, "config", "--get", "branch." + attached + ".remote", timeout=10)
        merge = git(pack, "config", "--get", "branch." + attached + ".merge", timeout=10)
    except UpdateError:
        return None
    return remote, merge.removeprefix("refs/heads/") if merge.startswith("refs/heads/") else ""


def resolve(pack: Path, attached: str, fetch: bool, known: dict) -> str:
    """The branch on origin to follow: STUDIO_UPDATE_BRANCH, else the checkout's origin upstream, else the default."""
    override = os.environ.get("STUDIO_UPDATE_BRANCH", "").strip()
    if override:
        known["following"] = branch = branch_name(pack, override)
        if attached != branch:
            raise UpdateError("Updates follow " + branch + " (set by STUDIO_UPDATE_BRANCH). This checkout is on "
                              + attached + "; your branch is kept.")
        return branch
    tracked = upstream_of(pack, attached)
    if tracked:
        remote, branch = tracked
        if remote != "origin" or not branch:
            raise UpdateError("This branch (" + attached + ") tracks " + (remote if remote != "." else "a local branch")
                              + ", not a branch on origin. Updates come only from origin; set the branch to track origin to update it.")
        known["following"] = branch = branch_name(pack, branch)
        return branch
    known["following"] = branch = known["default_branch"] = default_branch(pack, fetch)
    if attached != branch:
        raise UpdateError("Updates follow the repository's default branch (" + branch + ") or the branch this checkout tracks on origin. "
                          "This checkout is on " + attached + ", which tracks no branch on origin; your branch is kept.")
    return branch


def inspect(pack: Path, fetch: bool = False) -> dict:
    """Fetch into a private ref; never change the user's branch or working files while the app runs."""
    known = {}
    try:
        return _inspect(pack, fetch, known)
    except UpdateError as error:
        error.info = known
        raise


def _inspect(pack: Path, fetch: bool, known: dict) -> dict:
    if git(pack, "rev-parse", "--show-toplevel") != str(pack.resolve()):
        raise UpdateError("This folder is not a Git installation. Install a Git copy to use updates.")
    known["source"] = source = repository(git(pack, "config", "--get", "remote.origin.url"))
    known["current"] = current = git(pack, "rev-parse", "HEAD")
    try:
        attached = git(pack, "symbolic-ref", "--quiet", "--short", "HEAD")
    except UpdateError as error:
        raise UpdateError("Updates are paused on a detached checkout. Check out the branch you installed to update.") from error
    branch = resolve(pack, attached, fetch, known)
    if "default_branch" not in known:
        # Informational when a tracked or chosen branch is followed: its absence never blocks an update.
        try:
            known["default_branch"] = default_branch(pack, fetch)
        except UpdateError:
            known["default_branch"] = None
    base = {"source": source, "branch": branch, "following": branch, "default_branch": known["default_branch"],
            "current": current, "available": False, "target": None}
    known.update(base)
    if git(pack, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateError("Local code changes were found. Save or review them before updating; they will be kept.")
    # An interrupted operation must be resolved by its owner, even if its index happens to be clean.
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG"):
        marker_path = Path(git(pack, "rev-parse", "--git-path", marker))
        if not marker_path.is_absolute():
            marker_path = pack / marker_path
        if marker_path.exists():
            raise UpdateError("A Git operation is in progress. Finish it before updating.")
    if not fetch:
        return base
    git(pack, "fetch", "--no-tags", "--no-recurse-submodules", "origin", "+refs/heads/" + branch + ":refs/studio-updates/latest")
    target = git(pack, "rev-parse", "refs/studio-updates/latest")
    if current == target:
        return {**base, "target": target}
    try:
        git(pack, "merge-base", "--is-ancestor", current, target)
    except UpdateError as error:
        raise UpdateError("This installation has its own commits. Updates will keep them; merge them manually first.") from error
    changed = git(pack, "diff", "--name-only", current, target).splitlines()
    if any(name == "requirements.lock" or name.startswith(("scripts/setup-", "oneshot-setup")) for name in changed):
        raise UpdateError("This update also changes installation dependencies. Run the setup instructions for this version first.")
    try:
        version = git(pack, "show", target + ":VERSION", timeout=10)[:80]
    except UpdateError:
        version = target[:8]
    return {**base, "target": target, "available": True, "latest_version": version}


def latest_release(source: str) -> dict | None:
    """The newest GitHub release, for information only: installing is always a fast-forward of the followed branch."""
    request = urllib.request.Request("https://api.github.com/repos/" + quote(source, safe="/") + "/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "Studio-updater"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            found = json.loads(response.read(1_000_000))
        release = {"tag": found["tag_name"], "name": found.get("name") or found["tag_name"],
                   "published_at": found.get("published_at"), "url": found.get("html_url")}
    except Exception:
        return None
    if not all(value is None or isinstance(value, str) for value in release.values()) or not isinstance(release["tag"], str):
        return None
    if release["url"] and not release["url"].startswith("https://github.com/"):
        release["url"] = None
    return {key: value[:200] if isinstance(value, str) else value for key, value in release.items()}


class Manager:
    def __init__(self, pack: Path, home: Path, queue_lock, busy, restart=None):
        self.pack, self.queue_lock, self.busy, self.restart = pack.resolve(), queue_lock, busy, restart
        key = hashlib.sha256(str(self.pack).encode()).hexdigest()[:16]
        self.folder = home / ".studio-updates" / key
        self.settings_path = self.folder / "settings.json"
        self.request_path = self.folder / "request.json"
        self.result_path = self.folder / "result.json"
        self.mutex = threading.RLock()
        self.maintenance = threading.Event()
        self.stop = threading.Event()
        self.info = {"checking": False, "available": False, "checked_at": None, "message": "Check for the latest Studio update."}
        self.checked_monotonic = None
        self.started = False

    def automatic(self) -> bool:
        try:
            return json.loads(self.settings_path.read_text()).get("automatic") is True
        except (OSError, ValueError):
            return False

    def managed(self) -> bool:
        return bool(os.environ.get("STUDIO_UPDATE_SESSION")) and not os.environ.get("STUDIO_PIDFILE")

    def status(self) -> dict:
        with self.mutex:
            result = {**self.info, "automatic": self.automatic(), "updating": self.maintenance.is_set(),
                      "managed": self.managed(), "session": os.environ.get("STUDIO_UPDATE_SESSION", ""), **self.installed()}
            try:
                result["last_result"] = json.loads(self.result_path.read_text())
            except (OSError, ValueError):
                result["last_result"] = None
            return result

    def installed(self) -> dict:
        try:
            version = (self.pack / "VERSION").read_text().strip()[:80] or None
        except OSError:
            version = None
        try:
            commit = git(self.pack, "rev-parse", "--short", "HEAD", timeout=5)
        except UpdateError:
            commit = None
        return {"installed_version": version, "installed_commit": commit}

    def check(self) -> dict:
        with self.mutex:
            if self.info["checking"] or self.maintenance.is_set():
                return self.status()
            self.info.update(checking=True, available=False, message="Checking GitHub…")
        threading.Thread(target=self._check, daemon=True).start()
        return self.status()

    def _check(self):
        try:
            try:
                found = inspect(self.pack, fetch=True)
                found = {**found, "message": "An update is ready." if found["available"] else "Studio is up to date."}
            except UpdateError as error:
                # Keep what was learned (source, followed branch) so the page can say what failed where.
                known = {key: value for key, value in getattr(error, "info", {}).items()
                         if key in ("source", "following", "default_branch", "current")}
                if "following" in known:
                    known["branch"] = known["following"]
                found = {**known, "available": False, "target": None, "message": str(error)}
            release = latest_release(found["source"]) if found.get("source") else None
            if release:
                found["latest_release"] = release
            with self.mutex:
                self.info = found
        finally:
            with self.mutex:
                self.info.update(checking=False, checked_at=time.time())
                self.checked_monotonic = time.monotonic()

    def set_automatic(self, enabled: bool):
        with self.mutex:
            if enabled:
                if not self.managed():
                    raise UpdateError("Open Studio with scripts/app.sh to enable automatic updates. Managed workers update through Sparks.")
                inspect(self.pack)
            write_json(self.settings_path, {"automatic": enabled})
        return self.status()

    def apply(self, automatic: bool = False):
        with self.mutex:
            if self.maintenance.is_set():
                return self.status()
            if self.info.get("checking") or not self.info.get("available"):
                raise UpdateError("Check for updates before installing.")
            if not self.managed() or self.restart is None:
                raise UpdateError("Open Studio with scripts/app.sh to install updates. Managed workers update through Sparks.")
            # The scheduler rechecks the toggle under the same mutex as settings writes.
            if automatic and not self.automatic():
                return self.status()
            with self.queue_lock:
                if self.busy():
                    raise UpdateError("Waiting for Studio jobs and the render engine to finish. Try again when Studio is idle.")
                current = inspect(self.pack)
                if current["current"] != self.info["current"] or current["following"] != self.info.get("following"):
                    raise UpdateError("The installed code changed. Check for updates again.")
                self.maintenance.set()
            try:
                write_json(self.request_path, {"session": os.environ["STUDIO_UPDATE_SESSION"],
                           "current": self.info["current"], "target": self.info["target"],
                           "following": self.info["following"]})
                self.info["message"] = "Installing update. Studio will restart and this page will reconnect."
                # Called after the response can be sent; Uvicorn drains HTTP requests before the launcher changes code.
                timer = threading.Timer(1, self.restart)
                timer.daemon = True
                timer.start()
            except Exception:
                self.maintenance.clear()
                self.request_path.unlink(missing_ok=True)
                raise
            return self.status()

    def start(self):
        if self.started:
            return
        self.started = True

        def loop():
            while not self.stop.wait(30):
                self.tick()
        threading.Thread(target=loop, daemon=True).start()

    def tick(self):
        if not self.automatic() or not self.managed() or self.maintenance.is_set():
            return
        if self.checked_monotonic is None or time.monotonic() - self.checked_monotonic >= INTERVAL:
            self.check()
        if self.status().get("available"):
            try:
                self.apply(automatic=True)
            except UpdateError as error:
                with self.mutex:
                    self.info["message"] = str(error)

    def router(self):
        router = APIRouter(prefix="/api/updates")

        def local(request: Request, mutation: bool = False):
            # Updates execute downloaded code: restrict management to this computer / trusted tailnet proxy.
            host = request.url.hostname or ""
            if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient") or not (
                    host in ("localhost", "127.0.0.1", "::1", "testserver") or host.endswith(".ts.net")):
                raise HTTPException(403, "Manage Studio updates on this computer or through its Tailscale address.")
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
                raise HTTPException(403, "Open the Studio page to manage updates.")
            if mutation and request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise HTTPException(415, "Send updates from the Studio page.")

        @router.get("/health")
        def health():
            return {"session": os.environ.get("STUDIO_UPDATE_SESSION", "")}

        @router.get("")
        def status(request: Request):
            local(request)
            return self.status()

        @router.post("/{action}")
        def change(action: str, body: dict, request: Request):
            local(request, True)
            try:
                if action == "check":
                    return self.check()
                if action == "install":
                    return self.apply()
                if action == "settings" and type(body.get("automatic")) is bool:
                    return self.set_automatic(body["automatic"])
                raise HTTPException(400, "Choose an update action or a true/false automatic setting.")
            except UpdateError as error:
                raise HTTPException(409, str(error)) from error
        return router


def apply_stopped(pack: Path, request: dict) -> tuple[str, str]:
    """Only called by the launcher after its own server child has exited."""
    found = inspect(pack, fetch=True)
    if (found["current"] != request["current"] or found["target"] != request["target"] or not found["available"]
            or found["following"] != request.get("following", found["following"])):
        raise UpdateError("The available update changed. Start Studio and check again.")
    backup = "refs/studio-backups/" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    git(pack, "update-ref", backup, found["current"])
    git(pack, "merge", "--ff-only", "--no-edit", found["target"])
    return found["current"], backup


def rollback(pack: Path, old: str, target: str):
    # Never discard a file changed by somebody while the app was restarting.
    if git(pack, "rev-parse", "HEAD") != target or git(pack, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateError("Automatic recovery paused to preserve local changes. The saved backup ref is available for recovery.")
    git(pack, "reset", "--keep", old)
