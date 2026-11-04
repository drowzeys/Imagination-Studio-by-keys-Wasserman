<#
.SYNOPSIS
  One-shot Imagination Studio install for an NVIDIA RTX Spark N1X laptop (Windows 11 on Arm, the Studio in WSL2 Ubuntu).

.DESCRIPTION
  Run once in an administrator PowerShell on the laptop. Then click "Imagination Studio" on the taskbar to use it.
  It:
    1. checks the machine (ARM64, an N1X GPU) and WSL2 with an Ubuntu distribution, systemd on;
    2. sets .wslconfig so WSL may use the memory Windows has (the GPU keeps its own fixed carve-out);
    3. clones or updates the Studio inside Ubuntu and runs the DGX Spark recipe there (oneshot-setup-rtx-spark-n1x.sh:
       STUDIO_N1X=1, the GPU carve-out as the memory pool, memory checks that flag rather than refuse);
       every part is installed by default (--all), so the whole Studio runs on the laptop;
    4. makes a Start and a Stop launcher with the Studio's icon (Desktop, Start menu) and pins Start to the taskbar;
    5. keeps the laptop awake for renders: no sleep or hibernate plugged in (and on battery with -NeverSleepOnBattery),
       the lid does nothing, the Wi-Fi adapter is never powered down;
    6. optionally starts the Studio at sign-in (-StartAtLogon) and opens SSH for another machine (-EnableSsh).
  Running it again updates the Studio (--upgrade) and leaves what is already set as it is.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\oneshot-setup-rtx-spark-n1x.ps1
.EXAMPLE
  .\oneshot-setup-rtx-spark-n1x.ps1 -Parts "--sound --music" -StartAtLogon -NeverSleepOnBattery
.EXAMPLE
  .\oneshot-setup-rtx-spark-n1x.ps1 -EnableSsh -AuthorizedKey "ssh-ed25519 AAAA... you@your-machine"
#>
[CmdletBinding()]
param(
  [string]$Distro = "Ubuntu",
  [string]$WslUser = "",
  [string]$Repo = "https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git",
  [string]$Branch = "main",
  [string]$Parts = "--all",          # what oneshot-setup-spark.sh installs; "" for the core only
  [switch]$SkipStudioSetup,          # only the Windows side (launchers, pin, power, startup)
  [switch]$NoPin,
  [switch]$NoPower,
  [switch]$NeverSleepOnBattery,
  [switch]$StartAtLogon,
  [switch]$EnableSsh,
  [string]$AuthorizedKey = "",
  [switch]$Force                     # go on even when the GPU is not an N1X
)
$ErrorActionPreference = "Continue"   # native tools (wsl, powercfg) report through exit codes, checked where it matters
$ProgressPreference = "SilentlyContinue"
function Say($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  + $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  ! $text" -ForegroundColor Yellow }
function Die($text) { Write-Host "  x $text" -ForegroundColor Red; exit 1 }

# ---------- administrator: power settings, firewall and SSH need it ----------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Say "Restarting as administrator"
  $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
  foreach ($k in $PSBoundParameters.Keys) {
    $v = $PSBoundParameters[$k]
    if ($v -is [switch]) { if ($v) { $argv += "-$k" } } else { $argv += "-$k"; $argv += "`"$v`"" }
  }
  Start-Process powershell -Verb RunAs -ArgumentList $argv
  exit 0
}

# ---------- 1. the machine ----------
Say "This machine"
if ($env:PROCESSOR_ARCHITECTURE -ne "ARM64") { Warn "not Windows on Arm ($env:PROCESSOR_ARCHITECTURE)"; if (-not $Force) { Die "this recipe is for an RTX Spark N1X (-Force to go on)" } }
$gpu = ""
try { $gpu = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1) } catch {}
if ($gpu -match "N1X|RTX Spark") { Ok "GPU: $gpu" } else { Warn "GPU: '$gpu' is not an RTX Spark N1X"; if (-not $Force) { Die "install the NVIDIA driver, or -Force" } }
$ramGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Ok "Windows sees $ramGB GB of RAM (the GPU's carve-out is apart from it)"

# ---------- 2. WSL2 and Ubuntu ----------
Say "WSL2 and $Distro"
& wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
  Warn "WSL is not installed: installing it (a restart follows)"
  & wsl.exe --install --no-distribution
  Die "restart Windows, then run this script again"
}
$old = [Console]::OutputEncoding; [Console]::OutputEncoding = [Text.Encoding]::Unicode
$distros = (& wsl.exe -l -q) | ForEach-Object { $_.Trim() } | Where-Object { $_ }
[Console]::OutputEncoding = $old
if ($distros -notcontains $Distro) {
  Warn "$Distro is not installed: installing it. Make your Linux user when it asks, type exit, then run this script again"
  & wsl.exe --install -d $Distro
  exit 0
}
if (-not $WslUser) {
  $WslUser = (& wsl.exe -d $Distro -e sh -c "id -un").Trim()
  if ($WslUser -eq "root") {  # the default user is root: take the first normal user (uid 1000 and up)
    $WslUser = (& wsl.exe -d $Distro -u root -e sh -c "awk -F: '`$3>=1000 && `$3<60000 {print `$1; exit}' /etc/passwd").Trim()
  }
}
if (-not $WslUser -or $WslUser -eq "root") { Die "set a normal Linux user in $Distro (or pass -WslUser)" }
Ok "$Distro, user $WslUser"
function InWsl([string]$cmd, [string]$user = $WslUser) {
  & wsl.exe -d $Distro -u $user -e bash -lc $cmd
  if ($LASTEXITCODE -ne 0) { throw "in WSL ($user): $cmd exited ${LASTEXITCODE}" }
}
# systemd (the Studio's ComfyUI and guards expect a normal init), set once
$conf = (& wsl.exe -d $Distro -u root -e sh -c "cat /etc/wsl.conf 2>/dev/null") -join "`n"
if ($conf -notmatch "systemd\s*=\s*true") {
  InWsl "printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf" "root"
  & wsl.exe --shutdown
  Ok "systemd turned on in $Distro"
} else { Ok "systemd on" }

# .wslconfig: let WSL use the RAM Windows has, a 16 GB swap, localhost forwarding (the Studio's page on 127.0.0.1)
$wslcfg = Join-Path $env:USERPROFILE ".wslconfig"
$text = if (Test-Path $wslcfg) { Get-Content $wslcfg -Raw } else { "" }
$want = [ordered]@{ "memory" = "$([math]::Max(16, $ramGB - 2))GB"; "swap" = "16GB"; "localhostForwarding" = "true" }
$changed = $false
if ($text -notmatch "(?m)^\[wsl2\]") { $text = $text.TrimEnd() + "`r`n[wsl2]`r`n"; $changed = $true }
foreach ($k in $want.Keys) {
  if ($text -notmatch "(?mi)^\s*$k\s*=") { $text = $text -replace "(?m)^\[wsl2\]\s*$", "[wsl2]`r`n$k=$($want[$k])"; $changed = $true }
}
if ($changed) {
  if (Test-Path $wslcfg) { Copy-Item $wslcfg "$wslcfg.bak-imagination" -Force }
  Set-Content -Path $wslcfg -Value $text -Encoding ascii
  & wsl.exe --shutdown
  Ok ".wslconfig set (backup .wslconfig.bak-imagination)"
} else { Ok ".wslconfig already set" }

# ---------- 3. the Studio inside Ubuntu ----------
$repoDir = "/home/$WslUser/src/Imagination-Studio-by-keys-Wasserman"
if (-not $SkipStudioSetup) {
  Say "The Studio in $Distro ($repoDir)"
  InWsl "command -v git >/dev/null && command -v ffmpeg >/dev/null && command -v curl >/dev/null || (apt-get update -q && apt-get install -y -q git ffmpeg curl)" "root"
  InWsl "mkdir -p ~/src && if [ -d '$repoDir/.git' ]; then cd '$repoDir' && git fetch -q origin && git checkout -q '$Branch' && git pull -q --ff-only; else git clone -q -b '$Branch' '$Repo' '$repoDir'; fi && git -C '$repoDir' log --oneline -1"
  $installed = (& wsl.exe -d $Distro -u $WslUser -e sh -c "test -x ~/.local/opt/tensorfold-studio/venv/bin/python && echo yes").Trim()
  $how = if ($installed -eq "yes") { "--upgrade" } else { "--yes $Parts" }
  Say "Installing (oneshot-setup-rtx-spark-n1x.sh $how): models are large, this can take a while"
  & wsl.exe -d $Distro -u $WslUser -e bash -lc "cd '$repoDir' && bash oneshot-setup-rtx-spark-n1x.sh $how"
  if ($LASTEXITCODE -ne 0) { Warn "the Studio's setup ended with ${LASTEXITCODE}: see its last lines above (a gated model needs: hf auth login, inside $Distro)" }
  else { Ok "the Studio is installed" }
}

# ---------- 4. launchers, icon, taskbar ----------
Say "Launchers"
$home_ = Join-Path $env:LOCALAPPDATA "ImaginationStudio"
New-Item -ItemType Directory -Force -Path $home_ | Out-Null
$icon = Join-Path $home_ "imagination-studio.ico"
Copy-Item "\\wsl.localhost\$Distro$($repoDir -replace '/', '\')\app\static\brand\app-icon.ico" $icon -Force -ErrorAction SilentlyContinue
$start = Join-Path $home_ "Start-Studio.cmd"
$stop = Join-Path $home_ "Stop-Studio.cmd"
@"
@echo off
title Imagination Studio (RTX Spark)
rem Imagination Studio runs in WSL ($Distro) for as long as this window is open: close it, or run Stop, to stop it.
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7870/ -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo Imagination Studio is already running: opening it.
  start "" http://127.0.0.1:7870/
  timeout /t 3 >nul
  exit /b 0
)
echo Starting Imagination Studio... the page opens when it is ready. Keep this window open.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for (`$i = 0; `$i -lt 150; `$i++) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7870/ -TimeoutSec 2 | Out-Null; Start-Process 'http://127.0.0.1:7870/'; break } catch { Start-Sleep 2 } }"
wsl.exe -d $Distro -u $WslUser -e bash $repoDir/scripts/n1x-studio.sh
echo Imagination Studio stopped.
timeout /t 5 >nul
"@ | Set-Content -Path $start -Encoding ascii
@"
@echo off
title Stop Imagination Studio
wsl.exe -d $Distro -u $WslUser -e bash $repoDir/scripts/n1x-studio.sh stop
timeout /t 3 >nul
"@ | Set-Content -Path $stop -Encoding ascii
$ws = New-Object -ComObject WScript.Shell
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$links = @{}
foreach ($where in @([Environment]::GetFolderPath("Desktop"), $menu)) {
  $l = $ws.CreateShortcut((Join-Path $where "Imagination Studio.lnk"))
  $l.TargetPath = "$env:WINDIR\System32\cmd.exe"; $l.Arguments = "/c `"$start`""; $l.WorkingDirectory = $home_
  $l.IconLocation = "$icon,0"; $l.WindowStyle = 7; $l.Description = "Start Imagination Studio and open it"; $l.Save()
  $links[$where] = $l.FullName
}
$s = $ws.CreateShortcut((Join-Path $menu "Stop Imagination Studio.lnk"))
$s.TargetPath = "$env:WINDIR\System32\cmd.exe"; $s.Arguments = "/c `"$stop`""; $s.IconLocation = "$icon,0"; $s.WindowStyle = 7
$s.Description = "Stop Imagination Studio and free the GPU"; $s.Save()
Ok "Start and Stop in the Start menu, Start on the Desktop ($home_)"

if (-not $NoPin) {
  # Windows 11 has no API to pin an app; its supported way is a taskbar layout (the Start Layout policy), which appends
  # the pin and leaves the person's own pins alone. The pin appears after Explorer restarts (or the next sign-in).
  $layout = Join-Path $home_ "TaskbarLayout.xml"
  @"
<?xml version="1.0" encoding="utf-8"?>
<LayoutModificationTemplate xmlns="http://schemas.microsoft.com/Start/2014/LayoutModification"
  xmlns:defaultlayout="http://schemas.microsoft.com/Start/2014/FullDefaultLayout"
  xmlns:start="http://schemas.microsoft.com/Start/2014/StartLayout"
  xmlns:taskbar="http://schemas.microsoft.com/Start/2014/TaskbarLayout" Version="1">
  <CustomTaskbarLayoutCollection PinListPlacement="Append">
    <defaultlayout:TaskbarLayout>
      <taskbar:TaskbarPinList>
        <taskbar:DesktopApp DesktopApplicationLinkPath="$($links[$menu])" />
      </taskbar:TaskbarPinList>
    </defaultlayout:TaskbarLayout>
  </CustomTaskbarLayoutCollection>
</LayoutModificationTemplate>
"@ | Set-Content -Path $layout -Encoding utf8
  $pol = "HKCU:\Software\Policies\Microsoft\Windows\Explorer"
  New-Item -Path $pol -Force | Out-Null
  Set-ItemProperty -Path $pol -Name StartLayoutFile -Value $layout
  Set-ItemProperty -Path $pol -Name LockedStartLayout -Value 1 -Type DWord
  Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
  Ok "pinned to the taskbar (layout $layout; if it is not there yet, sign out and in once, or right-click Start > Pin to taskbar)"
}

# ---------- 5. awake for renders ----------
if (-not $NoPower) {
  Say "Power: awake while plugged in$(if ($NeverSleepOnBattery) { ' and on battery' })"
  powercfg /change standby-timeout-ac 0; powercfg /change hibernate-timeout-ac 0
  powercfg /setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0
  if ($NeverSleepOnBattery) {
    powercfg /change standby-timeout-dc 0; powercfg /change hibernate-timeout-dc 0; powercfg /hibernate off
    powercfg /setdcvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0
  }
  powercfg /setactive SCHEME_CURRENT
  cmd /c "powercfg /overlaysetactive ded574b5-45a0-4f42-8737-46345c09c238 >nul 2>&1"   # Windows power mode: Best performance
  Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "Wi-?Fi|Wireless|WLAN" } |
    ForEach-Object { Disable-NetAdapterPowerManagement -Name $_.Name -NoRestart -ErrorAction SilentlyContinue }
  Ok "no sleep or hibernate on AC, lid does nothing, Windows on Best performance"
  Warn "for the DGX Spark's speed set the maker's own mode too (HP: myHP > Performance), plugged in: docs/rtx-spark-n1x.md"
}

# ---------- 6. optional: start at sign-in, SSH ----------
if ($StartAtLogon) {
  $a = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\cmd.exe" -Argument "/c `"$start`""
  $t = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $st = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
  Register-ScheduledTask -TaskName "Imagination Studio" -Action $a -Trigger $t -Settings $st -Force | Out-Null
  Ok "starts at sign-in (Task Scheduler: Imagination Studio; Unregister-ScheduledTask 'Imagination Studio' to undo)"
}
if ($EnableSsh) {
  Say "SSH"
  if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) { Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null }
  Set-Service sshd -StartupType Automatic; Start-Service sshd
  Get-NetConnectionProfile | Where-Object NetworkCategory -eq Public | ForEach-Object { Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private }
  if (-not (Get-NetFirewallRule -Name sshd-imagination -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name sshd-imagination -DisplayName "OpenSSH Server (Imagination Studio)" -Protocol TCP -LocalPort 22 -Action Allow -Profile Private | Out-Null
  }
  if ($AuthorizedKey) {
    $keys = "C:\ProgramData\ssh\administrators_authorized_keys"
    if (-not (Test-Path $keys) -or -not (Select-String -Path $keys -SimpleMatch $AuthorizedKey -Quiet)) { Add-Content -Path $keys -Value $AuthorizedKey }
    icacls $keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null
  }
  Ok "SSH on port 22 for this home network (Private)$(if ($AuthorizedKey) { ', the key added' })"
}

Say "Done. Click Imagination Studio on the taskbar (or the Desktop) to start it; close its window, or Stop, to stop it."
