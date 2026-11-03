param([switch]$Verify)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run scripts\app.ps1 -Install first.' }
Write-Host 'FFmpeg/ffprobe: native Windows ARM64, BtbN GPL build, about 135 MB.'
Write-Host 'Installed per-user; no administrator access or system PATH changes.'
$Arguments = @('-X', 'utf8', (Join-Path $Root 'app\windows_media.py'))
if ($Verify) { $Arguments += '--verify' }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Windows FFmpeg setup/verification failed.' }
