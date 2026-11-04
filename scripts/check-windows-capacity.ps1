param(
    [ValidateRange(0, 255)]
    [int]$DeviceIndex = 0,
    [string]$Output
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This check is for native Windows only.' }
if ($Output -and (Test-Path -LiteralPath $Output)) { throw 'The report already exists; choose a new output filename.' }
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup-windows-gpu.ps1 first.' }
Write-Host 'Capacity inventory and tiny CUDA operations only; no models, servers or changed memory limits.'
$Probe = & $Python -X utf8 (Join-Path $PSScriptRoot 'windows-gpu-check.py') --device-index $DeviceIndex
if ($LASTEXITCODE -ne 0) { throw 'CUDA verification failed; no capacity report was written.' }
$Gpu = & nvidia-smi "--id=$DeviceIndex" --query-gpu=driver_version,memory.total,memory.free --format=csv,noheader,nounits
if ($LASTEXITCODE -ne 0) { throw 'The dedicated GPU budget could not be queried.' }
$Gpu = $Gpu | ConvertFrom-Csv -Header driver_version,total_mb,free_mb
$Memory = Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum
$Windows = Get-CimInstance Win32_OperatingSystem
$Report = [ordered]@{
    observed_at = (Get-Date).ToUniversalTime().ToString('o')
    scope = 'Capacity snapshot and tiny CUDA checks; not model qualification. Host and GPU budgets overlap.'
    physical_total_mb = [long][math]::Floor($Memory.Sum / 1MB)
    host_total_mb = [long][math]::Floor($Windows.TotalVisibleMemorySize / 1024)
    host_free_mb = [long][math]::Floor($Windows.FreePhysicalMemory / 1024)
    windows_version = $Windows.Version
    windows_build = $Windows.BuildNumber
    driver_version = $Gpu.driver_version.Trim()
    gpu_dedicated_total_mb = [int]$Gpu.total_mb
    gpu_dedicated_free_mb = [int]$Gpu.free_mb
    cuda = (($Probe -join "`n") | ConvertFrom-Json)
}
$Json = $Report | ConvertTo-Json -Depth 6
if ($Output) {
    $Utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output), $Json, $Utf8)
}
$Json
