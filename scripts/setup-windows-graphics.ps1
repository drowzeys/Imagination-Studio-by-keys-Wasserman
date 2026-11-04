param([string]$NodePath, [string]$BrowserPath)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run scripts\app.ps1 -Install first.' }
. (Join-Path $PSScriptRoot 'windows-node.ps1')
$NodePath = Initialize-StudioNode -NodePath $NodePath
$NodeInfo = & $NodePath -p 'JSON.stringify({platform:process.platform,arch:process.arch,version:process.versions.node})'
if ($LASTEXITCODE -ne 0) { throw "Node did not start. $StudioNodeHelp" }
$NodeInfo = $NodeInfo | ConvertFrom-Json
if ($NodeInfo.platform -ne 'win32' -or $NodeInfo.arch -ne 'arm64') { throw "Graphics setup requires native Windows ARM64 Node. $StudioNodeHelp" }
& $Python -X utf8 -c 'import sys; sys.path.insert(0,sys.argv[1]); import graphics; sys.exit(0 if graphics.node_safe(tuple(map(int,sys.argv[2].split(chr(46))))) else 1)' (Join-Path $Root 'app') $NodeInfo.version
if ($LASTEXITCODE -ne 0) { throw "Node is missing required security fixes; no npm packages were run. $StudioNodeHelp" }
$Npm = Join-Path (Split-Path $NodePath -Parent) 'node_modules\npm\bin\npm-cli.js'
if (-not (Test-Path -LiteralPath $Npm)) { throw "npm is missing beside Node. $StudioNodeHelp" }
if (-not $BrowserPath) {
    foreach ($Candidate in @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
    )) {
        if (Test-Path -LiteralPath $Candidate) { $BrowserPath = $Candidate; break }
    }
}
if (-not $BrowserPath -or -not (Test-Path -LiteralPath $BrowserPath)) { throw 'Install Microsoft Edge, then rerun scripts\setup-windows-graphics.ps1, or pass -BrowserPath to an existing Chromium executable. No browser is downloaded by this script.' }
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\graphics'
if ($env:GRAPHICS_DIR) { $Runtime = $env:GRAPHICS_DIR }
New-Item -ItemType Directory -Force $Runtime | Out-Null
Write-Host 'HyperFrames 0.8.186 and GSAP 3.14.2: small npm dependencies only; no models or browsers are downloaded.'
Copy-Item (Join-Path $PSScriptRoot 'windows-graphics\package.json'),(Join-Path $PSScriptRoot 'windows-graphics\package-lock.json') -Destination $Runtime
$env:HYPERFRAMES_NO_TELEMETRY = '1'
$env:HYPERFRAMES_NO_UPDATE_CHECK = '1'
$env:HYPERFRAMES_SKIP_SKILLS = '1'
Push-Location $Runtime
try {
    & $NodePath $Npm ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
        throw @'
Graphics dependency installation failed; see the npm error above.
If using a private registry, check authentication and package availability.
Then rerun .\scripts\setup-windows-graphics.ps1 with the same arguments.
'@
    }
} finally { Pop-Location }
$Hyperframes = Join-Path $Runtime 'node_modules\hyperframes'
$BrowserPatch = Join-Path $PSScriptRoot 'windows-graphics\hyperframes-browser.patch'
& git -C $Hyperframes apply --check -- $BrowserPatch
if ($LASTEXITCODE -ne 0) { throw 'The Windows browser-probe compatibility patch does not match the pinned HyperFrames build.' }
& git -C $Hyperframes apply -- $BrowserPatch
if ($LASTEXITCODE -ne 0) { throw 'The Windows browser-probe compatibility patch failed.' }
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $Runtime 'node-path'), [IO.Path]::GetFullPath($NodePath), $Utf8)
[IO.File]::WriteAllText((Join-Path $Runtime 'browser-path'), [IO.Path]::GetFullPath($BrowserPath), $Utf8)
& $Python -X utf8 (Join-Path $PSScriptRoot 'windows-graphics-check.py')
if ($LASTEXITCODE -ne 0) { throw 'Windows graphics check failed; the route remains disabled.' }
