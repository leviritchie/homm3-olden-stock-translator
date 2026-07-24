@echo off
setlocal
cd /d "%~dp0"
REM Default: open the WinForms GUI (no console prompts).
REM Pass args through for CLI use, e.g. Convert-Map.bat -Cli -H3m ...
powershell -STA -NoProfile -ExecutionPolicy Bypass -File "%~dp0Convert-Map.ps1" %*
set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
  echo.
  echo Launch/conversion failed. If PowerShell blocked the script, right-click Convert-Map.ps1 -^> Properties -^> Unblock,
  echo or run: powershell -ExecutionPolicy Bypass -File Convert-Map.ps1
  pause
  exit /b %ERR%
)
endlocal
