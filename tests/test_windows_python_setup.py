"""Exercise prerequisite setup with a fake launcher, never downloading Python."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not SHELL, reason="PowerShell is required")


def run_setup(tmp_path, *, installed=False, failure="", existing=False):
    runtime = tmp_path / "runtime"
    if existing:
        executable = runtime / "Scripts" / "python.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
    script = tmp_path / "setup.ps1"
    helper = str(ROOT / "scripts" / "windows-python.ps1").replace("'", "''")
    target = str(runtime).replace("'", "''")
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
. '{helper}'
$script:Calls = [System.Collections.Generic.List[string]]::new()
function global:py {{
    $script:Calls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args[1] -eq '-c' -and ${str(not installed).lower()}) {{
        $global:LASTEXITCODE = 1
    }}
    if ('{failure}' -eq 'install' -and $args[0] -eq 'install') {{
        $global:LASTEXITCODE = 1
    }}
    if ('{failure}' -eq 'venv' -and $args[1] -eq '-m') {{
        $global:LASTEXITCODE = 1
    }}
}}
$Failure = ''
try {{ Initialize-StudioPython -Runtime '{target}' -Arm64 }}
catch {{ $Failure = $_.Exception.Message }}
@{{calls = @($script:Calls.ToArray()); error = $Failure}} | ConvertTo-Json -Compress
""", encoding="utf-8")
    done = subprocess.run([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout.splitlines()[-1])


def test_missing_arm64_python_is_installed_before_creating_environment(tmp_path):
    result = run_setup(tmp_path)
    assert not result["error"]
    assert result["calls"][0].startswith("-V:3.13-arm64 -c ")
    assert result["calls"][1] == "install 3.13-arm64"
    assert result["calls"][2].startswith("-V:3.13-arm64 -m venv ")


def test_existing_python_is_reused_and_existing_environment_is_untouched(tmp_path):
    result = run_setup(tmp_path, installed=True)
    assert not result["error"] and len(result["calls"]) == 2
    assert all(not call.startswith("install ") for call in result["calls"])
    result = run_setup(tmp_path, existing=True)
    assert not result["error"] and result["calls"] == []


@pytest.mark.parametrize("failure, message", [
    ("install", "installation failed"),
    ("venv", "Could not create"),
])
def test_prerequisite_failure_is_explicit_and_does_not_continue(tmp_path, failure, message):
    result = run_setup(tmp_path, failure=failure)
    assert message in result["error"]
    if failure == "install":
        assert not any("-m venv" in call for call in result["calls"])


def test_install_only_requires_explicit_install(tmp_path):
    done = subprocess.run([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "app.ps1"), "-InstallOnly"],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode != 0 and "-InstallOnly requires -Install" in done.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows report script")
def test_capacity_report_refuses_overwrite_before_probing_hardware(tmp_path):
    report = tmp_path / "capacity.json"
    report.write_text("existing report", encoding="utf-8")
    done = subprocess.run([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "check-windows-capacity.ps1"), "-Output", str(report)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode != 0 and "report already exists" in done.stderr
    assert "tiny CUDA" not in done.stdout
    assert report.read_text(encoding="utf-8") == "existing report"
