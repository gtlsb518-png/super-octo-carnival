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

set "PY=%~dp0runtime\python\python.exe"

if not exist "%PY%" (
    echo.
    echo  [ERROR] Python was not installed correctly.
    echo  Delete the "runtime" folder and run this file again.
    pause
    exit /b 1
)

echo.
echo  Checking packages...
"%PY%" -c "import fastapi, uvicorn; print('  packages OK')"
if errorlevel 1 (
    echo.
    echo  [ERROR] Required packages are missing.
    echo  Delete the "runtime" folder and run this file again.
    pause
    exit /b 1
)

if not exist "outputs" mkdir outputs
if not exist "uploads" mkdir uploads

set "PATH=%~dp0runtime\ffmpeg\bin;%PATH%"

echo.
echo  ============================================
echo    Starting server - your browser will open.
echo    (Press Ctrl+C in this window to stop)
echo  ============================================
echo.

"%PY%" "%~dp0server.py"

echo.
echo  Server stopped.
echo  If there was an error, see the message above
echo  and the file  error_log.txt  in this folder.
pause
