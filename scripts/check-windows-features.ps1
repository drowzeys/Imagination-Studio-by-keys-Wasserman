param(
    [ValidateSet("hlg", "finish", "all")]
    [string]$Feature = "all"
)
$ErrorActionPreference = "Stop"
$Python = Join-Path $env:LOCALAPPDATA "ImaginationStudio\ui-venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Run scripts\app.ps1 -InstallOnly -Install first." }
& $Python -X utf8 (Join-Path $PSScriptRoot "windows-features-check.py") --feature $Feature
if ($LASTEXITCODE -ne 0) { throw "Windows feature qualification failed; the failed feature remains disabled." }
