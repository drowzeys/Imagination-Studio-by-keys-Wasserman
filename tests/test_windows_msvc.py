"""Compiler detection/installation flow, with the installer mocked: no compiler is downloaded by tests."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not SHELL, reason="PowerShell is required")


@pytest.mark.parametrize("existing,verify,fails,installs,error", [
    (True, False, False, 0, ""),
    (False, False, False, 1, ""),
    (False, True, False, 0, "verification did not change"),
    (False, False, True, 1, "still missing"),
])
def test_compiler_is_detected_before_installing_and_verification_is_read_only(tmp_path, existing, verify, fails, installs, error):
    helper = str(ROOT / "scripts" / "windows-msvc.ps1").replace("'", "''")
    script = tmp_path / "compiler.ps1"
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
. '{helper}'
$script:Present = ${str(existing).lower()}
$script:Installs = 0
function Find-StudioArm64Compiler {{
    if ($script:Present) {{ return 'C:\\BuildTools\\vcvarsall.bat' }}
    return ''
}}
function Install-StudioArm64Compiler {{
    $script:Installs++
    $script:Present = ${str(not fails).lower()}
}}
$Result = ''; $Failure = ''
try {{ $Result = Initialize-StudioArm64Compiler -Verify:${str(verify).lower()} }}
catch {{ $Failure = $_.Exception.Message }}
@{{result=$Result; installs=$script:Installs; error=$Failure}} | ConvertTo-Json -Compress
""", encoding="utf-8")
    done = subprocess.run([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True, timeout=30, check=True)
    found = json.loads(done.stdout.splitlines()[-1])
    assert found["installs"] == installs
    if error:
        assert error in found["error"] and not found["result"]
    else:
        assert not found["error"] and found["result"].endswith("vcvarsall.bat")
