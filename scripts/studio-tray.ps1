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
function ConvertTo-DesktopArgument([string]$Value) {
  '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}
function Invoke-Desktop([string[]]$Arguments) {
  $Info = New-Object System.Diagnostics.ProcessStartInfo
  $Info.FileName = $Python
  $Info.Arguments = ((@($PythonArgs) + @('-X','utf8',$Controller) + @($Arguments)) |
    ForEach-Object { ConvertTo-DesktopArgument $_ }) -join ' '
  $Info.UseShellExecute = $false
  $Info.CreateNoWindow = $true
  $Info.RedirectStandardOutput = $true
  $Info.RedirectStandardError = $true
  $Process = New-Object System.Diagnostics.Process
  $Process.StartInfo = $Info
  try {
    if (-not $Process.Start()) { throw 'The desktop controller could not start.' }
    $Output = $Process.StandardOutput.ReadToEndAsync()
    $Errors = $Process.StandardError.ReadToEndAsync()
    if (-not $Process.WaitForExit(240000)) {
      $Process.Kill()
      throw 'The desktop operation exceeded its deadline. Check tray status before trying again.'
    }
    $Text = $Output.GetAwaiter().GetResult()
    $ErrorText = $Errors.GetAwaiter().GetResult()
    if (-not $Text.Trim()) {
      throw "The desktop controller returned no result (exit $($Process.ExitCode)). $ErrorText"
    }
    $Result = $Text | ConvertFrom-Json
    if (-not $Result.ok) {
      if ($Result.error) { throw $Result.error }
      throw "The desktop controller returned an invalid result (exit $($Process.ExitCode))."
    }
    if ($Process.ExitCode -ne 0) { throw "The desktop controller failed (exit $($Process.ExitCode)). $ErrorText" }
    return $Result
  } finally { $Process.Dispose() }
}
function Show-Problem($Message) {
  Add-Content -LiteralPath (Join-Path $Home_ 'desktop-tray.log') -Value ("{0:o} {1}" -f (Get-Date), [string]$Message)
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
function Run-Desktop([string[]]$Arguments, [switch]$Open) {
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
  $Names = @{native='Native Windows'; external='External Studio'}
  $Running = if (-not $S.running) { 'Stopped' } elseif ($Names.ContainsKey($S.running)) { $Names[$S.running] } else { $S.running }
  Add-Menu "Studio: $Running" $null $false
  if ($script:RefreshError) {
    Add-Menu $script:RefreshError $null $false
  } elseif ($S.detail) {
    Add-Menu $S.detail $null $false
  }
  $Tray.Text = "Imagination Studio - $Running"
  Add-Menu 'Open Studio' { Start-Process 'http://127.0.0.1:7870/' } ([bool]$S.running)
  foreach ($Kind in @('native')) {
    $Value = $Kind
    $Name = $Names[$Kind]
    $Installed = $S.backends.$Kind.available
    $Label = if (-not $Installed) { "$Name - not registered or unavailable" }
      elseif ($S.running -eq $Kind) { "$Name - running" }
      else { "Start $Name" }
    $StartHandler = { Run-Desktop @('start','--backend',$Value) -Open }.GetNewClosure()
    Add-Menu $Label $StartHandler ([bool]$Installed -and -not $script:Busy -and -not $S.running)
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
