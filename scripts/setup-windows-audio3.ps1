param([switch]$Verify, [switch]$DependenciesOnly)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Runtime = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$Environment = Join-Path $Runtime 'audio3-gpu-venv'
$Python = Join-Path $Environment 'Scripts\python.exe'
$Source = Join-Path $Runtime 'stable-audio-3-source'
$Revision = '790793b89ddceea612be9ce42ff6830d18f6fa57'
$Models = Join-Path $Runtime 'models\stable-audio-3-small-sfx'
if ($env:OS -ne 'Windows_NT') { throw 'This setup requires native Windows; it does not use WSL.' }
if ($Verify -and -not (Test-Path -LiteralPath $Python)) {
    throw 'The isolated Stable Audio 3 runtime is missing. Verification changed nothing.'
}
if (-not $Verify) {
    . (Join-Path $PSScriptRoot 'windows-python.ps1')
    Initialize-StudioPython -Runtime $Environment
    if (-not (Test-Path -LiteralPath (Join-Path $Source '.git'))) {
        & git clone --no-checkout https://github.com/Stability-AI/stable-audio-3.git $Source
        if ($LASTEXITCODE -ne 0) { throw 'Stable Audio 3 source download failed.' }
    }
    $Current = & git -C $Source rev-parse HEAD 2>$null
    if ($Current -ne $Revision -or -not (Test-Path -LiteralPath (Join-Path $Source 'pyproject.toml'))) {
        & git -C $Source fetch origin $Revision
        if ($LASTEXITCODE -ne 0) { throw 'Could not fetch the pinned Stable Audio 3 source.' }
        & git -C $Source checkout --detach $Revision
        if ($LASTEXITCODE -ne 0) { throw 'Could not select the pinned Stable Audio 3 source. Local source changes were not discarded.' }
    }
    $Patch = Join-Path $PSScriptRoot 'audio3-windows-dependencies.patch'
    # Windows PowerShell 5 treats the expected native stderr as a terminating error, even with 2>$null.
    $Probe = 'git -C "{0}" apply --reverse --check "{1}" 2>nul' -f $Source, $Patch
    & $env:ComSpec /d /s /c $Probe
    if ($LASTEXITCODE -ne 0) {
        & git -C $Source apply $Patch
        if ($LASTEXITCODE -ne 0) { throw 'The Windows Torch dependency compatibility patch failed.' }
    }
    Write-Host 'Installing Stable Audio 3 in its own native CUDA environment; existing GPU, Foley and UI packages are unchanged.'
    & $Python -X utf8 -m pip install --upgrade --only-binary=:all: -r (Join-Path $Root 'app\requirements-windows-audio3.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Stable Audio 3 dependencies failed to install.' }
    & $Python -X utf8 -m pip install --no-deps $Source
    if ($LASTEXITCODE -ne 0) { throw 'The pinned Stable Audio 3 package failed to install.' }
}
& $Python -X utf8 -m pip check
if ($LASTEXITCODE -ne 0) { throw 'The isolated Stable Audio 3 runtime has incompatible dependencies.' }
& $Python -X utf8 -c 'from stable_audio_3 import StableAudioModel; import torch; assert torch.version.cuda, "Native CUDA Torch is required"; print("Stable Audio 3 API imported; no model loaded.")'
if ($LASTEXITCODE -ne 0) { throw 'The Stable Audio 3 API could not load; no render was started.' }
if ($DependenciesOnly) { return }
Write-Host 'Stable Audio 3 Small SFX: Stability AI Community License (enterprise licence above US$1 million yearly revenue).'
Write-Host 'The T5Gemma encoder also requires Gemma Terms of Use. Accept any Hugging Face gates and sign in yourself.'
Write-Host 'Setup reports download bytes before fetching. No inference or qualification runs automatically.'
$Arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'windows-audio3-setup.py'), '--models', $Models)
if (-not $Verify) { $Arguments += '--download' }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Stable Audio 3 model setup/verification failed.' }
Write-Host 'Installed. Restart Studio and run Stable Audio 3 Small SFX in Machine > Feature checks, then select it in Sound effects.'
