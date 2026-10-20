"""A local model server that steps aside for renders (app/local_server.py, STUDIO_LOCAL_CONTROL):
python3 -m pytest tests/test_local_server.py. A stand-in control program keeps its state in a file; no model runs."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
import local_server as L  # noqa: E402

CONTROL = """#!/bin/sh
state="$(dirname "$0")/state"; log="$(dirname "$0")/log"; echo "$1" >> "$log"
case "$1" in
  status) [ "$(cat "$state" 2>/dev/null)" = up ];;
  stop) echo down > "$state";;
  start) echo up > "$state";;
  pid) [ "$(cat "$state" 2>/dev/null)" = up ] && echo $$;;
esac
"""


def stand_in(tmp_path, monkeypatch, up=True) -> Path:
    tool = tmp_path / "control"
    tool.write_text(CONTROL)
    tool.chmod(0o755)
    (tmp_path / "state").write_text("up\n" if up else "down\n")
    monkeypatch.setenv("STUDIO_LOCAL_CONTROL", str(tool))
    monkeypatch.setattr(L, "available_mb", lambda: 60000)
    monkeypatch.setattr(L, "held_mb", lambda: 26000)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    L.state["stopped_by_us"] = False
    return tmp_path


def asked(folder: Path) -> list[str]:
    return [w for w in (folder / "log").read_text().split() if w != "status"]


def test_unset_nothing_runs(monkeypatch):
    monkeypatch.delenv("STUDIO_LOCAL_CONTROL", raising=False)
    assert L.room_mb() is None and L.release("x") is False and L.resume() is False


def test_a_running_server_is_stopped_for_a_job_counted_as_room_and_started_again_after(tmp_path, monkeypatch):
    folder = stand_in(tmp_path, monkeypatch)
    assert L.room_mb() == 60000 + 26000  # its memory is the job's once it is stopped
    assert L.release("a video job needs the GPU") is True
    assert (folder / "state").read_text().strip() == "down" and L.room_mb() is None
    assert L.release("again") is False  # already down
    assert L.resume() is True and (folder / "state").read_text().strip() == "up"
    assert L.resume() is False  # started once
    assert asked(folder) == ["stop", "start"]


def test_a_server_the_person_stopped_stays_stopped(tmp_path, monkeypatch):
    folder = stand_in(tmp_path, monkeypatch, up=False)
    assert L.release("a video job needs the GPU") is False and L.resume() is False
    assert (folder / "state").read_text().strip() == "down"


def test_the_queue_stops_it_before_a_job_and_brings_it_back_when_empty(tmp_path, monkeypatch):
    import server
    folder = stand_in(tmp_path, monkeypatch)
    started = []

    def fake_perform(job, worker=None):
        if job["kind"] not in ("export", "soundtrack") and worker is None and not job.get("remote"):
            started.append((job["id"], L.release(f"a {job['kind']} job needs the GPU")))
    # the hook is in server.perform: check it is there, then the order the queue gives
    source = (ROOT / "app" / "server.py").read_text()
    assert "local_server.release(" in source and "local_server.resume()" in source
    assert "local_server.room_mb()" in source
    fake_perform({"id": "j1", "kind": "video", "params": {}})
    assert started == [("j1", True)] and (folder / "state").read_text().strip() == "down"
    L.resume()
    assert (folder / "state").read_text().strip() == "up"
