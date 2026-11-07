<#
.SYNOPSIS
  Finish (app/finish.py) natively on Windows: Tesseract's command-line renderer, Tesseract Converter and the two fonts
  the title card and captions use, as scripts/setup-finish.sh installs them on a Mac or Linux x86_64.

.DESCRIPTION
  Under %LOCALAPPDATA%\ImaginationStudio\finish (the native CUDA tier's PREFIX) it adds:
    tesseract\   Tesseract's pinned release from github.com/mirage-hq/tesseract (tsrct.exe and its FFmpeg DLLs)
    converter\   Tesseract Converter (MIT, github.com/mirage-hq/Tesseract-Converter), built here from its pinned source
                 with one fix, natively for Windows on Arm, beside FFmpeg's LGPL DLLs
    fonts\       Instrument Serif and Geist (SIL Open Font License)
  Tesseract's release is built for windows-x86_64 only: on Windows on Arm (an RTX Spark N1X) it runs through Windows'
  own x64 emulation, which Windows 11 includes. The converter's release is too, and on Windows it refuses every
  Premiere Pro project that links an After Effects composition (a path check that compares "/" with "\\":
  mirage-hq/Tesseract-Converter#6), so the converter is built from source at the release's commit with that check
  fixed. The build needs Rust (rustup) and LLVM's libclang, which this installs with winget when they are missing, the
  Visual Studio Build Tools' ARM64 compiler, and FFmpeg 8.1's LGPL shared build (BtbN, pinned by SHA-256). It takes
  about 3 minutes; if it fails, the release is installed instead (After Effects and Resolve work; Premiere waits). Each download is checked against the SHA-256 pinned here, and every file in
  it against the bundle's own list, before anything is installed. Tesseract's own install.ps1 is not used: it refuses
  an ARM64 machine (it compares the bundle's architecture with the processor's), though the program runs there.
  Licences: Tesseract is free to download and use but not open source: Mirage's own terms (TERMS.md, beside it) leave
  out commercial use by a business with a yearly revenue of 1 M USD or more without a separate agreement, and do not
  allow passing the program on. The converter is MIT. Tesseract may send basic usage counts (no file names, paths or
  contents); this script never changes that setting (tesseract\bin\tsrct.exe telemetry disable turns it off).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows-finish.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows-finish.ps1 -Verify
#>
[CmdletBinding()]
param(
  [switch]$Verify,
  [string]$Prefix = ""
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Ok($text) { Write-Host "  + $text" -ForegroundColor Green }
function Note($text) { Write-Host "  ! $text" -ForegroundColor Yellow }
if ($env:OS -ne "Windows_NT") { throw "This setup is for Windows: scripts/setup-finish.sh installs Finish elsewhere." }
if (-not $Prefix) { $Prefix = if ($env:PREFIX) { $env:PREFIX } else { Join-Path $env:LOCALAPPDATA "ImaginationStudio" } }
$Dest = Join-Path $Prefix "finish"

$TsrctVersion = "0.4.0"       # app/finish.py TSRCT_VERSION (fxSchemaVersion 77)
$TsrctSha = "a5673f2a246e72ca108e5fd852a4c5c109a24763653216e9b8e88757817fcdf6"
$ConvVersion = "0.3.0"        # app/finish.py CONVERTER_VERSION
$ConvSha = "91d9f91bcdd6090518c221f056916639830db375ca779a11fcc3b3e204c7b795"
$ConvCommit = "2097c8c5a052160b35b4c9ea160362cb6cd0e4f6"    # v0.3.0, as the release's build.json records
$ConvFix = "windows-aep-path"  # app/finish.py CONVERTER_FIX: the build's record (converter\studio-build.json) names it
# FFmpeg 8.1 for the converter's media inspection: BtbN's LGPL shared build of the release app/windows_media.py pins
$FfVersion = "n8.1.3-27-g8dca1586f4"
$FfArchive = "ffmpeg-$FfVersion-winarm64-lgpl-shared-8.1.zip"
$FfUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/$FfArchive"
$FfSha = "7cc09ffd85f4f74609564f157d037b202cf91f9d3a349ef42e3f72ee19277320"
$FontsCommit = "45e3d5269e4a17d823f6d84470bc084dc5ae46f6"    # google/fonts, as scripts/setup-finish.sh
$Fonts = @(@("instrumentserif/InstrumentSerif-Regular.ttf", "InstrumentSerif-Regular.ttf", "498efd461f6ddfcb7a111bf9a565709d2085d48201d501ead960d93e84ffbb88"),
           @("geist/Geist%5Bwght%5D.ttf", "Geist[wght].ttf", "73894e0448cae90a92b6c2f8732b7bb9acb7b94c418bff559dad4a18e1de9659"))

function Sha256([string]$path) {
  $stream = [IO.File]::OpenRead($path)
  try { return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stream)) -replace "-", "").ToLower() }
  finally { $stream.Dispose() }
}
function VersionOf([string]$exe) {
  if (-not (Test-Path -LiteralPath $exe)) { return "" }
  $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  $said = (& $exe --version 2>$null | Select-Object -First 1)
  $ErrorActionPreference = $previous
  if ($said) { return ([string]$said).Split(" ")[1] } else { return "" }
}
# a release zip into a fresh folder, checked against its pinned SHA-256 first
function Fetch([string]$url, [string]$sha, [string]$scratch) {
  $zip = Join-Path $scratch (Split-Path $url -Leaf)
  Invoke-WebRequest -UseBasicParsing $url -OutFile $zip
  if ((Sha256 $zip) -ne $sha) { throw "$(Split-Path $url -Leaf) does not match its pinned SHA-256: not installed" }
  Ok "checksum of $(Split-Path $url -Leaf)"
  $out = Join-Path $scratch "x"
  Expand-Archive -LiteralPath $zip -DestinationPath $out -Force
  return (Get-ChildItem -LiteralPath $out -Directory | Select-Object -First 1).FullName
}

Write-Host "Finish (Tesseract $TsrctVersion, Tesseract Converter $ConvVersion)"
New-Item -ItemType Directory -Force -Path (Join-Path $Dest "fonts") | Out-Null

# ---- the fonts
foreach ($font in $Fonts) {
  $target = Join-Path $Dest "fonts\$($font[1])"
  if ((Test-Path -LiteralPath $target) -and (Sha256 $target) -eq $font[2]) { Ok "font: $($font[1])"; continue }
  if ($Verify) { Note "font $($font[1]) is not installed"; continue }
  $part = Join-Path $Dest "fonts\download.part"  # a name without brackets: -OutFile reads [wght] as a wildcard
  Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/google/fonts/$FontsCommit/ofl/$($font[0])" -OutFile $part
  if ((Sha256 $part) -ne $font[2]) { Remove-Item -LiteralPath $part -Force; throw "font $($font[1]) does not match its checksum: not installed" }
  Move-Item -LiteralPath $part -Destination $target -Force
  $family = $font[0].Split("/")[0]
  try { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/google/fonts/$FontsCommit/ofl/$family/OFL.txt" -OutFile (Join-Path $Dest "fonts\OFL-$($font[1].Split('.')[0]).txt") } catch {}
  Ok "font: $($font[1]) (SIL Open Font License)"
}

# ---- Tesseract
$Tsrct = Join-Path $Dest "tesseract\bin\tsrct.exe"
if ((VersionOf $Tsrct) -eq $TsrctVersion) { Ok "Tesseract ${TsrctVersion}: $Tsrct" }
elseif ($Verify) { Note "Tesseract is not installed under $Dest" }
else {
  $scratch = Join-Path $env:TEMP ("studio-finish-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
  New-Item -ItemType Directory -Force -Path $scratch | Out-Null
  try {
    Write-Host "  downloading tesseract-$TsrctVersion-windows-x86_64.zip (about 115 MB)"
    $bundle = Fetch "https://github.com/mirage-hq/tesseract/releases/download/v$TsrctVersion/tesseract-$TsrctVersion-windows-x86_64.zip" $TsrctSha $scratch
    # every file against the bundle's own CHECKSUMS.sha256
    $bad = 0
    foreach ($line in Get-Content -LiteralPath (Join-Path $bundle "CHECKSUMS.sha256")) {
      if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { throw "malformed line in the bundle's CHECKSUMS.sha256: $line" }
      $file = Join-Path $bundle ($Matches[2] -replace "/", "\")
      if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Sha256 $file) -ne $Matches[1]) { $bad++ }
    }
    if ($bad) { throw "$bad file(s) of the Tesseract bundle do not match its checksums: not installed" }
    if ((VersionOf (Join-Path $bundle "bin\tsrct.exe")) -ne $TsrctVersion) { throw "the bundle's tsrct.exe does not start or reports another version" }
    Remove-Item -LiteralPath (Join-Path $Dest "tesseract") -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $bundle -Destination (Join-Path $Dest "tesseract")
    Ok "Tesseract $(VersionOf $Tsrct): $Tsrct"
    Write-Host "    Tesseract is free to use under Mirage's terms (not open source; see TERMS.md beside it)."
  } finally { Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue }
}
if (Test-Path -LiteralPath $Tsrct) {
  $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  Write-Host "    usage counts: $(& $Tsrct telemetry status 2>$null | Select-Object -First 1) (left as you set them)"
  $ErrorActionPreference = $previous
}

# ---- Tesseract Converter (MIT): built from source with the Windows path fix, else the release
$Conv = Join-Path $Dest "converter\tsrct-conv.exe"
$Record = Join-Path $Dest "converter\studio-build.json"
function BuiltRecord {
  if (-not (Test-Path -LiteralPath $Record)) { return $null }
  try { return Get-Content -Raw -LiteralPath $Record | ConvertFrom-Json } catch { return $null }
}
function Install-ConverterRelease {
  $scratch = Join-Path $env:TEMP ("studio-finish-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
  New-Item -ItemType Directory -Force -Path $scratch | Out-Null
  try {
    Write-Host "  downloading Tesseract-Converter-$ConvVersion-windows-x86_64.zip (about 50 MB)"
    $bundle = Fetch "https://github.com/mirage-hq/Tesseract-Converter/releases/download/v$ConvVersion/Tesseract-Converter-$ConvVersion-windows-x86_64.zip" $ConvSha $scratch
    # the bundle's own manifest lists a checksum for each of its native files
    $files = (Get-Content -Raw -LiteralPath (Join-Path $bundle "native\manifest.json") | ConvertFrom-Json).files
    $names = @($files.PSObject.Properties.Name)
    if (-not $names) { throw "the converter bundle's manifest lists no files" }
    foreach ($name in $names) {
      if ((Sha256 (Join-Path $bundle ($name -replace "/", "\"))) -ne $files.$name) { throw "the converter bundle does not match its manifest ($name): not installed" }
    }
    if ((VersionOf (Join-Path $bundle "tsrct-conv.exe")) -ne $ConvVersion) { throw "the converter does not start or reports another version" }
    Remove-Item -LiteralPath (Join-Path $Dest "converter") -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $bundle -Destination (Join-Path $Dest "converter")
    Ok "Tesseract Converter $(VersionOf $Conv): $Conv (MIT, the release: Premiere projects wait for the fixed build)"
  } finally { Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue }
}
function Build-Converter {
  if ($env:PROCESSOR_ARCHITECTURE -ne "ARM64" -and $env:PROCESSOR_ARCHITEW6432 -ne "ARM64") { throw "the converter is built here for Windows on Arm only" }
  . (Join-Path $PSScriptRoot "windows-msvc.ps1")
  . (Join-Path $PSScriptRoot "windows-rust.ps1")
  $null = Initialize-StudioArm64Compiler
  Ok "Visual Studio Build Tools (ARM64 compiler)"
  Ok "Rust: $(Initialize-StudioRust)"
  Ok "libclang: $(Initialize-StudioLibclang)"
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is needed to fetch the converter's source" }
  $scratch = Join-Path $env:TEMP ("studio-conv-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
  New-Item -ItemType Directory -Force -Path $scratch | Out-Null
  $previous = $ErrorActionPreference
  try {
    Write-Host "  downloading $FfArchive (about 56 MB)"
    $ffZip = Join-Path $scratch $FfArchive
    Invoke-WebRequest -UseBasicParsing $FfUrl -OutFile $ffZip
    if ((Sha256 $ffZip) -ne $FfSha) { throw "$FfArchive does not match its pinned SHA-256" }
    Expand-Archive -LiteralPath $ffZip -DestinationPath (Join-Path $scratch "ff") -Force
    $ff = (Get-ChildItem -LiteralPath (Join-Path $scratch "ff") -Directory | Select-Object -First 1).FullName
    $src = Join-Path $scratch "src"
    $ErrorActionPreference = "Continue"   # git and cargo say their progress on standard error
    & git init -q $src 2>&1 | Out-Null
    & git -C $src fetch -q --depth 1 https://github.com/mirage-hq/Tesseract-Converter.git $ConvCommit 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "could not fetch the converter's source at $ConvCommit" }
    & git -C $src checkout -q FETCH_HEAD 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0 -or (& git -C $src rev-parse HEAD) -ne $ConvCommit) { throw "the converter's source is not at $ConvCommit" }
    $ErrorActionPreference = $previous
    # the fix (mirage-hq/Tesseract-Converter#6): an artifact path written with "/" was compared with the same path
    # rebuilt with Windows' "\", so every linked After Effects composition was refused; compare them with "/" both
    $file = Join-Path $src "apps\tesseract-conv\src\hybrid\package.rs"
    $old = '&& path.components().collect::<PathBuf>().as_os_str() == path.as_os_str(),'
    $new = '&& path.components().collect::<PathBuf>().to_str().map(|s| s.replace(''\\'', "/")) == path.to_str().map(|s| s.replace(''\\'', "/")),'
    $text = Get-Content -Raw -LiteralPath $file
    $found = ([regex]::Matches($text, [regex]::Escape($old))).Count
    if ($found -ne 2) { throw "the converter's source is not as expected ($found of the 2 path checks found)" }
    [IO.File]::WriteAllText($file, $text.Replace($old, $new))
    Ok "source at $($ConvCommit.Substring(0, 12)), the Windows path check fixed"
    Write-Host "  building tsrct-conv (about 3 minutes; the first build also fetches the Rust toolchain its rust-toolchain.toml pins)"
    $env:FFMPEG_DIR = $ff
    $log = Join-Path $scratch "build.log"
    $ErrorActionPreference = "Continue"
    & $env:ComSpec /d /s /c "cd /d `"$src`" && cargo build --release --locked -p tsrct-conv > `"$log`" 2>&1"
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) { Get-Content -LiteralPath $log -Tail 20 | Out-Host; throw "cargo build failed (exit $code)" }
    $out = Join-Path $scratch "converter"
    New-Item -ItemType Directory -Force -Path (Join-Path $out "licenses") | Out-Null
    Copy-Item -LiteralPath (Join-Path $src "target\release\tsrct-conv.exe") -Destination $out
    Copy-Item -Path (Join-Path $ff "bin\*.dll") -Destination $out
    Copy-Item -LiteralPath (Join-Path $src "LICENSE") -Destination (Join-Path $out "licenses\Tesseract-Converter-LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $ff "LICENSE.txt") -Destination (Join-Path $out "licenses\FFmpeg-LICENSE.txt")
    if ((VersionOf (Join-Path $out "tsrct-conv.exe")) -ne $ConvVersion) { throw "the built converter does not start or reports another version" }
    [ordered]@{ version = $ConvVersion; source = $ConvCommit; fix = $ConvFix; platform = "windows-arm64"; ffmpeg = $FfVersion
                ffmpeg_licence = "LGPL-2.1-or-later (BtbN lgpl-shared)" } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $out "studio-build.json") -Encoding ASCII
    Remove-Item -LiteralPath (Join-Path $Dest "converter") -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $out -Destination (Join-Path $Dest "converter")
    Ok "Tesseract Converter $(VersionOf $Conv), built for Windows on Arm with the path fix: $Conv (MIT; FFmpeg LGPL)"
  } finally {
    $ErrorActionPreference = $previous
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
  }
}
$built = BuiltRecord
if ((VersionOf $Conv) -eq $ConvVersion -and $built -and $built.fix -eq $ConvFix -and $built.source -eq $ConvCommit) {
  Ok "Tesseract Converter ${ConvVersion} (built here, with the Windows path fix): $Conv"
} elseif ($Verify) {
  if ((VersionOf $Conv) -eq $ConvVersion) { Note "Tesseract Converter is the release, without the Windows path fix: Premiere projects wait for the build (run this without -Verify)" }
  else { Note "Tesseract Converter is not installed: Premiere and After Effects projects wait for it (Resolve does not)" }
} else {
  try { Build-Converter }
  catch {
    Note "the converter could not be built here: $($_.Exception.Message)"
    if ((VersionOf $Conv) -ne $ConvVersion) { Install-ConverterRelease }
    else { Note "keeping the installed release: After Effects and Resolve work; Premiere waits for the build (run this again)" }
  }
}
Write-Host "Done. The Studio finds it at $Dest; open Edit > Timeline > Finish."
