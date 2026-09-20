@echo off
setlocal
cd /d "%~dp0"
echo Building FinanceRanker for Windows. This will install Python dependencies and download the bundled browser.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\build.ps1"
set "build_result=%ERRORLEVEL%"
if not "%build_result%"=="0" (
  echo.
  echo Build failed. Review the error above.
) else (
  echo.
  echo Portable app: dist\FinanceRanker\FinanceRanker.exe
  echo Keep the whole dist\FinanceRanker folder together when moving the app.
  echo If Inno Setup was installed, the installer is in dist\FinanceRanker-0.1.1-setup.exe
)
echo.
pause
exit /b %build_result%
