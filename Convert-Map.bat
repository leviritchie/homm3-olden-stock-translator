@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Convert-Map.ps1" %*
set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
  echo.
  echo Conversion failed. If PowerShell blocked the script, right-click Convert-Map.ps1 -^> Properties -^> Unblock,
  echo or run: powershell -ExecutionPolicy Bypass -File Convert-Map.ps1
  pause
  exit /b %ERR%
)
echo.
pause
endlocal
