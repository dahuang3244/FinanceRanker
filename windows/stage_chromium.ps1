# Stage the Chromium that ships inside the Windows bundle, into vendor\chromium.
#
#   powershell -ExecutionPolicy Bypass -File windows\stage_chromium.ps1 [-Force]
#
# Called by windows\build.ps1 and .github\workflows\build.yml; not meant to be
# run by end users.
#
# Why the official Chromium continuous snapshot, and not Chrome for Testing:
#
#   Chrome for Testing is Google's *branded* portable Chrome. It carries a
#   "Chrome for Testing" branding resource, so every launch renders the info bar
#   IDS_CHROME_FOR_TESTING_INFOBAR -- "Chrome for Testing v<version> is only for
#   automated testing. For regular browsing, use a standard version of Chrome
#   that updates automatically." (zh-CN.pak: "Chrome 测试版 v$1 仅适用于自动测试...").
#   That is a normal-browsing warning shown to end users of a desktop app, which
#   is exactly backwards for us.
#
#   There is no command line switch that suppresses it: chrome.dll exposes no
#   such feature flag, only the machine-level enterprise policy
#   ChromeForTestingAllowed (read from SOFTWARE\Policies\Google\Chrome for
#   Testing). Shipping an unbranded Chromium build makes the warning not exist in
#   the first place, needs no admin rights and no policy, and keeps the app
#   self-contained.
#
#   The snapshot is Chromium's own "latest" continuous x64 build; the bucket is
#   a plain, stable, unauthenticated path: <bucket>/Win_x64/<revision>/chrome-win.zip
#
# Re-running with -Force re-stages an already-present copy (use it to move off a
# Chrome for Testing staging, which the old build scripts left behind).

[CmdletBinding()]
param(
    # Re-stage even when vendor\chromium\chrome.exe already exists.
    [switch]$Force,

    # Browser platform directory in the snapshot bucket: Win_x64 or Win.
    [string]$Platform = "Win_x64"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$dest = Join-Path (Split-Path -Parent $PSScriptRoot) "vendor\chromium"
$exe = Join-Path $dest "chrome.exe"

# A staging already in place is reused, so a local rebuild does not re-download
# and re-extract 350 MB. The one exception is a *Chrome for Testing* tree: builds
# made before windows\stage_chromium.ps1 existed put one there, and reusing it
# would keep shipping the "only for automated testing" info bar this script
# exists to remove. Identify it by its branding resource, which is exactly what
# Chromium builds do not carry.
if ((Test-Path $exe) -and (-not $Force)) {
    $brand = (Get-Item $exe).VersionInfo.FileDescription
    if ($brand -like "*Chrome for Testing*") {
        Write-Host "re-staging: $exe is Chrome for Testing, not Chromium" -ForegroundColor Yellow
    } else {
        Write-Host "bundled Chromium already staged (use -Force to refresh): $exe"
        exit 0
    }
}

if (-not ("System.IO.Compression.ZipFile" -as [type])) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
}

$bucket = "https://storage.googleapis.com/chromium-browser-snapshots"

# The bucket has no JSON index or version alias: a revision number *is* the
# address. LAST_CHANGE names the newest snapshot on the platform's default
# branch. It is read as bytes rather than parsed as JSON so a stale CDN copy
# cannot silently invalidate it, and it is deliberately not -Encoding utf8:
# PowerShell 5.1 mangles UTF-8 there, and this value is only ever ASCII digits.
Write-Host "resolving the latest $Platform snapshot" -ForegroundColor Cyan
$revision = ([System.Text.Encoding]::ASCII.GetString(
    (Invoke-WebRequest "$bucket/$Platform/LAST_CHANGE" -UseBasicParsing -TimeoutSec 60).Content)).Trim()
if ($revision -notmatch '^\d+$') { throw "unexpected LAST_CHANGE payload: '$revision'" }
Write-Host "snapshot revision $revision"

$zipUrl = "$bucket/$Platform/$revision/chrome-win.zip"
$zip = Join-Path $env:TEMP "chromium-$revision.zip"

if ((Test-Path $zip) -and ((Get-Item $zip).Length -gt 50MB)) {
    Write-Host "reusing the cached download at $zip"
} else {
    Write-Host "downloading $zipUrl (about 350 MB)"
    # curl.exe rather than Invoke-WebRequest: this is ~350 MB and IWR's progress
    # rendering makes it several times slower.
    curl.exe -L --fail --retry 3 -o $zip $zipUrl
    if ($LASTEXITCODE -ne 0) { throw "Chromium download failed (curl exit $LASTEXITCODE)" }
}

# Replace the tree rather than copying over it: a staging that is being replaced
# may be a *Chrome for Testing* one, and mixing two distributions' files produces
# a broken browser whose failure mode is far from its cause.
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$staging = Join-Path $env:TEMP "chromium-extract-$revision"
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $staging)

# The archive has a single chrome-win\ root (chrome.exe, chrome.dll, locales\,
# ...). The layout the launcher resolves is documented in desktop.py's
# _find_bundled_browser: _internal\chromium\chrome.exe.
$inner = Join-Path $staging "chrome-win"
if (-not (Test-Path (Join-Path $inner "chrome.exe"))) {
    throw "chrome-win\chrome.exe is missing from the snapshot archive"
}
Copy-Item (Join-Path $inner "*") $dest -Recurse -Force

if (-not (Test-Path $exe)) { throw "chrome.exe was not staged at $exe" }
if (-not (Test-Path (Join-Path $dest "chrome.dll"))) { throw "the staged browser has no chrome.dll" }

# The snapshot archive ships Chrome's own test harnesses too, and
# interactive_ui_tests.exe alone is ~346 MB -- it would nearly double the
# packaged app for a binary no user can run. Chrome for Testing, which this
# replaced, contained none of them. Sanity check after pruning: the payload is
# chrome.dll (~320 MB) plus ~140 MB of support files, so a staged size close to
# twice that means this prune stopped matching.
$pruned = 0
Get-ChildItem $dest -File | Where-Object { $_.Name -like "*_tests.exe" -or $_.Name -like "*unittests*" } | ForEach-Object {
    Write-Host ("pruning test binary {0} ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor DarkGray
    $pruned += $_.Length
    Remove-Item $_.FullName -Force
}
if ($pruned -gt 0) { Write-Host ("pruned {0:N1} MB of test binaries" -f ($pruned / 1MB)) }

$version = (Get-Item $exe).VersionInfo.ProductVersion
$mb = (Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("staged Chromium {0} (snapshot {1}): {2:N1} MB" -f $version, $revision, $mb) -ForegroundColor Green
