"""Where the studio keeps everything (projects, uploads, renders, voice, music, LoRAs, settings): STUDIO_HOME when it is
set, else ~/ImaginationStudio, the folder named after the studio.

An install from before the name keeps its things in ~/TensorFoldStudio. The server, when it is started (not when a
script or a test imports it), and the launcher move that folder once to ~/ImaginationStudio, a rename on the same disk, and leave ~/TensorFoldStudio as a link to it, so a script, a
Spark worker or a bookmark that still names the old folder finds the same files. Nothing is copied or deleted; when
the move cannot be made (another disk, no permission) the old folder is used as it is."""
from __future__ import annotations

import os
from pathlib import Path

NAME = "ImaginationStudio"
OLD = "TensorFoldStudio"


def studio_home(move: bool = False) -> Path:
    named = os.environ.get("STUDIO_HOME", "").strip()
    if named:
        return Path(named).expanduser()
    new, old = Path.home() / NAME, Path.home() / OLD
    if new.exists():
        return new
    if old.is_dir() and not old.is_symlink():
        if not move:
            return old
        try:
            old.rename(new)
        except OSError:
            return old
        try:
            old.symlink_to(new, target_is_directory=True)
        except OSError:
            pass
        return new
    return new
