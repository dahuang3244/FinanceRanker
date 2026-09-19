# Build the Windows portable app and the installer, locally.
#
#   powershell -ExecutionPolicy Bypass -File windows\build.ps1
#
# Run this ON WINDOWS: PyInstaller cannot cross-compile, so a macOS/Linux host
# cannot produce a Windows .exe. If you do not have a Windows machine, push the
# repo and let .github/workflows/build.yml build it for you.
#
# Prereqs: Python 3.12 x64 on PATH, and Inno Setup 6 (optional, for the installer):
#   winget install -e --id JRSoftware.InnoSetup

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== FinanceRanker Windows build ==" -ForegroundColor Cyan
Write-Host "root: $root"

# ---- interpreter sanity: the bundle is arch-bound, so a mismatched Python
# ---- silently produces an exe that will not run on the target machine.
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "python not found on PATH" }
$info = python -c "import platform,sys;print(platform.machine(), sys.version.split()[0])"
Write-Host "python: $info"
if ($info -notmatch "64|AMD64|ARM64") { throw "a 64-bit Python is required (got: $info)" }

# ---- dependencies
Write-Host "`n-- installing dependencies" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# ---- tests before shipping anything
Write-Host "`n-- running tests" -ForegroundColor Cyan
$env:PYTHONPATH = $root
foreach ($t in @("test_engine","test_preview","test_sectors","test_scale","test_health")) {
    python "tests/$t.py"
    if ($LASTEXITCODE -ne 0) { throw "$t failed" }
}

# ---- build
Write-Host "`n-- building with PyInstaller" -ForegroundColor Cyan
python -m PyInstaller --clean --noconfirm finance_ranker.spec
if (-not (Test-Path "dist\FinanceRanker\FinanceRanker.exe")) {
    throw "build finished but dist\FinanceRanker\FinanceRanker.exe is missing"
}

# ---- smoke test the frozen bundle
Write-Host "`n-- smoke-testing the packaged app" -ForegroundColor Cyan
$env:FR_DATA_DIR = Join-Path $env:TEMP "frdata-smoke"
$env:FR_SCHEDULE_HOURS = "0"
Remove-Item -Recurse -Force $env:FR_DATA_DIR -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath "dist\FinanceRanker\FinanceRanker.exe" -ArgumentList "--no-window" -PassThru
try {
    $port = $null
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        $log = Join-Path $env:FR_DATA_DIR "launcher.log"
        if (Test-Path $log) {
            $m = Select-String -Path $log -Pattern "serving at http://127\.0\.0\.1:(\d+)" | Select-Object -Last 1
            if ($m) { $port = $m.Matches[0].Groups[1].Value; break }
        }
        if ($proc.HasExited) { throw "exited early with code $($proc.ExitCode)" }
    }
    if (-not $port) { throw "server did not report a port within 90s" }
    $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 60
    Write-Host "health: $($health.status) | reachable: $($health.probe.reachable -join ', ')"
    $page = Invoke-WebRequest "http://127.0.0.1:$port/" -TimeoutSec 30 -UseBasicParsing
    if ($page.StatusCode -ne 200) { throw "index.html returned $($page.StatusCode)" }
    Write-Host "smoke test passed" -ForegroundColor Green
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}

# ---- installer (optional)
Write-Host "`n-- building the installer" -ForegroundColor Cyan
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    & $iscc "windows\installer.iss"
    Get-ChildItem "dist\*-setup.exe" | ForEach-Object {
        Write-Host ("installer: {0} ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Green
    }
} else {
    Write-Warning "Inno Setup not found; skipping the installer."
    Write-Warning "Install it with: winget install -e --id JRSoftware.InnoSetup"
}

Write-Host "`nDone." -ForegroundColor Cyan
Write-Host "  portable : dist\FinanceRanker\FinanceRanker.exe"
if ($iscc) { Write-Host "  installer: dist\FinanceRanker-*-setup.exe" }
