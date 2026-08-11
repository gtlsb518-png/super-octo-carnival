@echo off
title CapCut Agent - install everything
cd /d "%~dp0"

echo.
echo  ============================================
echo    CapCut Agent  -  install EVERYTHING
echo  ============================================
echo.
echo  Installs all of it in one go:
echo    1) core        : portable python + ffmpeg + server
echo    2) subtitles   : faster-whisper + speech model (about 3 GB)
echo    3) GPU support : NVIDIA only - subtitles 5-10x faster
echo    4) cutout      : rembg + model (about 176 MB)
echo.
echo  Needs internet. The model downloads take a while.
echo  Safe to run again - anything already installed is skipped.
echo.
pause

echo.
echo  [core] portable python / ffmpeg / server packages ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo  [ERROR] Core setup failed. Check internet / firewall, then run again.
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
echo  ============================================
echo    Done. Now run the start bat to use it.
echo  ============================================
pause
