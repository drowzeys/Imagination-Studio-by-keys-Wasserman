"""`bash scripts/app.sh stop` (app/launcher.py stop): the Studio of this checkout is ended through the launcher that
holds its lock, a launcher paused with Ctrl+Z included, and nothing else is signalled."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import launcher  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows stops the Studio with its own script")

# a launcher's stand-in: takes the lock as the launcher does, leaves its record, and ends on SIGTERM (or never, "deaf")
STAND_IN = """
import signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import launcher
lock = Path(sys.argv[2])
handle = lock.open("a+")
assert launcher.own(handle)
if "record" in sys.argv:
    launcher.note(lock)
signal.signal(signal.SIGTERM, signal.SIG_IGN if "deaf" in sys.argv else (lambda *_: sys.exit(0)))
print("READY", flush=True)
time.sleep(120)
"""


def stand_in(tmp_path, *words):
    script = tmp_path / "launcher.py"  # named as the launcher is: the stop checks what it signals
    script.write_text(STAND_IN)
    lock = tmp_path / "studio-launcher.lock"
    process = subprocess.Popen([sys.executable, str(script), str(ROOT / "app"), str(lock), *words], stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "READY"
    return process, lock


def state(pid):
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1]


def test_with_no_studio_running_there_is_nothing_to_stop(tmp_path, capsys):
    assert launcher.stop_running(tmp_path / "studio-launcher.lock") == 0
    assert "not running" in capsys.readouterr().out


def test_a_launcher_paused_with_ctrl_z_is_resumed_and_ended_and_the_lock_is_free(tmp_path, capsys):
    process, lock = stand_in(tmp_path, "record")
    try:
        os.kill(process.pid, signal.SIGSTOP)  # as Ctrl+Z leaves it
        for _ in range(50):
            if state(process.pid) == "T":
                break
            time.sleep(0.05)
        assert state(process.pid) == "T"
        os.kill(process.pid, signal.SIGTERM)  # a plain kill: it waits, and the lock stays held
        time.sleep(0.3)
        assert process.poll() is None and launcher.holder(lock) == process.pid
        assert launcher.stop_running(lock, wait=10) == 0
        assert process.wait(timeout=5) == 0 and "has stopped" in capsys.readouterr().out
        with lock.open("a+") as handle:
            assert launcher.own(handle)  # the next start takes it
    finally:
        process.kill()


def test_a_launcher_without_a_record_is_found_by_the_lock_it_holds(tmp_path):
    if not Path("/proc/locks").exists():
        pytest.skip("no /proc/locks on this system")
    process, lock = stand_in(tmp_path)
    try:
        assert not launcher.record_of(lock).exists() and launcher.holder(lock) == process.pid
        assert launcher.stop_running(lock, wait=10) == 0 and process.wait(timeout=5) == 0
    finally:
        process.kill()


def test_a_record_that_names_another_process_is_not_signalled(tmp_path):
    process, lock = stand_in(tmp_path, "record")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        # the record's PID has gone to another program since (its start time differs, and it is no launcher)
        launcher.write_json(launcher.record_of(lock), {"pid": other.pid, "started": "Mon Jan  1 00:00:00 2001"})
        found = launcher.holder(lock)
        assert found != other.pid and found in (None, process.pid)
        launcher.stop_running(lock, wait=3)
        assert other.poll() is None  # untouched
    finally:
        other.kill()
        process.kill()


def test_a_launcher_that_does_not_leave_is_ended_only_with_force(tmp_path, capsys):
    process, lock = stand_in(tmp_path, "record", "deaf")
    try:
        assert launcher.stop_running(lock, wait=1.5) == 1
        assert "stop --force" in capsys.readouterr().err and process.poll() is None
        assert launcher.stop_running(lock, force=True, wait=1) == 0 and process.wait(timeout=5) == -signal.SIGKILL
    finally:
        process.kill()


def test_app_sh_takes_stop_and_restart_and_the_launcher_ignores_ctrl_z():
    script = (ROOT / "scripts/app.sh").read_text()
    assert 'launcher.py" stop "${@:2}"' in script and "restart)" in script and "usage: bash scripts/app.sh" in script
    source = (ROOT / "app/launcher.py").read_text()
    assert "signal.signal(signal.SIGTSTP, signal.SIG_IGN)" in source
