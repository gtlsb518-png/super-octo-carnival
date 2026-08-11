@echo off
title CapCut Agent - subtitle feature
cd /d "%~dp0"

if not exist "%~dp0runtime\python\python.exe" (
    echo  Please run the main setup bat first.
    echo  It creates the runtime folder this needs.
    pause
    exit /b 1
)

echo.
echo  Installing subtitle (speech recognition) feature.
echo  This download is large (several GB) and takes a while.
echo  You only need this if you want auto subtitles.
echo.

"%~dp0runtime\python\python.exe" -m pip install --no-warn-script-location faster-whisper
if errorlevel 1 (
    echo.
    echo  [ERROR] Install failed. Check internet and run again.
    pause
    exit /b 1
)

rem ── GPU (NVIDIA) support: makes subtitles 5-10x faster ──
nvidia-smi -L >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  NVIDIA GPU found. Installing CUDA libraries for fast subtitles...
    "%~dp0runtime\python\python.exe" -m pip install --no-warn-script-location nvidia-cublas-cu12 nvidia-cudnn-cu12
    if errorlevel 1 (
        echo  [WARN] CUDA libraries failed to install. Subtitles will run on CPU.
    ) else (
        echo  GPU ready.
    )
) else (
    echo.
    echo  No NVIDIA GPU detected. Subtitles will run on CPU.
)

echo.
echo  Done! Subtitle feature installed.
echo  Now run the main installer bat and turn the subtitle option on.
pause
