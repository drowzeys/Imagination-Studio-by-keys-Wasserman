function Initialize-StudioPython {
    param(
        [Parameter(Mandatory = $true)][string]$Runtime,
        [switch]$Arm64
    )

    $Python = Join-Path $Runtime 'Scripts\python.exe'
    if (Test-Path -LiteralPath $Python) { return }
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Install the official Python install manager, then rerun setup. The py command is required: https://www.python.org/downloads/windows/'
    }

    $Version = '3.13'
    if ($Arm64 -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        $Version = '3.13-arm64'
    }
    & py "-V:$Version" -c "import sys; sys.exit(0)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing Python $Version with the official Python install manager."
        & py install $Version
        if ($LASTEXITCODE -ne 0) {
            throw "Python $Version installation failed. Update the official Python install manager and rerun setup; legacy py launchers cannot install runtimes."
        }
    }
    & py "-V:$Version" -m venv $Runtime
    if ($LASTEXITCODE -ne 0) { throw "Could not create the isolated Python $Version environment." }
}
