@echo off
setlocal
cd /d "%~dp0"

echo [CapCap] Starting remote API server...
python app\remote_api_server.py

if errorlevel 1 (
    echo.
    echo [CapCap] Remote API server exited with an error.
    pause
)
