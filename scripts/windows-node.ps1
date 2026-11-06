$StudioNodeHelp = 'Install the current Windows ARM64 Node.js LTS with npm from https://nodejs.org/en/download, then rerun scripts\setup-windows-graphics.ps1. For an existing installation, pass -NodePath to its node.exe.'

function Find-StudioNode {
    $Private = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\node-runtime\node-v22.23.4-win-arm64\node.exe'
    if (Test-Path -LiteralPath $Private) { return $Private }
    $Found = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($Found) { return $Found.Source }
    foreach ($Candidate in @(
        (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe')
    )) {
        if (Test-Path -LiteralPath $Candidate) { return $Candidate }
    }
}

function Initialize-StudioNode {
    param([string]$NodePath)

    if ($NodePath) {
        if (-not (Test-Path -LiteralPath $NodePath)) { throw $StudioNodeHelp }
        return $NodePath
    }
    $NodePath = Find-StudioNode
    if ($NodePath) { return $NodePath }
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) { throw "Node.js and winget were not found. $StudioNodeHelp" }
    Write-Host 'Installing Windows ARM64 Node.js LTS with npm through winget (OpenJS.NodeJS.LTS).'
    & $Winget.Source install --id OpenJS.NodeJS.LTS --exact --architecture arm64 --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "winget could not install Node.js (exit $LASTEXITCODE). $StudioNodeHelp" }
    $NodePath = Find-StudioNode
    if (-not $NodePath) { throw "winget finished, but node.exe was not found. $StudioNodeHelp" }
    return $NodePath
}
