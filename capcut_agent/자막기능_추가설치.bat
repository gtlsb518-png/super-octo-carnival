@echo off
rem Kept for the old name. Everything is installed by install_all.ps1 now:
rem subtitles + GPU support + background removal + model downloads.
title CapCut Agent - install everything
cd /d "%~dp0"

if not exist "%~dp0runtime\python\python.exe" (
    echo.
    echo  Please run the main setup bat first.
    echo  It creates the runtime folder this needs.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_all.ps1"
if errorlevel 1 (
    echo.
    echo  [ERROR] Install failed. Check internet / firewall, then run again.
    pause
    exit /b 1
)

echo.
echo  Done.
pause
