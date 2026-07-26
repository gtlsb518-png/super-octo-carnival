@echo off
title CapCut Agent
cd /d "%~dp0"

echo.
echo  ============================================
echo    CapCut Agent  -  setup and run
echo  ============================================
echo.
echo  First run downloads a portable Python and ffmpeg
echo  into the local "runtime" folder. No admin needed,
echo  nothing is installed system-wide.
echo  Please wait - this needs an internet connection...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo  [ERROR] Setup failed.
    echo  Check your internet / firewall, then run this file again.
    pause
    exit /b 1
)

if not exist "outputs" mkdir outputs
if not exist "uploads" mkdir uploads

set "PATH=%~dp0runtime\ffmpeg\bin;%PATH%"
set "PYTHONPATH=%~dp0"

echo.
echo  ============================================
echo    Server starting!
echo    Your browser opens at  http://localhost:8765
echo    (Press Ctrl+C in this window to stop)
echo  ============================================
echo.

"%~dp0runtime\python\python.exe" "%~dp0server.py"
pause
