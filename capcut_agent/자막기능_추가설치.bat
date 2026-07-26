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

echo.
echo  Done! Subtitle feature installed.
echo  Now run the main installer bat and turn the subtitle option on.
pause
