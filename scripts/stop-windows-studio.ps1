# Stop the native Windows Studio (scripts\app.ps1 on the native CUDA tier) and its ComfyUI, giving the GPU's memory back.
# The server is stopped by the process id it recorded (STUDIO_PIDFILE) and only while that process is still the
# Studio's server (its command line names app\server.py); the Bolt engine it started ends with it (its job object).
# ComfyUI is stopped by spark\comfy_win.py, by the identity it recorded itself. Nothing is stopped by name.
$H = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$PidFile = Join-Path $H 'studio.pid'
if (Test-Path $PidFile) {
    $Id = 0
    [void][int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$Id)
    $Found = if ($Id) { Get-CimInstance Win32_Process -Filter "ProcessId = $Id" -ErrorAction SilentlyContinue } else { $null }
    if ($Found -and $Found.CommandLine -match 'app\\server\.py') {
        Stop-Process -Id $Id -Force
        Write-Host 'Imagination Studio stopped.'
    } else {
        Write-Host 'Imagination Studio is not running.'
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
$Gpu = Join-Path $H 'gpu-venv\Scripts\python.exe'
if (Test-Path $Gpu) { & $Gpu -X utf8 (Join-Path (Split-Path $PSScriptRoot -Parent) 'spark\comfy_win.py') stop }
