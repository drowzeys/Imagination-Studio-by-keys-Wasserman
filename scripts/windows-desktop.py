#!/usr/bin/env python3
"""Windows desktop backend registrations and identity-checked Native/WSL lifecycle. No model setup."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
BASE = "http://127.0.0.1:7870"
NO_WINDOW = 0x08000000


class DesktopError(RuntimeError):
    pass


def home() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "ImaginationStudio"


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise DesktopError("Desktop settings are not a JSON object.")
    return value


def write(path: Path, value: dict, private: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        # Create with private permissions before writing credentials.
        with temporary.open("x", encoding="utf-8") as handle:
            if private:
                import access
                access.windows_private(temporary)
            json.dump(value, handle)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def locked(name="desktop.lock"):
    import msvcrt
    path = home() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise DesktopError("Another desktop operation is in progress. Try again when it finishes.") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def profile(backend: str, *, studio: str = "", distro: str = "", user: str = "", repo: str = "") -> dict:
    if backend == "native":
        path = Path(studio).resolve()
        if not studio or not (path / "scripts" / "app.ps1").is_file() or not (path / "app" / "desktop_control.py").is_file():
            raise DesktopError("Choose an updated native Studio checkout containing the desktop controller.")
        return {"studio": str(path)}
    if backend != "wsl" or not distro or distro.startswith("-") or any(c in distro for c in "\r\n\0"):
        raise DesktopError("Choose the installed WSL distribution.")
    if not user or user.startswith("-") or any(c in user for c in "\r\n\0"):
        raise DesktopError("Specify the WSL login user.")
    if not repo.startswith("/") or any(c in repo for c in "\r\n\0") or ".." in PurePosixPath(repo).parts:
        raise DesktopError("Specify the absolute Linux Studio checkout path.")
    return {"distro": distro, "user": user, "repo": repo.rstrip("/")}


def register(backend: str, **kwargs):
    found = profile(backend, **kwargs)
    if backend == "wsl":
        verify_wsl(found)
        found["verified"] = True
    data = read(home() / "desktop.json")
    registrations = data.setdefault("backends", {})
    registrations[backend] = found
    if data.get("default") not in registrations:
        data["default"] = backend
    write(home() / "desktop.json", data)
    return data


def request(path: str, identity: dict | None = None, *, stop: bool = False) -> dict:
    headers = {"x-studio-desktop": identity["token"]} if identity else {}
    req = urllib.request.Request(BASE + path, data=b"{}" if stop else None, headers=headers,
                                 method="POST" if stop else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=120 if stop else 3) as response:
        raw = response.read((1 << 20) + 1)
    if len(raw) > 1 << 20:
        raise DesktopError("Desktop status response exceeds its limit.")
    return json.loads(raw)


def owned_status(identity: dict) -> dict:
    found = request("/api/desktop/session", identity)
    if found.get("session") != identity.get("session") or found.get("backend") != identity.get("backend"):
        raise DesktopError("The answering app belongs to another launch. It was not stopped.")
    return found


def available(backend: str, found: dict) -> bool:
    if backend == "native":
        return ((Path(found["studio"]) / "app" / "desktop_control.py").is_file()
                and (home() / "ui-venv" / "Scripts" / "python.exe").is_file())
    return found.get("verified") is True


def verify_wsl(found: dict):
    script = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.exit(0 if (p/'app'/'desktop_control.py').is_file() and (p/'scripts'/'n1x-studio.sh').is_file() else 1)"
    done = subprocess.run(["wsl.exe", "-d", found["distro"], "-u", found["user"], "--exec",
                           "python3", "-c", script, found["repo"]],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=30, creationflags=NO_WINDOW)
    if done.returncode:
        raise DesktopError("The registered WSL checkout is unavailable or needs the updated desktop-control files.")


def status() -> dict:
    config = read(home() / "desktop.json")
    identity = read(home() / "desktop-session.json")
    running = None
    detail = ""
    try:
        with socket.create_connection(("127.0.0.1", 7870), timeout=1):
            pass
    except OSError:
        if identity:
            if supervisor_alive(identity):
                running = identity["backend"]
                detail = "Starting or draining; wait before switching."
    else:
        if identity:
            try:
                found = owned_status(identity)
                running = identity["backend"]
                detail = "Jobs active" if found["busy"] else "Stopping" if found["stopping"] else "Ready"
            except (urllib.error.URLError, TimeoutError, DesktopError):
                running, detail = "external", "Started outside this tray; stop it from its own launcher before switching."
        else:
            running, detail = "external", "Started outside this tray; stop it from its own launcher before switching."
    registrations = config.get("backends", {})
    return {"default": config.get("default"), "running": running, "detail": detail,
            "backends": {kind: {"registered": kind in registrations,
                                "available": available(kind, registrations[kind]) if kind in registrations else False}
                         for kind in ("native", "wsl")}}


def supervisor_alive(identity: dict) -> bool:
    from windows_backend_handle import creation_time, kernel
    api = kernel()
    handle = api.OpenProcess(0x1000 | 0x100000, False, identity["supervisor"])
    if not handle:
        return False
    try:
        return creation_time(handle) == identity.get("created") and api.WaitForSingleObject(handle, 0) == 258
    finally:
        api.CloseHandle(handle)


def stop_backend():
    identity = read(home() / "desktop-session.json")
    found = status()
    if found["running"] is None:
        return
    if found["running"] == "external" or not identity:
        raise DesktopError("This app was started outside the tray. Stop it with its own launcher first.")
    current = owned_status(identity)
    if current["busy"]:
        raise DesktopError("Wait for queued and running jobs before stopping or switching.")
    try:
        request("/api/desktop/stop", identity, stop=True)
    except urllib.error.HTTPError as error:
        if error.code == 409:
            raise DesktopError("New work arrived. Wait for the queue to finish before switching.") from None
        raise
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if not (home() / "desktop-session.json").exists():
            return
        time.sleep(0.5)
    raise DesktopError("The owned app has not finished draining. It was not force-killed; switching is blocked.")


def launch(backend: str):
    config = read(home() / "desktop.json")
    chosen = config.get("backends", {}).get(backend)
    if not chosen or not available(backend, chosen):
        raise DesktopError("That backend is not registered, installed, or updated for tray control.")
    current = status()
    if current["running"] == backend and current["detail"] == "Ready":
        return
    if current["running"] is not None:
        raise DesktopError("Stop the current backend before starting another.")
    if backend == "wsl":
        verify_wsl(chosen)
    python = home() / "ui-venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)
    launch_id = uuid.uuid4().hex
    with (home() / "desktop-supervisor.log").open("a", encoding="utf-8") as log:
        supervisor = subprocess.Popen([str(python), "-X", "utf8", str(Path(__file__).resolve()),
                                       "supervise", "--backend", backend], stdin=subprocess.DEVNULL,
                                      stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW | 0x200,
                                      env={**os.environ, "STUDIO_DESKTOP_LAUNCH": launch_id})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if supervisor.poll() is not None:
            raise DesktopError("Desktop startup failed. See desktop-supervisor.log in the local Studio runtime folder.")
        identity = read(home() / "desktop-session.json")
        # Windows venv python.exe is a trampoline; the actual supervisor PID differs from Popen.pid.
        if identity.get("session") == launch_id:
            try:
                owned_status(identity)
                return
            except (urllib.error.URLError, TimeoutError):
                pass
        time.sleep(0.5)
    raise DesktopError("Startup is still pending. No second backend was started; check the tray status and desktop log.")


def launch_command(backend: str, found: dict, identity: dict) -> tuple[list[str], dict, str | None]:
    env = {**os.environ, "STUDIO_DESKTOP_TOKEN": identity["token"],
           "STUDIO_DESKTOP_SESSION": identity["session"], "STUDIO_DESKTOP_BACKEND": backend}
    if backend == "native":
        return (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(Path(found["studio"]) / "scripts" / "app.ps1"), "-Port", "7870"], env, None)
    # Secrets go over stdin, never WSLENV, argv, a URL, or the Linux checkout.
    script = """import json,os,sys
c=json.loads(sys.stdin.readline())
os.environ.update(c['env'])
os.chdir(c['repo'])
os.execvp('bash',['bash','scripts/n1x-studio.sh'])
"""
    data = json.dumps({"repo": found["repo"], "env": {
        "STUDIO_DESKTOP_TOKEN": identity["token"], "STUDIO_DESKTOP_SESSION": identity["session"],
        "STUDIO_DESKTOP_BACKEND": "wsl", "HOST": "127.0.0.1", "PORT": "7870",
        "STUDIO_PIDFILE": "/tmp/studio-desktop-" + identity["session"] + ".pid"}})
    return ["wsl.exe", "-d", found["distro"], "-u", found["user"], "--exec", "python3", "-c", script], env, data


def supervise(backend: str):
    with locked("desktop-supervisor.lock"):
        supervise_owned(backend)


def supervise_owned(backend: str):
    from windows_process import OwnedJob
    from windows_backend_handle import identity as process_identity
    created = process_identity()["created"]
    if status()["running"] is not None:
        raise DesktopError("Another app or launch is already active. No second backend was started.")
    identity = {"token": secrets.token_hex(32), "session": os.environ.get("STUDIO_DESKTOP_LAUNCH") or uuid.uuid4().hex,
                "backend": backend, "supervisor": os.getpid(), "created": created}
    config = read(home() / "desktop.json")
    found = config["backends"][backend]
    command, env, data = launch_command(backend, found, identity)
    owned = OwnedJob(breakaway=True)
    try:
        write(home() / "desktop-session.json", identity, private=True)
        with (home() / ("desktop-" + backend + ".log")).open("a", encoding="utf-8") as log:
            process = owned.spawn(command, env=env, stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  creationflags=NO_WINDOW | 0x200)
            if data:
                process.stdin.write(data + "\n")
                process.stdin.close()
            code = process.wait()
        if code:
            raise DesktopError("The desktop backend exited unsuccessfully; see its local desktop log.")
    finally:
        owned.close()
        current = read(home() / "desktop-session.json")
        if current.get("session") == identity["session"]:
            (home() / "desktop-session.json").unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "status", "start", "stop", "switch", "default", "supervise"))
    parser.add_argument("--backend", choices=("native", "wsl"))
    parser.add_argument("--studio", default="")
    parser.add_argument("--distro", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = status()
        elif args.action == "supervise":
            supervise(args.backend)
            return 0
        else:
            with locked():
                if args.action == "register":
                    if not args.backend:
                        raise DesktopError("Choose Native or WSL.")
                    register(args.backend, studio=args.studio, distro=args.distro, user=args.user, repo=args.repo)
                elif args.action == "default":
                    config = read(home() / "desktop.json")
                    if args.backend not in config.get("backends", {}):
                        raise DesktopError("Register that backend before choosing it as default.")
                    config["default"] = args.backend
                    write(home() / "desktop.json", config)
                else:
                    if args.action in ("switch", "stop"):
                        if not args.confirmed:
                            raise DesktopError("Confirm stop/switch in the tray before proceeding.")
                        stop_backend()
                    if args.action in ("start", "switch"):
                        current = status()
                        if args.action == "switch" or args.backend or not current["running"]:
                            launch(args.backend or read(home() / "desktop.json").get("default"))
                result = status()
        print(json.dumps({"ok": True, **result}))
        return 0
    except (OSError, ValueError, KeyError, DesktopError, subprocess.SubprocessError) as error:
        # Never stringify HTTP requests/headers or environment, which hold private launch credentials.
        message = ("Desktop request was refused (HTTP " + str(error.code) + ")."
                   if isinstance(error, urllib.error.HTTPError) else str(error))
        print(json.dumps({"ok": False, "error": message}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
