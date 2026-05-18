<#
.SYNOPSIS
    Fetches ffmpeg.exe and ffprobe.exe into this directory.

.DESCRIPTION
    The two ffmpeg binaries are ~148 MB each, which is over GitHub's 100 MB
    per-file push limit, so they're not committed. This script downloads the
    Gyan.dev "release essentials" Windows build, verifies its SHA256 against a
    pinned value, and extracts the two exes into this folder.

    Idempotent: if both exes already exist, exits immediately. Delete one or
    both to force a re-download.

.NOTES
    The Gyan.dev URL is a moving "latest" pointer. When upstream releases a new
    version the SHA256 will stop matching and this script will fail loud — that's
    intentional. To accept the new version, update $ExpectedSha256 below after
    inspecting https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# Pinned: ffmpeg 8.1.1 release-essentials, published 2026-05-04.
$DownloadUrl     = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$ExpectedSha256  = '6F58CE889F59C311410F7D2B18895B33C03456463486F3B1EBC93D97A0F54541'

$BinDir   = $PSScriptRoot
$FfmpegExe  = Join-Path $BinDir 'ffmpeg.exe'
$FfprobeExe = Join-Path $BinDir 'ffprobe.exe'

if ((Test-Path $FfmpegExe) -and (Test-Path $FfprobeExe)) {
    Write-Host "ffmpeg.exe and ffprobe.exe already present in $BinDir. Nothing to do."
    Write-Host "(Delete one or both to force a re-download.)"
    exit 0
}

$TempZip = Join-Path ([System.IO.Path]::GetTempPath()) "ffmpeg-essentials-$([guid]::NewGuid().ToString('N')).zip"
$TempExtract = Join-Path ([System.IO.Path]::GetTempPath()) "ffmpeg-essentials-$([guid]::NewGuid().ToString('N'))"

try {
    Write-Host "Downloading ffmpeg release-essentials zip..."
    Write-Host "  from: $DownloadUrl"
    Write-Host "  to:   $TempZip"
    $ProgressPreference = 'Continue'
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $TempZip -UseBasicParsing

    Write-Host "Verifying SHA256..."
    $ActualHash = (Get-FileHash -Path $TempZip -Algorithm SHA256).Hash
    if ($ActualHash -ne $ExpectedSha256) {
        Write-Error @"
SHA256 mismatch for $DownloadUrl
  expected: $ExpectedSha256
  actual:   $ActualHash

This usually means Gyan.dev has published a newer build. Verify the published
hash at $DownloadUrl.sha256 and, if it matches the actual value above, update
`$ExpectedSha256 in this script.
"@
        exit 1
    }
    Write-Host "  hash OK: $ActualHash"

    Write-Host "Extracting ffmpeg.exe and ffprobe.exe..."
    Expand-Archive -Path $TempZip -DestinationPath $TempExtract -Force

    # The zip extracts to ffmpeg-<version>-essentials_build/bin/{ffmpeg,ffprobe}.exe
    $ExtractedFfmpeg  = Get-ChildItem -Path $TempExtract -Filter 'ffmpeg.exe'  -Recurse -File | Select-Object -First 1
    $ExtractedFfprobe = Get-ChildItem -Path $TempExtract -Filter 'ffprobe.exe' -Recurse -File | Select-Object -First 1

    if (-not $ExtractedFfmpeg -or -not $ExtractedFfprobe) {
        Write-Error "Could not find ffmpeg.exe or ffprobe.exe inside the downloaded archive."
        exit 1
    }

    Copy-Item -Path $ExtractedFfmpeg.FullName  -Destination $FfmpegExe  -Force
    Copy-Item -Path $ExtractedFfprobe.FullName -Destination $FfprobeExe -Force

    Write-Host ""
    Write-Host "Done."
    Write-Host "  $FfmpegExe"
    Write-Host "  $FfprobeExe"
}
finally {
    if (Test-Path $TempZip)     { Remove-Item -Path $TempZip -Force -ErrorAction SilentlyContinue }
    if (Test-Path $TempExtract) { Remove-Item -Path $TempExtract -Recurse -Force -ErrorAction SilentlyContinue }
}
