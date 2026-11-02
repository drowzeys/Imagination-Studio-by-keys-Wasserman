param([switch]$Verify)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
$Models = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\models\Sound-Comfy\stable-audio-open-1.0'

& (Join-Path $PSScriptRoot 'setup-windows-gpu.ps1') -Verify
if ($LASTEXITCODE -ne 0) { throw 'The Windows GPU environment must pass verification first.' }
Write-Host 'Stable Audio Open: Stability AI Community License; accept its Hugging Face gate yourself.'
Write-Host 'About 5.3 GB of weights. Files stay outside the repository. This setup does not run inference.'
$Arguments = @('-X', 'utf8', (Join-Path $Root 'app\windows_sfx.py'), '--models', $Models)
if (-not $Verify) { $Arguments += '--download' }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Stable Audio Open file setup/verification failed; no inference was started.' }
