"""Own one Studio child. Change source only after it exits, then verify startup and recover on failure."""
from __future__ import annotations

import json
import fcntl
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from studio_home import studio_home
from updates import Manager, UpdateError, apply_stopped, rollback, write_json, git


def main() -> int:
    pack = Path(__file__).resolve().parent.parent
    home = studio_home(move=True)
    manager = Manager(pack, home, None, lambda: False)
    # Lock the checkout across launchers, even when two installations use different Studio homes or ports.
    lock_path = Path(git(pack, "rev-parse", "--git-path", "studio-launcher.lock"))
    if not lock_path.is_absolute():
        lock_path = pack / lock_path
    ownership = lock_path.open("a")
    try:
        fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("This checkout already has a running Studio. Use a separate checkout for another instance.", file=sys.stderr)
        return 1
    stopping = False
    child = None

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def launch():
        session = uuid.uuid4().hex
        return subprocess.Popen([sys.executable, str(pack / "app/server.py")],
                                env={**os.environ, "STUDIO_UPDATE_SESSION": session}), session

    def healthy(session: str) -> bool:
        opener = build_opener(ProxyHandler({}))
        url = "http://127.0.0.1:" + os.environ.get("PORT", "7870") + "/api/updates/health"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not stopping and child.poll() is None:
            try:
                with opener.open(url, timeout=2) as response:
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
        child, session = launch()
        if healthy(session):
            write_json(manager.result_path, {"ok": True, "message": "Studio updated successfully.", "backup": backup, "time": time.time()})
            continue
        retire()
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
