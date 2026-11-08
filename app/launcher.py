"""Own one Studio child. Change source only after it exits, then verify startup and recover on failure."""
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

import access
from studio_home import studio_home
from updates import Manager, UpdateError, apply_stopped, engine_stale, prefix_of, rollback, sync_prefix, write_json, git


def keyed(opener, home: Path | None = None):
    """The opener with the app's access key (app/access.py), read now: a server started a moment ago makes it."""

    opener.addheaders = [(name, value) for name, value in access.headers_for(home or studio_home()).items()]
    return opener


def idle(opener, port: str) -> bool:
    keyed(opener)
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/jobs", timeout=5) as response:
            listing = json.load(response)
    except (OSError, ValueError):
        return False
    jobs = listing if isinstance(listing, list) else listing.get("jobs", [])
    return not any(job.get("status") in ("queued", "running") for job in jobs)


def rebuild_engine(pack: Path, pin: str, port: str) -> None:
    """A Mac's native FastH3 engine, built at the commit the update pins (scripts/build-zig-engine.sh, needs zig and
    the Metal toolchain) and put in PREFIX when the Studio is idle; the engine it replaces is kept as
    zig-engine.before-update. A failed build leaves the installed engine as it is and says so in the log."""

    done = subprocess.run(["bash", str(pack / "scripts/build-zig-engine.sh")], cwd=pack, capture_output=True, text=True, timeout=3600)
    built = pack / "payload/zig-engine"
    if done.returncode or (built / "COMMIT").read_text().strip() != pin:
        print("[launcher] the native engine was not rebuilt: " + (done.stderr or done.stdout)[-300:], file=sys.stderr, flush=True)
        return
    opener = build_opener(ProxyHandler({}))
    while not idle(opener, port):  # never under a render that is using it
        time.sleep(10)
    engine, kept = prefix_of() / "zig-engine", prefix_of() / "zig-engine.before-update"
    shutil.rmtree(kept, ignore_errors=True)
    staged = prefix_of() / ("zig-engine." + uuid.uuid4().hex)
    shutil.copytree(built, staged)
    # h3_case.py imports the renderer as h3_generate_dev from its own folder: oneshot-setup.sh links it there, the build does not
    (staged / "h3_generate_dev.py").unlink(missing_ok=True)
    (staged / "h3_generate_dev.py").symlink_to("../h3_generate.py")
    engine.rename(kept)
    staged.rename(engine)
    print(f"[launcher] native engine rebuilt at {pin[:8]}", file=sys.stderr, flush=True)


def own(handle) -> bool:
    """This launcher's hold on the checkout: a lock the system lets go of when the process ends, however it ends.
    Windows has no flock; msvcrt locks the file's first byte, which LK_NBLCK refuses at once when another holds it."""

    if sys.platform == "win32":
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def main() -> int:
    pack = Path(__file__).resolve().parent.parent
    home = studio_home(move=True)
    manager = Manager(pack, home, None, lambda: False)
    # Lock the checkout across launchers, even when two installations use different Studio homes or ports.
    lock_path = Path(git(pack, "rev-parse", "--git-path", "studio-launcher.lock"))
    if not lock_path.is_absolute():
        lock_path = pack / lock_path
    ownership = lock_path.open("a+")
    if not own(ownership):
        print("This checkout already has a running Studio. Use a separate checkout for another instance.", file=sys.stderr)
        return 1
    stopping = False
    child = None

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        # Ctrl-C already reaches the server: it shares this terminal's process group. Sending it
        # again is a second SIGINT, which the server treats as a forced exit and a traceback.
        if signum == signal.SIGINT or child is None or child.poll() is not None:
            return
        child.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def launch():
        session = uuid.uuid4().hex
        env = {**os.environ, "STUDIO_UPDATE_SESSION": session}
        # Windows (scripts/app.ps1) gives the server a PID file for its stop script and tray. A PID file otherwise
        # marks a worker that must not update itself; this one is named as the launcher's own child's.
        if os.environ.get("STUDIO_PIDFILE"):
            env["STUDIO_LAUNCHER_PIDFILE"] = os.environ["STUDIO_PIDFILE"]
        return subprocess.Popen([sys.executable, str(pack / "app/server.py")], env=env), session

    def healthy(session: str) -> bool:
        opener = build_opener(ProxyHandler({}))
        url = "http://127.0.0.1:" + os.environ.get("PORT", "7870") + "/api/updates/health"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not stopping and child.poll() is None:
            try:
                with keyed(opener, home).open(url, timeout=2) as response:
                    if json.load(response).get("session") == session:
                        return True
            except (OSError, ValueError):
                pass
            time.sleep(0.3)
        return False

    def retire():
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()  # our unresponsive server child only; never a renderer or unrelated process
                child.wait(timeout=5)

    try:  # copies a `git pull` left behind follow the code before the first render (the updater only syncs its own updates)
        refreshed = sync_prefix(pack, "HEAD")
    except OSError as error:
        refreshed = []
        print("PREFIX copies were not checked: " + str(error), file=sys.stderr)
    if refreshed:
        print("Refreshed in PREFIX: " + ", ".join(refreshed), file=sys.stderr)
    child, session = launch()
    # A worker's PID contract and start/stop commands depend on server.py being the managed process.
    while True:
        code = child.wait()
        if stopping:
            return code
        try:
            request = json.loads(manager.request_path.read_text())
        except (OSError, ValueError):
            return code
        manager.request_path.unlink(missing_ok=True)
        if request.get("session") != session or code != 0:
            return code
        try:
            old, backup = apply_stopped(pack, request)
        except UpdateError as error:
            write_json(manager.result_path, {"ok": False, "message": str(error), "time": time.time()})
            child, session = launch()
            continue
        refreshed = sync_prefix(pack, old)  # the renderers' copies in PREFIX follow the code (updates.PREFIX_COPIES)
        child, session = launch()
        if healthy(session):
            pin = engine_stale(pack) if sys.platform == "darwin" and shutil.which("zig", path="/opt/homebrew/bin:" + os.environ.get("PATH", "")) else None
            if pin:
                threading.Thread(target=rebuild_engine, args=(pack, pin, os.environ.get("PORT", "7870")), daemon=True).start()
            write_json(manager.result_path, {"ok": True, "message": "Studio updated successfully." + (" Refreshed in PREFIX: " + ", ".join(refreshed) + "." if refreshed else "")
                                             + (f" The native engine is being rebuilt at {pin[:8]}; it is swapped in when Studio is idle." if pin else ""),
                                             "backup": backup, "time": time.time()})
            continue
        retire()
        for name in refreshed:  # the update is undone: so are its PREFIX copies
            kept = prefix_of() / (name + ".before-update")
            if kept.is_file():
                kept.replace(prefix_of() / name)
        try:
            rollback(pack, old, request["target"])
        except UpdateError as error:
            write_json(manager.result_path, {"ok": False, "message": str(error), "backup": backup, "time": time.time()})
            return 1
        write_json(manager.settings_path, {"automatic": False})
        write_json(manager.result_path, {"ok": False, "message": "The update could not start. Your previous version was restored and automatic updates were turned off.", "backup": backup, "time": time.time()})
        if stopping:
            return 0
        child, session = launch()


if __name__ == "__main__":
    raise SystemExit(main())
