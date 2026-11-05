<#
.SYNOPSIS
  Register a Native Windows or WSL2 Studio without replacing the other backend or its default.
  Creates one notification tray launcher and Start/Stop shortcuts. No models, engines or system policy installs.
#>
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('native','wsl')]
  [string]$Backend,
  [string]$StudioDir = (Split-Path $PSScriptRoot -Parent),
  [string]$Distro,
  [string]$WslUser,
  [string]$WslRepo,
  [switch]$MakeDefault
)
$ErrorActionPreference = 'Stop'
$Home_ = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$Payload = Join-Path $Home_ 'desktop-controller'
$Source = Split-Path $PSScriptRoot -Parent
# Keep the Windows tray payload local even when registering a checkout inside a stopped WSL distribution.
New-Item -ItemType Directory -Force -Path (Join-Path $Payload 'scripts'), (Join-Path $Payload 'app\static\brand') | Out-Null
foreach ($Name in @('windows-desktop.py','studio-tray.ps1')) {
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot $Name) -Destination (Join-Path $Payload "scripts\$Name") -Force
}
foreach ($Name in @('access.py','windows_process.py','windows_memory.py','windows_backend_handle.py')) {
  Copy-Item -LiteralPath (Join-Path $Source "app\$Name") -Destination (Join-Path $Payload "app\$Name") -Force
}
Copy-Item -LiteralPath (Join-Path $Source 'app\static\brand\app-icon.ico') -Destination (Join-Path $Payload 'app\static\brand\app-icon.ico') -Force
$Python = Join-Path $Home_ 'ui-venv\Scripts\python.exe'
$PythonArgs = @()
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = (Get-Command py.exe -ErrorAction Stop).Source
  $PythonArgs = @('-3.13')
}
$Controller = Join-Path $Payload 'scripts\windows-desktop.py'
$Arguments = @('register','--backend',$Backend)
if ($Backend -eq 'native') { $Arguments += @('--studio',$StudioDir) }
else { $Arguments += @('--distro',$Distro,'--user',$WslUser,'--repo',$WslRepo) }
$Result = (& $Python @PythonArgs -X utf8 $Controller @Arguments | Out-String) | ConvertFrom-Json
if (-not $Result.ok) { throw $Result.error }
if ($MakeDefault) {
  $Result = (& $Python @PythonArgs -X utf8 $Controller default --backend $Backend | Out-String) | ConvertFrom-Json
  if (-not $Result.ok) { throw $Result.error }
}
$Tray = Join-Path $Payload 'scripts\studio-tray.ps1'
$PowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Start = Join-Path $Home_ 'Start-Studio.cmd'
$Stop = Join-Path $Home_ 'Stop-Studio.cmd'
@("@echo off", "`"$PowerShell`" -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Tray`" -Action Start") |
  Set-Content -LiteralPath $Start -Encoding ascii
@("@echo off", "`"$PowerShell`" -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Tray`" -Action Stop") |
  Set-Content -LiteralPath $Stop -Encoding ascii
$Icon = Join-Path $Home_ 'imagination-studio.ico'
Copy-Item -LiteralPath (Join-Path $Payload 'app\static\brand\app-icon.ico') -Destination $Icon -Force
$Shell = New-Object -ComObject WScript.Shell
$Menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
foreach ($Folder in @([Environment]::GetFolderPath('Desktop'),$Menu)) {
  $Link = $Shell.CreateShortcut((Join-Path $Folder 'Imagination Studio.lnk'))
  $Link.TargetPath = $PowerShell
  $Link.Arguments = "-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Tray`" -Action Start"
  $Link.WorkingDirectory = $Home_
  $Link.IconLocation = "$Icon,0"
  $Link.Description = 'Start the default Studio backend and show its tray controller'
  $Link.Save()
}
$Link = $Shell.CreateShortcut((Join-Path $Menu 'Stop Imagination Studio.lnk'))
$Link.TargetPath = $PowerShell
$Link.Arguments = "-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Tray`" -Action Stop"
$Link.IconLocation = "$Icon,0"
$Link.Save()
Write-Host "Registered $Backend. Existing backend registrations/default are preserved. Start Imagination Studio from its shortcut."
