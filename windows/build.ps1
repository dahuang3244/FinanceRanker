# Build the Windows portable app and the installer, locally.
#
#   powershell -ExecutionPolicy Bypass -File windows\build.ps1
#
# Run this ON WINDOWS: PyInstaller cannot cross-compile, so a macOS/Linux host
# cannot produce a Windows .exe. If you do not have a Windows machine, push the
# repo and let .github/workflows/build.yml build it for you.
#
# Prereqs: Python 3.12 x64 via the Windows py launcher or on PATH;
# Inno Setup 6 is optional (for the installer):
#   winget install -e --id JRSoftware.InnoSetup

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== FinanceRanker Windows build ==" -ForegroundColor Cyan
Write-Host "root: $root"

# ---- Windows sometimes exposes a Microsoft Store `python.exe` placeholder.
# ---- Probe actual interpreters and check both version and architecture before
# ---- attempting the build, including installs omitted from PATH.
$candidates = @()
$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($pyLauncher -and $pyLauncher.Source -notlike '*\Microsoft\WindowsApps\*') {
    try {
        $found = & $pyLauncher.Source -3.12 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $candidates += $found }
    } catch { }
}
$onPath = Get-Command python.exe -ErrorAction SilentlyContinue
if ($onPath -and $onPath.Source -notlike '*\Microsoft\WindowsApps\*') {
    $candidates += $onPath.Source
}
$candidates += @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:ProgramFiles 'Python312\python.exe')
)
$basePython = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (-not $candidate -or -not (Test-Path $candidate)) { continue }
    try {
        $signature = & $candidate -c "import sys;print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'|'+str(sys.maxsize > 2**32))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $signature -eq '3.12|True') {
            $basePython = $candidate
            break
        }
    } catch { }
}
if (-not $basePython) {
    Write-Host 'Python 3.12 x64 is not installed or could not be found.' -ForegroundColor Red
    Write-Host 'The Windows Store python.exe alias is not a Python installation.'
    Write-Host 'Install Python 3.12 x64, then double-click Build-Windows.cmd again:'
    Write-Host '  winget install --id Python.Python.3.12 --exact --source winget'
    Write-Host 'If winget is unavailable, use the 64-bit Windows installer from python.org.'
    exit 2
}
$info = & $basePython -c "import platform,sys;print(platform.machine(), sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "Python executable could not report its version" }
Write-Host "python: $info"
if ($info -notmatch "64|AMD64|ARM64") { throw "a 64-bit Python is required (got: $info)" }
if ($info -notmatch "3\.12\.") { throw "Python 3.12 is required (got: $info)" }

# Install dependencies in the project, without changing the user's other
# Python installations. Reusing the environment speeds up later rebuilds.
$python = Join-Path $root ".venv-build\Scripts\python.exe"
if (-not (Test-Path $python)) {
    & $basePython -m venv ".venv-build"
    if ($LASTEXITCODE -ne 0) { throw "could not create .venv-build" }
}

# ---- dependencies
Write-Host "`n-- installing dependencies" -ForegroundColor Cyan
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }

# ---- tests before shipping anything
Write-Host "`n-- running tests" -ForegroundColor Cyan
$env:PYTHONPATH = $root
foreach ($t in @("test_engine","test_formula_revision","test_market_risk","test_snapshot_freshness","test_metric_catalog","test_non_gaap_bridge","test_options","test_trackrecord","test_gex","test_treasury","test_i18n_coverage","test_quarterly","test_eps_basis","test_asset_freshness","test_volatility","test_ebitda_fallback","test_adr_fallback","test_tsm_metrics","test_insights_public","test_preview","test_sectors","test_scale","test_health")) {
    & $python "tests/$t.py"
    if ($LASTEXITCODE -ne 0) { throw "$t failed" }
}
if (Get-Command node -ErrorAction SilentlyContinue) {
    node "tests/test_company_labels.js"
    if ($LASTEXITCODE -ne 0) { throw "bilingual company labels failed" }
    node "tests/test_insights_ui.js"
    if ($LASTEXITCODE -ne 0) { throw "company insights UI failed" }
    node "tests/test_ranking_table.js"
    if ($LASTEXITCODE -ne 0) { throw "ranking table guard failed" }
    node "tests/test_analyst_ui.js"
    if ($LASTEXITCODE -ne 0) { throw "analyst panel failed" }
    node "tests/test_price_chart.js"
    if ($LASTEXITCODE -ne 0) { throw "price chart failed" }
    node "tests/test_options_ui.js"
    if ($LASTEXITCODE -ne 0) { throw "options UI failed" }
    node "tests/test_options_integration.js"
    if ($LASTEXITCODE -ne 0) { throw "options integration failed" }
    if ($LASTEXITCODE -ne 0) { throw "ranking table guard failed" }
    node "tests/test_risk_labels.js"
    if ($LASTEXITCODE -ne 0) { throw "return/risk metric labels failed" }
}

# ---- the browser that ships inside the app
# ---- Windows does not use pywebview: its backend needs pythonnet, which cannot
# ---- load from a frozen bundle. Chromium remains a fallback when the user's
# ---- standard browser cannot be opened.
Write-Host "`n-- staging the bundled browser" -ForegroundColor Cyan
$ProgressPreference = "SilentlyContinue"
if (Test-Path "vendor\chromium\chrome.exe") {
    Write-Host "already staged, skipping download"
} else {
    $meta = Invoke-RestMethod "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
    $stable = $meta.channels.Stable
    $url = ($stable.downloads.chrome | Where-Object { $_.platform -eq "win64" }).url
    if (-not $url) { throw "could not resolve a win64 Chrome for Testing URL" }
    Write-Host "Chrome for Testing $($stable.version)"
    Write-Host "downloading $url (about 200 MB)"
    $zip = Join-Path $env:TEMP "chrome-win64.zip"
    # curl.exe rather than Invoke-WebRequest: ~200 MB, and IWR's progress
    # rendering makes it several times slower.
    curl.exe -L --fail --retry 3 -o $zip $url
    if ($LASTEXITCODE -ne 0) { throw "browser download failed (curl exit $LASTEXITCODE)" }
    Expand-Archive -Path $zip -DestinationPath $env:TEMP -Force
    New-Item -ItemType Directory -Force -Path "vendor\chromium" | Out-Null
    Copy-Item "$env:TEMP\chrome-win64\*" "vendor\chromium\" -Recurse -Force
    if (-not (Test-Path "vendor\chromium\chrome.exe")) { throw "chrome.exe was not staged" }
}
$mb = (Get-ChildItem "vendor\chromium" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("staged Chromium: {0:N1} MB" -f $mb) -ForegroundColor Green

# ---- build
Write-Host "`n-- building with PyInstaller" -ForegroundColor Cyan
& $python -m PyInstaller --clean --noconfirm finance_ranker.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
if (-not (Test-Path "dist\FinanceRanker\FinanceRanker.exe")) {
    throw "build finished but dist\FinanceRanker\FinanceRanker.exe is missing"
}

# ---- smoke test the frozen bundle
Write-Host "`n-- smoke-testing the packaged app" -ForegroundColor Cyan
$env:FR_DATA_DIR = Join-Path $env:TEMP "frdata-smoke"
$env:FR_SCHEDULE_HOURS = "0"
Remove-Item -Recurse -Force $env:FR_DATA_DIR -ErrorAction SilentlyContinue

# --no-window never touches the window layer, so assert it separately.
$self = Start-Process -FilePath "dist\FinanceRanker\FinanceRanker.exe" -ArgumentList "--selftest" -PassThru -Wait
if ($self.ExitCode -ne 0) { throw "selftest failed (exit $($self.ExitCode))" }
Write-Host "window layer present" -ForegroundColor Green

$proc = Start-Process -FilePath "dist\FinanceRanker\FinanceRanker.exe" -ArgumentList "--no-window" -PassThru
try {
    $port = $null
    # The launcher gives up after 120 s, so allow longer here to let its own
    # diagnostic (written to launcher.log) win the race.
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        $log = Join-Path $env:FR_DATA_DIR "launcher.log"
        if (Test-Path $log) {
            $m = Select-String -Path $log -Pattern "serving at http://127\.0\.0\.1:(\d+)" | Select-Object -Last 1
            if ($m) { $port = $m.Matches[0].Groups[1].Value; break }
        }
        if ($proc.HasExited) { throw "exited early with code $($proc.ExitCode)" }
    }
    if (-not $port) { throw "server did not report a port within 180s" }
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
