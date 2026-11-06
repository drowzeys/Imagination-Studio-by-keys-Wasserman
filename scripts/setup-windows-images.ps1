<#
.SYNOPSIS
  Safe, weights-only Windows image setup. Auto selects SD-Turbo, not unmeasured large models.
.DESCRIPTION
  Reuses healthy GPU/ComfyUI installations; installs only missing prerequisites using their existing pins/feed policy.
  No model render or qualification is started. Run the separate live image check after setup.
  Explicit native choices never become SD-Turbo. Matching failed shared tests require -AllowUnverifiedImageModel.
  Missing shared measurement metadata produces a warning, not a download block.
  The N1X 128 GB Qwen Turbo measurement is a reference, not a model minimum or a fit claim for other variants.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows-images.ps1 -Model auto
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows-images.ps1 -Model qwen -AllowUnverifiedImageModel
#>
[CmdletBinding()]
param(
  [ValidateSet('auto', 'sd-turbo', 'qwen', 'qwen-full', 'flux3k-fast', 'flux3k-full', 'moss-image')]
  [string]$Model = 'auto',
  [string]$Models = '',                   # native model folder; SD-Turbo uses STUDIO_WINDOWS_IMAGE_DIR or its default
  [switch]$AllowUnverifiedImageModel,
  [switch]$NoDownload,
  [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
$Selected = $Model
if ($Model -eq 'auto') { $Selected = 'sd-turbo' }
if ($PlanOnly) {
  @{ requested_model=$Model; selected_model=$Selected; native_fit='unknown; no hardware/runtime matching performed';
     why='Auto uses the bounded SD-Turbo fallback; RAM size alone does not qualify native image models.';
     prerequisites=@('GPU environment: install only if missing; otherwise offline verify',
                     'pinned ComfyUI: install only if missing; otherwise offline verify');
     native_toolchain=$false; qualification=$false; no_download=[bool]$NoDownload;
     native_download_requires_acknowledgment='Not probed. Only failed shared tests on matching hardware/capacity require an override.';
     cached_models='Complete existing native files bypass the download acknowledgment; no render qualification implied.' } |
    ConvertTo-Json -Compress
  exit 0
}
if ($env:OS -ne 'Windows_NT' -or $env:PROCESSOR_ARCHITECTURE -ne 'ARM64') {
  throw 'This bounded image installer requires native Windows ARM64.'
}
$Root = Split-Path $PSScriptRoot -Parent
$Home_ = Join-Path $env:LOCALAPPDATA 'ImaginationStudio'
$Gpu = Join-Path $Home_ 'gpu-venv\Scripts\python.exe'
$Helper = Join-Path $Root 'app\windows_image_setup.py'
if (-not $Models) { $Models = Join-Path $Home_ 'models' }
if ($Selected -eq 'sd-turbo') {
  if ($PSBoundParameters.ContainsKey('Models')) {
    throw '-Models is for native image models. For SD-Turbo, set STUDIO_WINDOWS_IMAGE_DIR for both setup and the Studio, or omit -Models to use the default.'
  }
}
$Python = $Gpu
$PythonArgs = @()
if (-not (Test-Path -LiteralPath $Python)) {
  if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Install the official Python install manager and Python 3.13 first; no models were downloaded.'
  }
  $Python = 'py'
  $PythonArgs = @('-V:3.13')
}
$Policy = @('-X', 'utf8', $Helper, '--model', $Model, '--models', $Models)
if ($NoDownload) { $Policy += '--no-download' }
if ($AllowUnverifiedImageModel) { $Policy += '--allow-unverified' }
$PlanText = & $Python @PythonArgs @Policy
if ($LASTEXITCODE -ne 0) { throw 'Image setup refused before downloads; see the model-fit/license message above.' }
$Plan = $PlanText | ConvertFrom-Json
Write-Host "$($Plan.selected_model): $($Plan.why)"
if ($Selected -eq 'sd-turbo' -and $Plan.download) {
  & $Python @PythonArgs -X utf8 $Helper --preflight-sd
  if ($LASTEXITCODE -ne 0) { throw 'SD-Turbo capacity preflight failed; no model download was started.' }
}
if ($Selected -ne 'sd-turbo' -and $Plan.download) {
  & $Python @PythonArgs -X utf8 $Helper --preflight-native
  if ($LASTEXITCODE -ne 0) { throw 'Native GPU inventory is unsupported; no model download was started.' }
}

if (Test-Path -LiteralPath $Gpu) {
  & $Gpu -X utf8 $Helper --check-runtime gpu
  if ($LASTEXITCODE -ne 0) { throw 'Existing GPU environment is unhealthy; it was not reinstalled. Repair it explicitly with setup-windows-gpu.ps1.' }
} else {
  & (Join-Path $PSScriptRoot 'setup-windows-gpu.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'GPU environment setup failed.' }
}
$Comfy = Join-Path $Home_ 'ComfyUI'
if (Test-Path -LiteralPath $Comfy) {
  & $Gpu -X utf8 $Helper --check-runtime comfy
  if ($LASTEXITCODE -ne 0) { throw 'Existing ComfyUI/runtime is unhealthy; it was not reinstalled or changed.' }
} else {
  & (Join-Path $PSScriptRoot 'setup-windows-comfy.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Pinned ComfyUI setup failed.' }
}

if ($Selected -eq 'sd-turbo') {
  $VerifyWeights = $NoDownload -or $Plan.model_files_complete
  & (Join-Path $PSScriptRoot 'setup-windows-storyboard.ps1') -Verify:$VerifyWeights
  if ($LASTEXITCODE -ne 0) { throw 'SD-Turbo weight setup failed.' }
} else {
  if (-not $NoDownload) {
    & $Gpu @Policy --download-model
    if ($LASTEXITCODE -ne 0) { throw 'Native image setup failed; fix the reported access/download error before retrying. Completed files are reused. No qualification was started.' }
  }
  & $Gpu -X utf8 (Join-Path $PSScriptRoot 'windows-cuda-setup.py') config --models $Models --cuda 'C:\cuda-13.4'
  if ($LASTEXITCODE -ne 0) { throw 'Native image model-path configuration failed.' }
}
Write-Host 'Image weight setup finished. Rendering was not qualified or started; run the separate live image check.'
