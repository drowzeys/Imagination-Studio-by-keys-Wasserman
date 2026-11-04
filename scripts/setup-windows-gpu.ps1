param(
    [switch]$Verify,
    [ValidateRange(0, 255)]
    [int]$DeviceIndex = 0
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This setup is for native Windows only.' }
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv'
$Python = Join-Path $Runtime 'Scripts\python.exe'

if (-not $Verify) {
    . (Join-Path $PSScriptRoot 'windows-python.ps1')
    Initialize-StudioPython -Runtime $Runtime -Arm64
}
if (-not (Test-Path $Python)) {
    throw 'No isolated GPU environment. Run this script without -Verify to install it.'
}
& $Python -X utf8 -c "import platform,sys; sys.exit(0 if platform.machine().upper() == 'ARM64' else 1)"
if ($LASTEXITCODE -ne 0) { throw 'This GPU environment requires native ARM64 Python.' }
if (-not $Verify) {
    & $Python -X utf8 -m pip install -r (Join-Path $Root 'app\requirements-windows-gpu.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Windows CUDA dependency installation failed.' }
}
& $Python -X utf8 -m pip check
if ($LASTEXITCODE -ne 0) { throw 'The GPU environment has incompatible dependencies.' }
& $Python -X utf8 (Join-Path $Root 'scripts\windows-gpu-check.py') --device-index $DeviceIndex
if ($LASTEXITCODE -ne 0) { throw 'Windows CUDA verification failed. Rendering remains unavailable.' }
