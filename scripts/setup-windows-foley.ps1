param(
    [switch]$Verify,
    [switch]$DependenciesOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$CorePython = Join-Path $Runtime 'gpu-venv\Scripts\python.exe'
$Python = Join-Path $Runtime 'foley-gpu-venv\Scripts\python.exe'
$Setup = Join-Path $PSScriptRoot 'windows-cuda-setup.py'
. (Join-Path $PSScriptRoot 'windows-msvc.ps1')
if ($env:OS -ne 'Windows_NT' -or ($env:PROCESSOR_ARCHITECTURE -ne 'ARM64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'ARM64')) {
    throw 'This foley setup requires Windows on ARM64.'
}
if (-not (Test-Path -LiteralPath $CorePython)) {
    if ($Verify) { throw 'The Windows GPU environment is missing; verification changed nothing.' }
    & (Join-Path $PSScriptRoot 'setup-windows-gpu.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'The Windows GPU environment setup failed.' }
}
if ($Verify -and -not (Test-Path -LiteralPath $Python)) {
    throw 'The isolated foley runtime is missing; rerun setup without -Verify.'
}
if (-not $Verify) {
    . (Join-Path $PSScriptRoot 'windows-python.ps1')
    Initialize-StudioPython -Runtime (Join-Path $Runtime 'foley-gpu-venv') -Arm64
    $TargetSite = Join-Path $Runtime 'foley-gpu-venv\Lib\site-packages'
    $Stamp = Join-Path $Runtime 'foley-gpu-venv\core-requirements.sha256'
    $Expected = (@('requirements-windows-gpu.txt', 'requirements-windows-comfy.txt', 'requirements-windows-comfy-constraints.txt') |
        ForEach-Object { (Get-FileHash -LiteralPath (Join-Path $Root "app\$_") -Algorithm SHA256).Hash }) -join ':'
    if (-not (Test-Path -LiteralPath $Stamp) -or (Get-Content -Raw -LiteralPath $Stamp).Trim() -ne $Expected) {
        & $CorePython -X utf8 -m pip install --target $TargetSite --ignore-installed --upgrade --only-binary=:all: -r (Join-Path $Root 'app\requirements-windows-comfy.txt')
        if ($LASTEXITCODE -ne 0) { throw 'The isolated foley GPU/ComfyUI dependency installation failed.' }
        Set-Content -LiteralPath $Stamp -Value $Expected -Encoding ASCII
    }
}
$Comfy = Join-Path $Runtime 'ComfyUI'
if (-not (Test-Path -LiteralPath (Join-Path $Comfy 'main.py'))) {
    if ($Verify) { throw 'ComfyUI is missing; verification changed nothing.' }
    & (Join-Path $PSScriptRoot 'setup-windows-comfy.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'The Windows ComfyUI setup failed.' }
}
if (-not $Verify) {
    Write-Host 'Installing pinned ComfyUI-MMAudio dependencies in the isolated foley runtime; the baseline GPU environment is untouched.'
    & $Python -X utf8 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('soxr') else 1)"
    if ($LASTEXITCODE -ne 0) {
        $Compiler = Initialize-StudioArm64Compiler
        $Locator = Find-StudioVsWhere
        if ($Locator) { $env:PATH = (Split-Path $Locator -Parent) + [IO.Path]::PathSeparator + $env:PATH }
        $Command = 'call "{0}" arm64 >nul && "{1}" -X utf8 "{2}" nodes --parts foley --pip-python "{3}" --target "{4}"' -f $Compiler, $Python, $Setup, $CorePython, $TargetSite
        & $env:ComSpec /d /s /c $Command
    } else {
        & $Python -X utf8 $Setup nodes --parts foley --pip-python $CorePython --target $TargetSite
    }
    if ($LASTEXITCODE -ne 0) { throw 'The pinned foley node/dependency installation failed; no model was loaded.' }
}
$Node = Join-Path $Comfy 'custom_nodes\ComfyUI-MMAudio'
$Commit = & git -C $Node rev-parse HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or $Commit -ne '8eaeb72edc3aaf2059b57f2d96a1f6f689f19ae2') {
    throw 'The pinned ComfyUI-MMAudio node pack is missing or changed; rerun setup without -Verify.'
}
$Probe = @'
from importlib import metadata
import json
import platform
import numpy as np
import soxr
from packaging.requirements import Requirement
from pathlib import Path
import sys
versions = {}
assert platform.machine().lower() in ("arm64", "aarch64"), "Foley needs native ARM64 Python"
for line in (line for name in sys.argv[1:] for line in Path(name).read_text().splitlines()):
    if not line.strip() or line.lstrip().startswith(("#", "-")):
        continue
    requirement = Requirement(line)
    version = metadata.version(requirement.name)
    if version not in requirement.specifier:
        raise RuntimeError(f"{requirement.name} {version} does not meet {requirement.specifier}")
    versions[requirement.name] = version
result = soxr.resample(np.ones((4800, 2), np.float32), 48000, 44100)
assert result.shape == (4410, 2) and np.isfinite(result).all()
print(json.dumps({"dependencies": versions, "soxr": metadata.version("soxr"), "native_resample": True}))
'@
$Probe | & $Python -X utf8 - (Join-Path $Node 'requirements.txt') (Join-Path $Root 'app\requirements-windows-gpu.txt')
if ($LASTEXITCODE -ne 0) { throw 'Foley dependency verification failed; no model was loaded.' }
& $Python -X utf8 -m pip check
if ($LASTEXITCODE -ne 0) { throw 'The isolated foley environment has broken dependencies; verification did not pass.' }
if ($DependenciesOnly) {
    Write-Host 'Foley dependencies verified. No weights downloaded and no inference run.'
    return
}
$UiPython = Join-Path $Runtime 'ui-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $UiPython)) {
    if ($Verify) { throw 'The Studio UI environment is missing; verification changed nothing.' }
    & (Join-Path $PSScriptRoot 'app.ps1') -Install -InstallOnly
    if ($LASTEXITCODE -ne 0) { throw 'Studio UI environment setup failed.' }
}
$MediaProbe = @'
import sys
sys.path.insert(0, sys.argv[1])
import windows_media
sys.exit(0 if all((windows_media.bin_folder() / (name + ".exe")).is_file() for name in ("ffmpeg", "ffprobe")) else 1)
'@
$MediaProbe | & $UiPython -X utf8 - (Join-Path $Root 'app')
$MediaPresent = $LASTEXITCODE -eq 0
& (Join-Path $PSScriptRoot 'setup-windows-ffmpeg.ps1') -Verify:($Verify -or $MediaPresent)
if ($LASTEXITCODE -ne 0) { throw 'Native FFmpeg setup/verification failed.' }
$Models = Join-Path $Runtime 'models'
if (-not $Verify) {
    Write-Host 'MMAudio weights: about 5.2 GiB; CC-BY-NC 4.0, non-commercial. Stored outside the repository.'
    & $Python -X utf8 $Setup models --parts foley --models $Models
    if ($LASTEXITCODE -ne 0) { throw 'Foley model download failed; no inference was run.' }
}
$Weights = Join-Path $Models 'Sound-Comfy\mmaudio'
foreach ($Name in @('mmaudio_large_44k_v2_fp16.safetensors', 'mmaudio_vae_44k_fp16.safetensors',
                     'mmaudio_synchformer_fp16.safetensors', 'apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors',
                     'nvidia\bigvgan_v2_44khz_128band_512x\bigvgan_generator.pt',
                     'nvidia\bigvgan_v2_44khz_128band_512x\config.json')) {
    $File = Join-Path $Weights $Name
    if (-not (Test-Path -LiteralPath $File -PathType Leaf) -or (Get-Item -LiteralPath $File).Length -eq 0) {
        throw "A foley model file is missing: $Name"
    }
}
Write-Host 'Foley files and dependencies are installed. This is not a render qualification; existing feature passes were not changed.'
