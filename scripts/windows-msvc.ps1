function Find-StudioVsWhere {
    foreach ($Base in (@($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Select-Object -Unique)) {
        if (-not $Base) { continue }
        $VsWhere = Join-Path $Base 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (Test-Path -LiteralPath $VsWhere) { return $VsWhere }
    }
    return ''
}

function Find-StudioArm64Compiler {
    $VsWhere = Find-StudioVsWhere
    if (-not $VsWhere) { return '' }
    $Places = @(& $VsWhere -all -products * -requires Microsoft.VisualStudio.Component.VC.Tools.ARM64 -property installationPath)
    if ($LASTEXITCODE -ne 0) { throw 'Visual Studio installation detection failed.' }
    foreach ($Place in $Places) {
        $Batch = Join-Path $Place 'VC\Auxiliary\Build\vcvarsall.bat'
        if (Test-Path -LiteralPath $Batch) { return $Batch }
    }
    return ''
}

function Install-StudioArm64Compiler {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'The ARM64 C++ compiler is missing. Install Windows Package Manager (winget), then rerun setup.'
    }
    Write-Host 'Installing Microsoft Visual Studio Build Tools: ARM64 C++ compiler and Windows SDK only.'
    & winget install -e --id Microsoft.VisualStudio.2022.BuildTools --accept-package-agreements --accept-source-agreements --disable-interactivity --override '--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.ARM64 --add Microsoft.VisualStudio.Component.Windows11SDK.26100' | Out-Host
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
        throw "The ARM64 compiler installation failed (exit $LASTEXITCODE). Complete any Windows approval prompt and rerun setup."
    }
}

function Initialize-StudioArm64Compiler {
    param([switch]$Verify)
    $Compiler = Find-StudioArm64Compiler
    if ($Compiler) { return $Compiler }
    if ($Verify) { throw 'The ARM64 C++ compiler is not installed; verification did not change the machine.' }
    Install-StudioArm64Compiler
    $Compiler = Find-StudioArm64Compiler
    if (-not $Compiler) { throw 'Visual Studio setup finished, but its ARM64 C++ component is still missing.' }
    return $Compiler
}
