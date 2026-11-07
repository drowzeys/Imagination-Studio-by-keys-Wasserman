# Rust (rustup, per user) and LLVM's libclang for what the Studio builds from source on Windows: Foley's tiktoken
# (no ARM64 wheel) and Tesseract Converter (scripts\setup-windows-finish.ps1, whose FFmpeg bindings bindgen makes with
# libclang). Dot-source it, then call Initialize-StudioRust and, for bindgen, Initialize-StudioLibclang.

function Update-StudioRustPath {
    $Cargo = Join-Path $env:USERPROFILE '.cargo\bin'
    $Places = @($Cargo, $env:PATH,
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))
    $env:PATH = ($Places | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Install-StudioWinget([string]$Id, [string[]]$More = @()) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Id is missing. Install Windows Package Manager (winget), then rerun setup."
    }
    & winget install -e --id $Id --accept-package-agreements --accept-source-agreements --disable-interactivity @More | Out-Host
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { throw "winget could not install $Id (exit $LASTEXITCODE)" }  # 0x8A15002B: already installed
}

function Initialize-StudioRust {
    param([switch]$Verify)
    Update-StudioRustPath
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        if ($Verify) { throw 'Rust (rustup) is not installed; verification did not change the machine.' }
        Install-StudioWinget 'Rustlang.Rustup'
        Update-StudioRustPath
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
            throw 'rustup was installed, but cargo is not available in this terminal. Open a new PowerShell window and rerun setup.'
        }
    }
    $Previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $Said = (& cargo --version 2>$null | Select-Object -First 1)
    $ErrorActionPreference = $Previous
    if (-not $Said) {
        if ($Verify) { throw 'rustup has no default toolchain; verification did not change the machine.' }
        & rustup default stable 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "rustup could not install the stable toolchain (exit $LASTEXITCODE)" }
        $Said = (& cargo --version | Select-Object -First 1)
    }
    return [string]$Said
}

function Initialize-StudioLibclang {
    param([switch]$Verify)
    $Bin = Join-Path $env:ProgramFiles 'LLVM\bin'
    if (-not (Test-Path -LiteralPath (Join-Path $Bin 'libclang.dll'))) {
        if ($Verify) { throw "LLVM's libclang is not installed; verification did not change the machine." }
        $Arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
        Install-StudioWinget 'LLVM.LLVM' @('--architecture', $Arch)
        if (-not (Test-Path -LiteralPath (Join-Path $Bin 'libclang.dll'))) { throw "LLVM was installed, but $Bin\libclang.dll is missing." }
    }
    $env:LIBCLANG_PATH = $Bin
    return $Bin
}
