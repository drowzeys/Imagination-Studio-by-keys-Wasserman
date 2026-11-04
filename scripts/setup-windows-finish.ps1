<#
.SYNOPSIS
  Finish (app/finish.py) natively on Windows: Tesseract's command-line renderer, Tesseract Converter and the two fonts
  the title card and captions use, as scripts/setup-finish.sh installs them on a Mac or Linux x86_64.

.DESCRIPTION
  Under %LOCALAPPDATA%\ImaginationStudio\finish (the native CUDA tier's PREFIX) it adds:
    tesseract\   Tesseract's pinned release from github.com/mirage-hq/tesseract (tsrct.exe and its FFmpeg DLLs)
    converter\   Tesseract Converter's pinned release from github.com/mirage-hq/Tesseract-Converter (MIT)
    fonts\       Instrument Serif and Geist (SIL Open Font License)
  Both releases are built for windows-x86_64 only: on Windows on Arm (an RTX Spark N1X) they run through Windows' own
  x64 emulation, which Windows 11 includes. Each download is checked against the SHA-256 pinned here, and every file in
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

# ---- Tesseract Converter (MIT)
$Conv = Join-Path $Dest "converter\tsrct-conv.exe"
if ((VersionOf $Conv) -eq $ConvVersion) { Ok "Tesseract Converter ${ConvVersion}: $Conv" }
elseif ($Verify) { Note "Tesseract Converter is not installed: Premiere and After Effects projects wait for it (Resolve does not)" }
else {
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
    Ok "Tesseract Converter $(VersionOf $Conv): $Conv (MIT)"
  } finally { Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue }
}
Write-Host "Done. The Studio finds it at $Dest; open Edit > Timeline > Finish."
