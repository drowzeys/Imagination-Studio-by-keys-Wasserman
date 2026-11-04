param([switch]$Verify)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup-windows-comfy.ps1 first.' }
Write-Host 'SD-Turbo: Stability AI Community License; about 2.6 GB of official FP16 components, outside the repository.'
Write-Host 'Hobby use is permitted; commercial use has registration and revenue conditions.'
$Arguments = @('-X', 'utf8', (Join-Path $Root 'app\windows_storyboard.py'), '--verify')
if (-not $Verify) { $Arguments += '--download' }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'The storyboard checkpoint setup failed. No render was started.' }
