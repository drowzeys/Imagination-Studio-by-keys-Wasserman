param([switch]$Calibrate, [switch]$CancelDuringSampling)

$ErrorActionPreference = 'Stop'
if (-not $Calibrate) { throw 'This runs an explicit bounded model experiment. Use -Calibrate to confirm.' }
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
$Models = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\models\Sound-Comfy\stable-audio-open-1.0'
$Source = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ComfyUI'
$Studio = $env:STUDIO_HOME
if (-not $Studio) { $Studio = Join-Path $env:USERPROFILE 'ImaginationStudio' }
$Output = Join-Path $Studio ("renders\calibration\windows-sfx-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + ".flac")

Write-Host 'Calibration only: native PyTorch allocation cap 12 GiB; process commit cap 12 GiB; job commit cap 16 GiB.'
Write-Host 'Uses the verified Stable Audio Open files and a GPU-first ComfyUI helper; normal render routes remain disabled.'
$Arguments = @('-X', 'utf8', (Join-Path $Root 'scripts\windows-sfx-check.py'),
    '--calibrate', '--models', $Models, '--source', $Source, '--output', $Output)
if ($CancelDuringSampling) { $Arguments += '--cancel-during-sampling' }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'The guarded SFX calibration did not complete; no route has been qualified.' }
if ($CancelDuringSampling) {
    Write-Host 'Sampling cancellation confirmed; a receipt was saved, without copying audio.'
} else {
    Write-Host 'Validated audio and a calibration receipt are in the Studio renders\calibration folder.'
}
