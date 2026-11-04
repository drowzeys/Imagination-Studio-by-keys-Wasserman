param([switch]$Verify)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv\Scripts\python.exe'
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\llama-cpp\b12063'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run scripts\app.ps1 -Install first.' }
Write-Host 'Qwen3-4B Q4_K_M: Apache-2.0; llama.cpp: MIT.'
Write-Host 'About 2.5 GB of weights and 310 MB of runtime archives, outside the repository.'
$Arguments = @('-X', 'utf8', (Join-Path $Root 'app\windows_brain.py'), '--root', $Runtime)
if (-not $Verify) { $Arguments += '--download' }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'The small local-brain file setup failed. No model was started.' }
