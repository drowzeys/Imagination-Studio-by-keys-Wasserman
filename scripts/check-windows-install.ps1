param(
    [ValidateSet('ui', 'gpu', 'comfy', 'storyboard', 'sound', 'brain', 'media', 'graphics')]
    [string[]]$Components = @('ui'),
    [switch]$All
)

$ErrorActionPreference = 'Stop'
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run scripts\app.ps1 -Install first.' }
if ($All) { $Components = @('ui', 'gpu', 'comfy', 'storyboard', 'sound', 'brain', 'media', 'graphics') }
Write-Host 'Offline install verification only: no downloads, GPU probes, browsers, model loads or qualification writes.'
& $Python -X utf8 (Join-Path $PSScriptRoot 'windows-install-check.py') --components @Components
if ($LASTEXITCODE -ne 0) { throw 'Installed files or dependencies failed verification; no installation was changed.' }
