@echo off
setlocal
title FinanceRanker - push to GitHub

rem Git ships inside DSH, not on PATH, so the full path is required.
set "GIT=C:\Users\Lenovo\AppData\Roaming\io.github.hairyf.deepseek-harness-desktop\dependencies\git\cmd\git.exe"
set "REPO=C:\Users\Lenovo\OneDrive - Nanyang Technological University\Desktop\FinanceRanker-TSM-FX-audit-Windows-source"

if not exist "%GIT%" (
  echo Could not find git at:
  echo   %GIT%
  echo.
  pause
  exit /b 1
)

cd /d "%REPO%"
if not "%CD%"=="%REPO%" (
  echo Could not open the repository folder:
  echo   %REPO%
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo  Repository
echo    %REPO%
echo ============================================================
echo.

echo -- branches and remote ------------------------------------
"%GIT%" rev-parse --abbrev-ref HEAD
"%GIT%" ls-remote origin main
echo.

echo -- commits waiting to be pushed ---------------------------
"%GIT%" log --oneline -4
echo.

echo -- checking whether the push is a clean fast-forward -----
"%GIT%" push --dry-run origin master:main
if errorlevel 1 (
  echo.
  echo The dry run failed. Nothing was pushed.
  echo If it says the remote has work you do not have, stop here
  echo and tell the assistant so the commits can be rebased.
  echo.
  pause
  exit /b 1
)

echo.
set /p ANSWER=Push these commits now? [y/N]: 
if /i not "%ANSWER%"=="y" (
  echo.
  echo Cancelled. Nothing was pushed.
  echo.
  pause
  exit /b 0
)

echo.
echo -- pushing ------------------------------------------------
"%GIT%" push origin master:main
if errorlevel 1 (
  echo.
  echo The push failed. See the message above.
  echo Authentication problems: run   cmdkey /delete:git:https://github.com   then try again.
  echo.
  pause
  exit /b 1
)

echo.
echo -- remote main now points at ------------------------------
"%GIT%" ls-remote origin main
echo.
echo Done. Verify at:
echo   https://github.com/dahuang3244/FinanceRanker/commits/main
echo.
pause
