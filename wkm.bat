@echo off
REM Double-click this. Then press Ctrl+Alt+M to hand control to the Mac.
cd /d "%~dp0"
title wkm - Ctrl+Alt+M to switch

if not exist ".venv\Scripts\python.exe" (
    echo First run - setting up...
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup-windows.ps1"
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Setup failed. See the messages above.
        pause
        exit /b 1
    )
)

:run
".venv\Scripts\python.exe" -m wkm source
echo.
echo wkm stopped. Press any key to restart it, or close this window.
pause >nul
goto run
