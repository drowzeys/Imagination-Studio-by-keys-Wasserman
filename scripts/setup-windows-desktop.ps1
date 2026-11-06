<#
.SYNOPSIS
  Register the native Windows Studio with the notification tray (Windows runs the Studio natively only; an older
  install's WSL2 registration is dropped). Creates the tray launcher and Start/Stop shortcuts. No models, engines or
  system policy installs.
#>
param(
  [ValidateSet('native')]
  [string]$Backend = 'native',
  [string]$StudioDir = (Split-Path $PSScriptRoot -Parent),
  [switch]$MakeDefault
)
$ErrorActionPreference = 'Stop'
$Home_ = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$Payload = Join-Path $Home_ 'desktop-controller'
$Source = Split-Path $PSScriptRoot -Parent
# The tray's own copy of its scripts, so a checkout being updated never runs half-written files.
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
$Arguments = @('register','--backend',$Backend,'--studio',$StudioDir)
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
  $Link.Description = 'Start the native Studio and show its tray controller'
  $Link.Save()
}
$Link = $Shell.CreateShortcut((Join-Path $Menu 'Stop Imagination Studio.lnk'))
$Link.TargetPath = $PowerShell
$Link.Arguments = "-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Tray`" -Action Stop"
$Link.IconLocation = "$Icon,0"
$Link.Save()
Write-Host "Registered the native Studio. Start Imagination Studio from its shortcut."
