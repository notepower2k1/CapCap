@echo off
setlocal
cd /d "%~dp0"

echo [CapCap] Starting local client...
python ui\gui.py

if errorlevel 1 (
    echo.
    echo [CapCap] Local client exited with an error.
    pause
)
