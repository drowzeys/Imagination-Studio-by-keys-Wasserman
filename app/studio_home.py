"""Where the studio keeps everything (projects, uploads, renders, voice, music, LoRAs, settings): STUDIO_HOME when it is
set, else ~/ImaginationStudio, the folder named after the studio.

An install from before the name keeps its things in ~/TensorFoldStudio. The server, when it is started (not when a
script or a test imports it), and the launcher move that folder once to ~/ImaginationStudio, a rename on the same disk, and leave ~/TensorFoldStudio as a link to it, so a script, a
Spark worker or a bookmark that still names the old folder finds the same files. Nothing is copied or deleted. The old
folder is used as it is, and nothing moves, when:
- it is itself a link (to a larger disk or a share): that place is the home;
- a studio server is running (an older one may be using the folder);
- the move or the link cannot be made (another disk, no permission): said on stderr.

Every part of the pack asks here: the app (server.py, launcher.py, discover.py, loras.py), the scripts
(lora-bundle.py imports it, the shell scripts run ``python3 app/studio_home.py``, which prints the folder)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NAME = "ImaginationStudio"
OLD = "TensorFoldStudio"


def say(text: str) -> None:
    print("[studio] " + text, file=sys.stderr, flush=True)


def server_running() -> bool:
    """Another studio server on this machine (an older version may have the old folder open)."""

    try:
        found = subprocess.run(["pgrep", "-f", "app/server.py"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    mine = {os.getpid(), os.getppid()}
    return any(pid.isdigit() and int(pid) not in mine for pid in found.stdout.split())


def studio_home(move: bool = False, base: Path | None = None) -> Path:
    named = os.environ.get("STUDIO_HOME", "").strip()
    if named:
        return Path(named).expanduser()
    base = base or Path.home()
    new, old = base / NAME, base / OLD
    if new.exists():
        return new
    if old.is_symlink():
        return old if old.is_dir() else new  # a link to the person's own place, unless it points nowhere
    if not old.is_dir():
        return new
    if not move:
        return old
    if server_running():
        say(f"a studio server is running: {old} is used where it is, and moved to {new} at a start when none is")
        return old
    try:
        old.rename(new)
    except OSError as error:
        say(f"{old} could not be moved to {new} ({error.strerror or error}): it is used where it is")
        return old
    try:
        old.symlink_to(new, target_is_directory=True)
    except OSError as error:
        say(f"moved {old} to {new}; the link left behind could not be made ({error.strerror or error})")
    return new


if __name__ == "__main__":
    print(studio_home())
