"""The launcher on Windows (scripts/app.ps1 runs app/launcher.py, so Settings → Updates installs source updates):
its hold on the checkout, and the PID file it names as its own child's. Runs on every platform; CI runs it on
Windows on Arm as one of the test_windows_* files."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import launcher  # noqa: E402


def test_one_launcher_holds_a_checkout_and_lets_go_when_it_ends(tmp_path):
    lock = tmp_path / "studio-launcher.lock"
    first = lock.open("a+")
    assert launcher.own(first)
    # another process cannot take it while the first holds it
    other = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import launcher; "
                            "sys.exit(0 if launcher.own(open(sys.argv[2], 'a+')) else 3)", str(ROOT / "app"), str(lock)],
                           capture_output=True, text=True, timeout=30)
    assert other.returncode == 3, other.stderr
    first.close()  # the launcher ended
    again = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import launcher; "
                            "sys.exit(0 if launcher.own(open(sys.argv[2], 'a+')) else 3)", str(ROOT / "app"), str(lock)],
                           capture_output=True, text=True, timeout=30)
    assert again.returncode == 0, again.stderr


def test_app_ps1_starts_the_launcher_for_its_own_studio():
    script = (ROOT / "scripts/app.ps1").read_text(encoding="utf-8")
    assert "$OwnPidFile = -not $env:STUDIO_PIDFILE" in script
    assert script.index("$OwnPidFile = -not $env:STUDIO_PIDFILE") < script.index("$env:STUDIO_PIDFILE = Join-Path")
    assert "'app\\launcher.py'" in script and "$Port -eq 7870" in script


def test_the_launcher_names_the_pid_file_it_gives_its_server(monkeypatch):
    source = (ROOT / "app/launcher.py").read_text(encoding="utf-8")
    assert 'env["STUDIO_LAUNCHER_PIDFILE"] = os.environ["STUDIO_PIDFILE"]' in source
    assert "import fcntl" in source and "import msvcrt" in source  # each only on its own platform
