param(
    [switch]$Install,
    [switch]$LocalBrain,
    [ValidateRange(1, 65535)]
    [int]$Port = 7870
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv'
$Python = Join-Path $Runtime 'Scripts\python.exe'

if ($Install) {
    if (-not (Test-Path $Python)) {
        & py -V:3.13 -m venv $Runtime
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13 first: py install 3.13' }
    }
    & $Python -X utf8 -m pip install -r (Join-Path $Root 'app\requirements-windows.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Windows UI dependency installation failed.' }
}
if (-not (Test-Path $Python)) {
    throw 'Run this script with -Install first. Python 3.13 is required.'
}

$env:PYTHONUTF8 = '1'
$env:HOST = '127.0.0.1'
$env:PORT = "$Port"
$env:STUDIO_DISCOVER_DELAY = '-1'
$env:BOLT_BACKEND = 'external'
if ($LocalBrain) {
    $env:STUDIO_LOCAL_ENDPOINT = 'http://127.0.0.1:8080/v1'
    $env:STUDIO_LOCAL_MODEL = 'Qwen3-4B'
}
Write-Host "Windows UI-only preview: http://127.0.0.1:$Port"
Write-Host 'Only explicitly qualified Windows storyboard stills can render. Press Ctrl+C to stop.'
& $Python -X utf8 (Join-Path $Root 'app\server.py')
if ($LASTEXITCODE -ne 0) { throw "Studio exited with code $LASTEXITCODE." }
