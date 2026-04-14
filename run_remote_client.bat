@echo off
setlocal
cd /d "%~dp0"

echo [CapCap] Starting remote client...
python ui\gui_remote.py

if errorlevel 1 (
    echo.
    echo [CapCap] Remote client exited with an error.
    pause
)
