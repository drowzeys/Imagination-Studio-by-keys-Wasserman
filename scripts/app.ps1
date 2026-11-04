param(
    [switch]$Install,
    [switch]$InstallOnly,   # with -Install: set the environment up, do not start the Studio
    [switch]$Preview,       # the small preview tier even where the native CUDA tier is installed
    [switch]$LocalBrain,
    [ValidateRange(1, 65535)]
    [int]$Port = 7870
)

$ErrorActionPreference = 'Stop'
if ($InstallOnly -and -not $Install) { throw '-InstallOnly requires -Install.' }
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv'
$Python = Join-Path $Runtime 'Scripts\python.exe'

if ($Install) {
    . (Join-Path $PSScriptRoot 'windows-python.ps1')
    Initialize-StudioPython -Runtime $Runtime
    & $Python -X utf8 -m pip install -r (Join-Path $Root 'app\requirements-windows.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Windows UI dependency installation failed.' }
    if ($InstallOnly) { return }
}
if (-not (Test-Path $Python)) {
    throw 'Run this script with -Install first. Python 3.13 is required.'
}
if ($InstallOnly) { exit 0 }
# the native CUDA tier (app\windows_cuda.py): chosen by the Studio itself where setup-windows-cuda.ps1 recorded it
$Native = (-not $Preview) -and (Test-Path (Join-Path $env:LOCALAPPDATA 'ImaginationStudio\cuda-tier.json'))

$env:PYTHONUTF8 = '1'
$env:HOST = '127.0.0.1'
$env:PORT = "$Port"
$env:STUDIO_DISCOVER_DELAY = '-1'
if ($Preview) { $env:STUDIO_WINDOWS_TIER = 'preview' }
# the native tier's server records its process id, so scripts\stop-windows-studio.ps1 stops this one and nothing else
if ($Native) { $env:STUDIO_PIDFILE = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\studio.pid' }
if (-not $Native) { $env:BOLT_BACKEND = 'external' }
if ($LocalBrain) {
    $env:STUDIO_LOCAL_ENDPOINT = 'http://127.0.0.1:8080/v1'
    $env:STUDIO_LOCAL_MODEL = 'Qwen3-4B'
}
if ($Native) {
    Write-Host "Imagination Studio, native Windows CUDA tier: http://127.0.0.1:$Port"
    Write-Host 'Video, pictures, songs and the Bolt brain on this GPU (ComfyUI and TensorFold). Press Ctrl+C to stop.'
} else {
    Write-Host "Windows limited preview: http://127.0.0.1:$Port"
    Write-Host 'Qualified stills, five-second sound effects, CPU media exports and bounded motion graphics. Press Ctrl+C to stop.'
}
& $Python -X utf8 (Join-Path $Root 'app\server.py')
if ($LASTEXITCODE -ne 0) { throw "Studio exited with code $LASTEXITCODE." }
