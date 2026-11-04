<#
.SYNOPSIS
  The native Windows CUDA tier of Imagination Studio (an NVIDIA RTX Spark N1X, Windows 11 on Arm): the DGX Spark's
  render path natively, without WSL. Run by oneshot-setup-rtx-spark-n1x.ps1 -Native; it can be run again (it resumes).

.DESCRIPTION
  1. the build tools, when missing: Python 3.13 (ARM64), Git, Zig 0.17, the Visual Studio 2022 Build Tools with the
     ARM64 compiler (winget), and NVIDIA's CUDA 13.4 Windows-on-Arm toolkit from its redistributable archives
     (sha256-checked) into -CudaRoot;
  2. the app's environment (scripts\app.ps1 -Install), FFmpeg (scripts\setup-windows-ffmpeg.ps1), the GPU venv
     (scripts\setup-windows-gpu.ps1: NVIDIA's PyTorch for CUDA 13.4) and the pinned ComfyUI (scripts\setup-windows-comfy.ps1);
  3. TensorFold's engines from source, the Studio's fork branch studio-1.2.2 at a pinned commit: `zig build tf-h3-int8`
     (tf_h3.dll, the int8 H3 blocks ComfyUI's TensorFoldH3Blocks node calls) and, with -Bolt, `zig build native bolt`
     (tensorfold-native.exe + bolt.dll, the Bolt brain) and, with -Voice, `zig build fish-drama3-generate fish-drama3-lib`
     (drama3-generate.exe + tensorfold_drama3.dll, Fish Audio Drama 3) and, with the music part, `zig build music3-generate`
     (music3-generate.exe, MiniMax Music 3's own engine), in an ARM64 developer environment; the files go to
     %LOCALAPPDATA%\ImaginationStudio\tf-h3, ...\bolt\native\bin, ...\drama3\bin and ...\music3\bin, with the commit
     they were built from (a run at the same commit keeps them). No prebuilt release is published yet;
  4. the models (scripts\windows-cuda-setup.py models: the DGX Spark's files, from the same repos) and, for the parts that
     need them, the authors' ComfyUI node packs at their pinned commits (scripts\windows-cuda-setup.py nodes: workflows
     for the Library's actions, foley for MMAudio), musubi-tuner's environment (train: scripts\windows-cuda-setup.py
     train) and Tesseract (finish: scripts\setup-windows-finish.ps1);
  5. ComfyUI's model folders and cuda-tier.json, with the CUDA pool measured (scripts\windows-cuda-setup.py config):
     the Studio runs the native tier only when that record and its files are there.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows-cuda.ps1 -Bolt -Music
#>
[CmdletBinding()]
param(
  [string]$Models = "",                  # the model folder (default %LOCALAPPDATA%\ImaginationStudio\models)
  [string]$Parts = "video,image",         # windows-cuda-setup.py's parts: video, image, image-edit, ref2va, flux3, moss, music, sound,
                                          # workflows (the Library's actions), swap (Character Swap), foley, train, finish
  [switch]$Bolt,                          # the Bolt brain: build tensorfold-native.exe + bolt.dll and fetch NVIDIA's checkpoints
  [switch]$Music,
  [switch]$Flux3,
  [switch]$Sound,
  [switch]$Voice,                         # Fish Audio Drama 3: build drama3-generate.exe + tensorfold_drama3.dll, fetch fishaudio/drama-3
  [switch]$NoDownload,                    # no model downloads (the files are already in -Models)
  [switch]$SkipToolchain,
  [switch]$ToolchainOnly,                 # step 1 alone: the build tools and the CUDA toolkit, nothing else
  [switch]$SkipBuild,                     # keep the engines already in place
  [string]$EngineFrom = "",               # a folder holding tf_h3.dll (and tensorfold-native.exe, bolt.dll): copied, not built
  [string]$CudaRoot = "C:\cuda-13.4",
  [string]$CudaRelease = "13.4.1",
  [string]$TensorFoldRepo = "https://github.com/drowzeys/TensorFold.git",
  [string]$TensorFoldCommit = "1c8bdbfd7ea58fdb110870c3ff17d065522b65d2",  # branch studio-1.2.2
  [string]$BoltModel = "",                # an existing Bolt checkpoint folder instead of downloading one
  [string]$BoltDraft = ""
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Say($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  + $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  ! $text" -ForegroundColor Yellow }
function Native([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

if ($env:OS -ne "Windows_NT" -or $env:PROCESSOR_ARCHITECTURE -ne "ARM64") { throw "The native CUDA tier is for Windows on Arm (an RTX Spark N1X)." }
$Root = Split-Path $PSScriptRoot -Parent
$Home_ = Join-Path $env:LOCALAPPDATA "ImaginationStudio"
if (-not $Models) { $Models = Join-Path $Home_ "models" }
New-Item -ItemType Directory -Force -Path $Home_, $Models | Out-Null
$Gpu = Join-Path $Home_ "gpu-venv\Scripts\python.exe"
if ($Music) { $Parts += ",music" }
if ($Flux3) { $Parts += ",flux3" }
if ($Sound) { $Parts += ",sound" }
if ($Bolt -and -not $BoltModel) { $Parts += ",bolt" }
if ($Voice) { $Parts += ",voice" }

# ---------- 1. build tools ----------
function Winget([string]$id, [string[]]$more = @()) {
  & winget install -e --id $id --accept-package-agreements --accept-source-agreements --disable-interactivity @more
  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { throw "winget could not install $id (exit $LASTEXITCODE)" }  # 0x8A15002B: already installed
}
function VcVars {
  $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
  if (-not (Test-Path $vswhere)) { return "" }
  $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.ARM64 -property installationPath
  if (-not $vs) { return "" }
  $bat = Join-Path $vs "VC\Auxiliary\Build\vcvarsall.bat"
  if (Test-Path $bat) { return $bat } else { return "" }
}
if (-not $SkipToolchain) {
  Say "Build tools"
  & py -V:3.13-arm64 -c "1" 2>$null
  if ($LASTEXITCODE -ne 0) { Winget "Python.Python.3.13" @("--architecture", "arm64") } else { Ok "Python 3.13 (ARM64)" }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget "Git.Git" } else { Ok "Git" }
  $zig = (& zig version 2>$null)
  if ($zig -notmatch "^0\.17") { Winget "zig.zig" @("--version", "0.17.0") } else { Ok "Zig $zig" }
  if (-not (VcVars)) {
    Winget "Microsoft.VisualStudio.2022.BuildTools" @("--override", "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.ARM64 --add Microsoft.VisualStudio.Component.Windows11SDK.26100 --includeRecommended")
  } else { Ok "Visual Studio Build Tools (ARM64 compiler)" }
  # NVIDIA's CUDA toolkit for Windows on Arm, from its redistributable archives: the compiler and the libraries the
  # engines build and run against, each archive checked against NVIDIA's own manifest
  if (-not (Test-Path (Join-Path $CudaRoot "bin\nvcc.exe"))) {
    $base = "https://developer.download.nvidia.com/compute/cuda/redist"
    $manifest = Invoke-RestMethod "$base/redistrib_$CudaRelease.json"
    $work = Join-Path $env:TEMP "cuda-redist-$CudaRelease"
    New-Item -ItemType Directory -Force -Path $work, $CudaRoot | Out-Null
    foreach ($name in @("cuda_nvcc", "cuda_cudart", "cuda_crt", "cccl", "libnvvm", "libcublas", "cuda_nvrtc", "libnvjitlink")) {
      $entry = $manifest.$name."windows-arm64"
      if (-not $entry) { throw "NVIDIA's $CudaRelease manifest has no windows-arm64 $name" }
      $zip = Join-Path $work (Split-Path $entry.relative_path -Leaf)
      if (-not (Test-Path $zip) -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne $entry.sha256.ToUpper()) {
        Invoke-WebRequest "$base/$($entry.relative_path)" -OutFile $zip
      }
      if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $entry.sha256.ToUpper()) { throw "$name did not match NVIDIA's sha256" }
      $out = Join-Path $work $name
      Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
      Expand-Archive $zip -DestinationPath $out
      $top = Get-ChildItem $out -Directory | Select-Object -First 1
      Copy-Item (Join-Path $top.FullName "*") $CudaRoot -Recurse -Force
      Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
      Ok "CUDA $CudaRelease $name"
    }
    # the archives and their expanded copies (about 3.5 GB) are not kept: a run that finds nvcc never fetches them again
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
  } else { Ok "CUDA toolkit at $CudaRoot" }
}

if ($ToolchainOnly) { Ok "build tools ready (-ToolchainOnly: nothing else installed)"; exit 0 }

# ---------- 2. the app, FFmpeg, the GPU venv and ComfyUI ----------
Say "The Studio's environments"
& (Join-Path $PSScriptRoot "app.ps1") -Install -InstallOnly
& (Join-Path $PSScriptRoot "setup-windows-ffmpeg.ps1")
& (Join-Path $PSScriptRoot "setup-windows-gpu.ps1")
# a ComfyUI already at the pin is kept as it is: the tier's own files in it (custom_nodes\tensorfold_h3,
# extra_model_paths.yaml) are untracked, which setup-windows-comfy.ps1's first-install check counts as changes
$Comfy = Join-Path $Home_ "ComfyUI"
$Pinned = (Test-Path (Join-Path $Comfy ".git")) -and ((& git -C $Comfy rev-parse HEAD) -eq "b9b290e43f1eb379946dfeb013faf9bbefa1a618") -and -not (& git -C $Comfy status --porcelain --untracked-files=no)
if ($Pinned) { Ok "ComfyUI at its pin (b9b290e4)" } else { & (Join-Path $PSScriptRoot "setup-windows-comfy.ps1") }
& $Gpu -X utf8 -m pip install --only-binary=:all: "huggingface_hub>=0.30"
Native "huggingface_hub in the GPU venv"
Ok "app venv, FFmpeg, GPU venv (PyTorch, CUDA 13.4), ComfyUI"

# ---------- 3. TensorFold's engines ----------
$TfH3 = Join-Path $Home_ "tf-h3"
$BoltBin = Join-Path $Home_ "bolt\native\bin"
$DramaBin = Join-Path $Home_ "drama3\bin"
$MusicBin = Join-Path $Home_ "music3\bin"
$MusicEngine = ($Parts -split "," | ForEach-Object { $_.Trim() }) -contains "music"
New-Item -ItemType Directory -Force -Path $TfH3, $BoltBin, $DramaBin, $MusicBin | Out-Null
if ($EngineFrom) {
  Say "TensorFold's engines from $EngineFrom"
  Copy-Item (Join-Path $EngineFrom "tf_h3.dll") $TfH3 -Force
  foreach ($one in @("tensorfold-native.exe", "bolt.dll")) { if (Test-Path (Join-Path $EngineFrom $one)) { Copy-Item (Join-Path $EngineFrom $one) $BoltBin -Force } }
  foreach ($one in @("drama3-generate.exe", "tensorfold_drama3.dll")) { if (Test-Path (Join-Path $EngineFrom $one)) { Copy-Item (Join-Path $EngineFrom $one) $DramaBin -Force } }
  if (Test-Path (Join-Path $EngineFrom "music3-generate.exe")) { Copy-Item (Join-Path $EngineFrom "music3-generate.exe") $MusicBin -Force }
} elseif (-not $SkipBuild -or -not (Test-Path (Join-Path $TfH3 "tf_h3.dll"))) {
  Say "TensorFold's engines from source ($TensorFoldRepo @ $($TensorFoldCommit.Substring(0, 8)))"
  $src = Join-Path $Home_ "src\TensorFold"
  if (-not (Test-Path (Join-Path $src ".git"))) {
    & git init --quiet $src; Native "git init"
    & git -C $src remote add origin $TensorFoldRepo; Native "git remote"
  }
  & git -C $src fetch --depth 1 origin $TensorFoldCommit; Native "fetching TensorFold"
  & git -C $src -c advice.detachedHead=false checkout --force --detach FETCH_HEAD; Native "checking out TensorFold"
  $vc = VcVars
  if (-not $vc) { throw "no Visual Studio ARM64 compiler (vcvarsall.bat): run without -SkipToolchain" }
  $nvcc = Join-Path $CudaRoot "bin\nvcc.exe"
  # an engine already built from this commit is kept (its folder's commit file says which)
  function Built([string]$folder, [string]$file) {
    (Test-Path (Join-Path $folder $file)) -and ((Get-Content (Join-Path $folder "commit") -ErrorAction SilentlyContinue) -eq $TensorFoldCommit)
  }
  $steps = @()
  if (-not (Built $TfH3 "tf_h3.dll")) { $steps += "zig build tf-h3-int8 -Dsm=121 -Dnvcc=`"$nvcc`"" }
  if ($Voice -and -not (Built $DramaBin "drama3-generate.exe")) { $steps += "zig build fish-drama3-generate fish-drama3-lib -Dsm=121 -Dnvcc=`"$nvcc`"" }
  if ($Bolt -and -not (Built $BoltBin "tensorfold-native.exe")) { $steps += "zig build native bolt -Dsm=121 -Doptimize=ReleaseFast -Dstrip=true -Dnvcc=`"$nvcc`" -Dbolt-src=`"$src\zig\bolt-cuda`"" }
  if ($MusicEngine -and -not (Built $MusicBin "music3-generate.exe")) { $steps += "zig build music3-generate -Dsm=121 -Doptimize=ReleaseFast -Dstrip=true -Dnvcc=`"$nvcc`"" }
  # each build in a .cmd file of its own (cmd /c strips the quotes of a one-line command that starts with one, and the
  # ARM64 developer environment then never loads)
  $bat = Join-Path $env:TEMP "studio-tensorfold-build.cmd"
  foreach ($step in $steps) {
    @("@echo off", "call `"$vc`" arm64 >nul", "set CUDA_PATH=$CudaRoot", "set PATH=$CudaRoot\bin;%PATH%",
      "set LIB=$CudaRoot\lib\arm64;%LIB%", "cd /d `"$src`"", $step) | Set-Content -Path $bat -Encoding ascii
    & cmd.exe /d /c $bat
    Native $step
  }
  foreach ($one in @(@("zig build tf-h3", "zig-out\h3-int8\tf_h3.dll", $TfH3), @("zig build fish-drama3", "zig-out\fish-drama3\bin\*", $DramaBin),
                     @("zig build native bolt", "zig-out\native\bin\*", $BoltBin), @("zig build music3", "zig-out\bin\music3-generate.exe", $MusicBin))) {
    if ($steps | Where-Object { $_.StartsWith($one[0]) }) {
      Copy-Item (Join-Path $src $one[1]) $one[2] -Force
      Set-Content -Path (Join-Path $one[2] "commit") -Value $TensorFoldCommit -Encoding ascii
    }
  }
}
if (-not (Test-Path (Join-Path $TfH3 "tf_h3.dll"))) { throw "no tf_h3.dll in $TfH3" }
Ok "H3 int8 blocks: $TfH3\tf_h3.dll"
if (Test-Path (Join-Path $BoltBin "tensorfold-native.exe")) { Ok "Bolt: $BoltBin\tensorfold-native.exe + bolt.dll" }
if (Test-Path (Join-Path $DramaBin "drama3-generate.exe")) { Ok "Drama 3: $DramaBin\drama3-generate.exe + tensorfold_drama3.dll" }
if (Test-Path (Join-Path $MusicBin "music3-generate.exe")) { Ok "MiniMax Music 3: $MusicBin\music3-generate.exe" }

# ---------- 4. the models ----------
if (-not $NoDownload) {
  Say "Models -> $Models ($Parts)"
  & $Gpu -X utf8 (Join-Path $Root "scripts\windows-cuda-setup.py") models --models $Models --parts $Parts
  if ($LASTEXITCODE -ne 0) { Warn "some files did not download (above): run again to resume; a gated model needs its licence accepted with your Hugging Face account" }
}

$Want = $Parts -split "," | ForEach-Object { $_.Trim() }
$Nodes = @($Want | Where-Object { $_ -in @("workflows", "foley") })
if ($Nodes) {
  Say "The authors' ComfyUI node packs ($($Nodes -join ', '))"
  & $Gpu -X utf8 (Join-Path $Root "scripts\windows-cuda-setup.py") nodes --parts ($Nodes -join ",")
  Native "the node packs"
}
if ($Want -contains "train") {
  Say "musubi-tuner's environment (Looks -> Train a LoRA)"
  & $Gpu -X utf8 (Join-Path $Root "scripts\windows-cuda-setup.py") train
  Native "the training environment"
}
if ($Want -contains "finish") {
  & (Join-Path $PSScriptRoot "setup-windows-finish.ps1")
}

# ---------- 5. ComfyUI's model folders and the tier's record ----------
Say "The native CUDA tier's record"
$cfg = @("-X", "utf8", (Join-Path $Root "scripts\windows-cuda-setup.py"), "config", "--models", $Models, "--cuda", $CudaRoot)
if ($BoltModel) { $cfg += @("--bolt-model", $BoltModel) }
if ($BoltDraft) { $cfg += @("--bolt-draft", $BoltDraft) }
& $Gpu @cfg
Native "writing cuda-tier.json"
Ok "done: scripts\app.ps1 starts the Studio on the native CUDA tier"
