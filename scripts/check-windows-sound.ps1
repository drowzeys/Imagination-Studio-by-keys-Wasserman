param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
$HomeDir = $env:STUDIO_HOME
if (-not $HomeDir) { $HomeDir = Join-Path $env:USERPROFILE 'ImaginationStudio' }
$Output = Join-Path $HomeDir ('renders\calibration\windows-sound-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $Python -X utf8 (Join-Path $Root 'app\windows_sound.py') --qualify --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Sound-effect qualification failed; the route remains disabled.' }
Write-Host "Verified five-second sound: $Output"
