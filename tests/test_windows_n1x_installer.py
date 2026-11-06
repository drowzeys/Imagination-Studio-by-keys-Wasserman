"""The RTX Spark N1X one-shot recipe (oneshot-setup-rtx-spark-n1x.ps1): a native Windows install, read from the file;
on Windows the PowerShell is parsed too (the windows-arm64 CI job)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "oneshot-setup-rtx-spark-n1x.ps1"


def test_the_one_shot_covers_install_launchers_pin_power_and_startup():
    text = PS1.read_text()
    for words in ("setup-windows-cuda.ps1", "AutomaticModelSelection", "Start-Studio.cmd", "Stop-Studio.cmd",
                  "TaskbarPinList", 'PinListPlacement="Append"', "standby-timeout-ac 0", "hibernate-timeout-ac 0",
                  "Disable-NetAdapterPowerManagement", "StartAtLogon", "EnableSsh", "administrators_authorized_keys",
                  "HfToken", ".cache\\huggingface\\token"):
        assert words in text, words
    assert "setup-windows-desktop.ps1" in text and "-Backend native" in text
    assert (ROOT / "app/static/brand/app-icon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"  # an .ico


def test_windows_is_native_only():
    """No WSL path is left: the one-shot installs natively, -Native stays an accepted no-op for older command lines,
    and the WSL-side scripts are gone."""
    text = PS1.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    body = code[code.index("[CmdletBinding()]"):]
    for word in ("wsl.exe", ".wslconfig", "WSLENV", "-Backend wsl", "$Distro", "$WslUser", "systemd=true"):
        assert word not in body, word
    assert "[switch]$Native" in body
    assert not (ROOT / "oneshot-setup-rtx-spark-n1x.sh").exists()
    assert not (ROOT / "scripts/n1x-studio.sh").exists()


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="PowerShell's parser is on Windows")
def test_the_powershell_parses():
    check = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e); "
             "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }" % PS1)
    done = subprocess.run(["powershell", "-NoProfile", "-Command", check], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
