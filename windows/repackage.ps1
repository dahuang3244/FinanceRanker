# Build the portable zip that gets handed to someone else.
#
# Lives in the repo, and reads the recipient readme from the repo, because both used to
# live only in %TEMP%: the zip's contents therefore depended on a file outside version
# control, and a fresh machine or a cleared temp folder would produce a package with no
# readme at all — including the data-source and licensing notice that file carries.
#
# Order matters. `build.ps1` produces the exe, `windows\deploy_mirror.ps1` copies it to
# the staged folder this script zips, and *then* this runs. Packaging before the mirror
# is refreshed produces a zip carrying the previous build, which the verifier at the end
# catches by comparing hashes.
$ErrorActionPreference = "Stop"

$src = Split-Path -Parent $PSScriptRoot
$root = "C:\Users\Lenovo\OneDrive - Nanyang Technological University\Desktop\FinanceRanker-v0.1.1-Windows-"
$dst = Join-Path $root "dist\FinanceRanker"
$readme = Join-Path $src "windows\READ-ME-FIRST.txt"

if (-not (Test-Path $readme)) {
    throw "recipient readme missing from the repo: $readme"
}
if (-not (Test-Path $dst)) {
    throw "staged build missing: $dst — run build.ps1 then deploy_mirror.ps1 first"
}

Get-Process -Name FinanceRanker -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
Start-Sleep -Seconds 4

$out = Join-Path $root "FinanceRanker-0.1.1-portable.zip"
$stage = Join-Path $env:TEMP "fr-stage"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $stage "FinanceRanker") | Out-Null
Copy-Item "$dst\*" (Join-Path $stage "FinanceRanker") -Recurse -Force
Copy-Item $readme (Join-Path $stage "FinanceRanker\READ-ME-FIRST.txt") -Force
Remove-Item (Join-Path $stage "FinanceRanker\data\launcher.log") -Force -ErrorAction SilentlyContinue
Remove-Item $out -Force -ErrorAction SilentlyContinue

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stage, $out, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue

Get-Item $out | Select-Object @{n='SizeMB';e={[math]::Round($_.Length/1MB,0)}}, LastWriteTime | Format-List

# Confirm the removed files are not in the archive.
$z = [System.IO.Compression.ZipFile]::OpenRead($out)
$names = $z.Entries | ForEach-Object { $_.FullName }
$z.Dispose()
$stale = $names | Where-Object { $_ -match 'assessment|qualitative' }
Write-Host "assessment artefacts inside the zip: $(if($stale){$stale -join ', '}else{'none'})"

$env:PYTHONPATH = $src
& "$src\.venv-build\Scripts\python.exe" "$src\tests\verify_package.py" 2>&1 | Select-Object -First 24
