param(
    [switch]$Cpu,
    [switch]$Check,
    [ValidateRange(1, 65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv\Scripts\python.exe'
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\llama-cpp\b12063'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run scripts\app.ps1 -Install first.' }
$Arguments = @('-X', 'utf8', (Join-Path $Root 'app\windows_brain.py'), '--root', $Runtime, '--serve', '--port', "$Port")
if ($Cpu) { $Arguments += '--cpu' }
if ($Check) { $Arguments += '--check' }
Write-Host 'Small local brain: one 4096-token slot. Ctrl+C stops its owned process tree.'
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'The local brain stopped with an error. See its server.log in the runtime folder.' }
