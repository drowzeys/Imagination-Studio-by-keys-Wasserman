"""Model-free PowerShell bootstrap tests; no real package manager or compiler runs."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not SHELL, reason="PowerShell is required")


def run_setup(body):
    source = str(ROOT / "scripts" / "setup-windows-cuda.ps1").replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$Tokens=$null; $Errors=$null
$Tree=[System.Management.Automation.Language.Parser]::ParseFile('{source}', [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) {{ throw ($Errors | Out-String) }}
$Names=@('Get-StudioZigVersion','Initialize-StudioZig','Install-StudioPackage')
$Tree.FindAll({{param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -in $Names}}, $true) |
  ForEach-Object {{ Invoke-Expression $_.Extent.Text }}
function Ok($Text) {{ Write-Output $Text }}
function Refresh-StudioToolPath {{ $global:Refreshed=$true }}
$global:LASTEXITCODE=0
{body}
"""
    return subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=30)


def test_missing_zig_installs_refreshes_and_verifies_in_same_terminal():
    done = run_setup("""
function Get-Command($Name) {
  if ($Name -eq 'winget.exe') { return 'mock-winget' }
  if ($global:Installed) { return 'MockZig' }
}
function MockZig { $global:LASTEXITCODE=0; '0.17.0' }
function Install-StudioPackage($Id, $More) {
  if ($Id -ne 'zig.zig' -or ($More -join ' ') -ne '--version 0.17.0') { throw 'Wrong package/pin' }
  $global:Installed=$true
}
Initialize-StudioZig
if (-not $global:Refreshed) { throw 'PATH was not refreshed' }
""")
    assert done.returncode == 0, done.stderr
    assert "Zig 0.17.0" in done.stdout


def test_existing_pinned_zig_does_not_install():
    done = run_setup("""
function Get-Command($Name) { if ($Name -eq 'zig') { return 'MockZig' } }
function MockZig { $global:LASTEXITCODE=0; '0.17.0' }
function Install-StudioPackage { throw 'Healthy Zig must not be reinstalled' }
Initialize-StudioZig
if ($global:Refreshed) { throw 'Healthy PATH should be untouched' }
""")
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("version", ["", "0.16.0"])
def test_unavailable_or_wrong_version_after_install_is_actionable(version):
    done = run_setup(f"""
function Get-Command($Name) {{
  if ($Name -eq 'winget.exe') {{ return 'mock-winget' }}
  if ('{version}') {{ return 'MockZig' }}
}}
function MockZig {{ $global:LASTEXITCODE=0; '{version}' }}
function Install-StudioPackage {{ }}
Initialize-StudioZig
""")
    assert done.returncode != 0
    assert "Open a new PowerShell window" in done.stderr


def test_missing_package_manager_reports_prerequisite():
    done = run_setup("""
function Get-Command { return $null }
function Install-StudioPackage { throw 'No package manager should run' }
Initialize-StudioZig
""")
    assert done.returncode != 0
    assert "Install Windows Package Manager" in done.stderr


def test_zig_version_failure_is_not_treated_as_success():
    done = run_setup("""
function Get-Command { return 'MockZig' }
function MockZig { $global:LASTEXITCODE=7; '0.17.0' }
Initialize-StudioZig
""")
    assert done.returncode != 0
    assert "version detection failed" in done.stderr


def test_real_package_helper_calls_application_once_without_shadowing():
    done = run_setup("""
$global:Calls=0
function Get-Command($Name, $CommandType) {
  if ($Name -ne 'winget.exe' -or $CommandType -ne 'Application') { throw 'Must resolve application explicitly' }
  return 'MockPackageApplication'
}
function MockPackageApplication {
  $global:Calls++
  if (($args -join ' ') -notmatch 'install -e --id zig.zig.*--version 0.17.0') { throw 'Wrong install arguments' }
  $global:LASTEXITCODE=0
}
Install-StudioPackage 'zig.zig' @('--version','0.17.0')
if ($global:Calls -ne 1) { throw 'Package manager must run exactly once' }
""")
    assert done.returncode == 0, done.stderr
