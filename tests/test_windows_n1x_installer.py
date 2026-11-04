"""The RTX Spark N1X one-shot recipe (oneshot-setup-rtx-spark-n1x.ps1 and scripts/n1x-studio.sh): what it sets up, read
from the files; on Windows the PowerShell is parsed too (the windows-arm64 CI job)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "oneshot-setup-rtx-spark-n1x.ps1"


def test_the_one_shot_covers_install_launchers_pin_power_and_startup():
    text = PS1.read_text()
    for words in ("oneshot-setup-rtx-spark-n1x.sh", '"--all"', "--upgrade", "systemd=true", ".wslconfig",
                  "Start-Studio.cmd", "Stop-Studio.cmd", "app-icon.ico", "TaskbarPinList", 'PinListPlacement="Append"',
                  "standby-timeout-ac 0", "hibernate-timeout-ac 0", "Disable-NetAdapterPowerManagement",
                  "StartAtLogon", "EnableSsh", "administrators_authorized_keys", "HfToken", "WSLENV", "huggingface/token"):
        assert words in text, words
    assert "scripts/n1x-studio.sh" in text and "scripts/n1x-studio.sh stop" in text
    assert (ROOT / "app/static/brand/app-icon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"  # an .ico


def test_the_wsl_side_starts_with_the_n1x_guard_and_stops_without_killing_itself():
    text = (ROOT / "scripts/n1x-studio.sh").read_text()
    assert "export STUDIO_N1X=1" in text and "scripts/app.sh" in text and "comfy.sh\" stop" in text
    assert '"[a]pp/server.py"' in text and '"[a]pp/launcher.py"' in text  # its own command line never matches
    done = subprocess.run(["bash", "-n", str(ROOT / "scripts/n1x-studio.sh")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="PowerShell's parser is on Windows")
def test_the_powershell_parses():
    check = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e); "
             "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }" % PS1)
    done = subprocess.run(["powershell", "-NoProfile", "-Command", check], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
