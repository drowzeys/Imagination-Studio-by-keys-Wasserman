param(
    [switch]$Verify,
    [switch]$Guarded,
    [ValidateRange(0, 255)]
    [int]$DeviceIndex = 0,
    [ValidateRange(1, 300)]
    [int]$StartupTimeout = 120,
    [ValidateRange(0, 300)]
    [int]$HoldSeconds = 0
)

$ErrorActionPreference = 'Stop'
if ($HoldSeconds -gt 0 -and -not $Guarded) { throw '-HoldSeconds requires -Guarded.' }
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
$Source = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ComfyUI'
$Commit = 'b9b290e43f1eb379946dfeb013faf9bbefa1a618'

& (Join-Path $PSScriptRoot 'setup-windows-gpu.ps1') -Verify -DeviceIndex $DeviceIndex
if ($LASTEXITCODE -ne 0) { throw 'Run setup-windows-gpu.ps1 successfully before this check.' }
if (Test-Path $Source) {
    $Head = & git -C $Source rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $Head -ne $Commit) { throw 'The existing ComfyUI checkout is not at the required pin; it has not been changed.' }
    $Dirty = & git -C $Source status --porcelain
    if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'The existing ComfyUI checkout has changes; it has not been changed.' }
}
if (-not $Verify) {
    & $Python -X utf8 -m pip install --only-binary=:all: -r (Join-Path $Root 'app\requirements-windows-comfy.txt')
    if ($LASTEXITCODE -ne 0) {
        throw 'ComfyUI dependency installation failed; see the pip error above for the package and required version. If your configured mirror does not supply it, ask your package-source administrator to make that pinned version available, then rerun .\scripts\setup-windows-comfy.ps1. Do not change the tested pins or bypass your package-source policy.'
    }
    if (-not (Test-Path $Source)) {
        & git init --quiet $Source
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated ComfyUI checkout.' }
        & git -C $Source remote add origin https://github.com/comfyanonymous/ComfyUI.git
        if ($LASTEXITCODE -ne 0) { throw 'Could not configure the ComfyUI source remote.' }
        & git -C $Source fetch --depth 1 origin $Commit
        if ($LASTEXITCODE -ne 0) { throw 'Could not fetch the pinned ComfyUI source.' }
        & git -C $Source -c advice.detachedHead=false checkout --detach FETCH_HEAD
        if ($LASTEXITCODE -ne 0) { throw 'Could not check out the pinned ComfyUI source.' }
    }
}
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'The isolated GPU environment has incompatible dependencies.' }
$CheckArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'windows-comfy-check.py'),
    '--source', $Source, '--device-index', "$DeviceIndex", '--timeout', "$StartupTimeout",
    '--hold-seconds', "$HoldSeconds")
if ($Guarded) { $CheckArgs += '--guarded' }
& $Python @CheckArgs
if ($LASTEXITCODE -ne 0) { throw 'No-model ComfyUI readiness or shutdown failed. Rendering remains unavailable.' }
