param(
  [ValidateSet('Tray', 'Start', 'Stop')]
  [string]$Action = 'Tray'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$Root = Split-Path $PSScriptRoot -Parent
$Home_ = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$Controller = Join-Path $PSScriptRoot 'windows-desktop.py'
$Python = Join-Path $Home_ 'ui-venv\Scripts\python.exe'
$PythonArgs = @()
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = (Get-Command py.exe -ErrorAction Stop).Source
  $PythonArgs = @('-3.13')
}
function Invoke-Desktop([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments) {
  $Text = & $Python @PythonArgs -X utf8 $Controller @Arguments
  $Result = ($Text | Out-String) | ConvertFrom-Json
  if (-not $Result.ok) { throw $Result.error }
  return $Result
}
function Show-Problem($Message) {
  [System.Windows.Forms.MessageBox]::Show([string]$Message, 'Imagination Studio', 'OK', 'Warning') | Out-Null
}
function Confirm-Stop($Message) {
  return [System.Windows.Forms.MessageBox]::Show($Message, 'Imagination Studio', 'YesNo', 'Question') -eq 'Yes'
}
if ($Action -eq 'Stop') {
  if (Confirm-Stop 'Stop the tray-owned Studio? Queued or running jobs will prevent stopping.') {
    try { $null = Invoke-Desktop @('stop', '--confirmed') } catch { Show-Problem $_.Exception.Message }
  }
  exit
}
$Hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($Home_))).Replace('-', '')
$Created = $false
$Mutex = New-Object Threading.Mutex($true, "Local\ImaginationStudioTray-$Hash", [ref]$Created)
if (-not $Created) {
  if ($Action -eq 'Start') {
    try {
      $null = Invoke-Desktop @('start')
      Start-Process 'http://127.0.0.1:7870/'
    } catch { Show-Problem $_.Exception.Message }
  }
  $Mutex.Dispose()
  exit
}
$Tray = New-Object System.Windows.Forms.NotifyIcon
$IconPath = Join-Path $Root 'app\static\brand\app-icon.ico'
$Tray.Icon = New-Object System.Drawing.Icon($IconPath)
$Tray.Text = 'Imagination Studio'
$Menu = New-Object System.Windows.Forms.ContextMenuStrip
$Tray.ContextMenuStrip = $Menu
$Tray.Visible = $true
$script:Busy = $false
$script:Snapshot = $null
$script:RefreshError = ''
function Refresh-Desktop {
  if ($script:Busy) { return }
  try { $script:Snapshot = Invoke-Desktop @('status'); $script:RefreshError = '' }
  catch { $script:RefreshError = $_.Exception.Message }
}
function Run-Desktop([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments, [switch]$Open) {
  if ($script:Busy) { return }
  $script:Busy = $true
  $Tray.Text = 'Imagination Studio - working'
  try {
    $script:Snapshot = Invoke-Desktop $Arguments
    if ($Open) { Start-Process 'http://127.0.0.1:7870/' }
  } catch { Show-Problem $_.Exception.Message }
  finally { $script:Busy = $false; Refresh-Desktop }
}
function Add-Menu($Text, [scriptblock]$Click, [bool]$Enabled = $true, [bool]$Checked = $false) {
  $Item = New-Object System.Windows.Forms.ToolStripMenuItem($Text)
  $Item.Enabled = $Enabled
  $Item.Checked = $Checked
  if ($Click) { $Item.add_Click($Click) }
  $Menu.Items.Add($Item) | Out-Null
}
function Draw-Menu {
  Refresh-Desktop
  $Menu.Items.Clear()
  $S = $script:Snapshot
  $Names = @{native='Native Windows'; wsl='WSL2'; external='External Studio'}
  $Running = if ($S.running) { $Names[$S.running] } else { 'Stopped' }
  Add-Menu "Studio: $Running" $null $false
  if ($script:RefreshError) {
    Add-Menu $script:RefreshError $null $false
  } elseif ($S.detail) {
    Add-Menu $S.detail $null $false
  }
  $Tray.Text = "Imagination Studio - $Running"
  Add-Menu 'Open Studio' { Start-Process 'http://127.0.0.1:7870/' } ([bool]$S.running)
  Add-Menu 'Start default backend' { Run-Desktop @('start') -Open } (-not $S.running -and -not $script:Busy)
  foreach ($Kind in @('native','wsl')) {
    $Value = $Kind
    $Name = $Names[$Kind]
    $Installed = $S.backends.$Kind.available
    $SwitchHandler = {
      if (Confirm-Stop "Switch to $Name? The current tray-owned backend will stop first. Active jobs or an externally started app prevent switching.") {
        Run-Desktop @('switch','--backend',$Value,'--confirmed') -Open
      }
    }.GetNewClosure()
    Add-Menu "Switch to $Name" $SwitchHandler ([bool]$Installed -and -not $script:Busy -and $S.running -ne $Kind -and $S.running -ne 'external')
    $DefaultHandler = { Run-Desktop @('default','--backend',$Value) }.GetNewClosure()
    Add-Menu "Default: $Name" $DefaultHandler ([bool]$Installed -and -not $script:Busy) ($S.default -eq $Kind)
  }
  Add-Menu 'Stop Studio' {
    if (Confirm-Stop 'Stop the tray-owned Studio? Active jobs prevent stopping.') { Run-Desktop @('stop','--confirmed') }
  } ([bool]$S.running -and $S.running -ne 'external' -and -not $script:Busy)
  $Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
  Add-Menu 'Refresh status' { Refresh-Desktop }
  Add-Menu 'Exit tray (leave Studio running)' { [System.Windows.Forms.Application]::Exit() }
}
$Menu.add_Opening({ Draw-Menu })
$Tray.add_DoubleClick({ Start-Process 'http://127.0.0.1:7870/' })
$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 10000
$Timer.add_Tick({
  Refresh-Desktop
  if ($script:Snapshot) {
    $Running = $script:Snapshot.running
    $Tray.Text = 'Imagination Studio - ' + $(if ($Running) { $Running } else { 'stopped' })
  }
})
try {
  Refresh-Desktop
  if ($Action -eq 'Start') { Run-Desktop @('start') -Open }
  $Timer.Start()
  [System.Windows.Forms.Application]::Run()
} finally {
  $Timer.Stop(); $Timer.Dispose()
  $Tray.Visible = $false
  $Tray.Icon.Dispose(); $Tray.Dispose(); $Menu.Dispose()
  $Mutex.ReleaseMutex(); $Mutex.Dispose()
}
