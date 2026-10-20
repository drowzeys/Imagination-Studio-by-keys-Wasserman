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
    assert "local_server.release(" in source and "local_server.resume(free_comfy" in source
    assert "local_server.room_mb()" in source
    fake_perform({"id": "j1", "kind": "video", "params": {}})
    assert started == [("j1", True)] and (folder / "state").read_text().strip() == "down"
    L.resume()
    assert (folder / "state").read_text().strip() == "up"


def test_a_containers_server_is_counted_by_its_whole_process_tree():
    import os
    import pytest
    if not os.path.isdir("/proc"):
        pytest.skip("no /proc on this machine (a Mac): the process tree is read on Linux")
    # this test's own process tree: the Python running it holds no GPU, so its resident size is what is counted
    assert os.getpid() in L.tree(os.getppid()) and L.tree(os.getpid()) >= {os.getpid()}


def test_memory_is_freed_before_the_server_starts_again_and_a_failed_start_is_said(tmp_path, monkeypatch, capsys):
    folder = stand_in(tmp_path, monkeypatch)
    order = []
    assert L.release("a video job needs the GPU")
    real = L.run
    monkeypatch.setattr(L, "run", lambda word, timeout=10: order.append(word) or real(word, timeout))
    assert L.resume(lambda: order.append("free")) is True and order[:2] == ["free", "start"]
    assert "started again" in capsys.readouterr().out
    (tmp_path / "control").write_text("#!/bin/sh\n[ \"$1\" = start ] && { echo 'not enough memory: 40 GiB free' >&2; exit 1; }\n[ \"$1\" = status ] && exit 0\nexit 0\n")
    L.state["stopped_by_us"] = True
    assert L.resume() is False and "not enough memory: 40 GiB free" in capsys.readouterr().out


def test_the_studios_own_port_is_not_passed_to_the_control(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch)
    (tmp_path / "control").write_text('#!/bin/sh\necho "port=${PORT:-unset} host=${HOST:-unset}"\n')
    monkeypatch.setenv("PORT", "7871")
    monkeypatch.setenv("HOST", "127.0.0.1")
    assert L.run("pid").stdout.strip() == "port=unset host=unset"
