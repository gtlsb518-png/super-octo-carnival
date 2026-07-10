@echo off
chcp 65001 > nul
title CapCut Agent - 설치 및 실행

echo.
echo  ============================================
echo   CapCut Agent v2.0  -  무음 자동 컷편집
echo  ============================================
echo.

:: Python 확인
python --version > nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo        https://python.org 에서 Python 3.10 이상을 설치하세요.
    pause
    exit /b 1
)

:: ffmpeg 확인
ffmpeg -version > nul 2>&1
if errorlevel 1 (
    echo [경고] ffmpeg을 찾을 수 없습니다.
    echo  winget install Gyan.FFmpeg 실행 후 재시작하세요.
    pause
    exit /b 1
)

:: 패키지 설치 (pip upgrade 먼저)
echo [1/3] Python 패키지 설치 중... (시간이 걸릴 수 있습니다)
python -m pip install --upgrade pip --quiet
python -m pip install fastapi uvicorn python-multipart faster-whisper
if errorlevel 1 (
    echo [오류] 패키지 설치 실패
    pause
    exit /b 1
)
echo       완료

:: 폴더 생성
if not exist "outputs" mkdir outputs
if not exist "uploads" mkdir uploads
echo [2/3] 폴더 생성 완료

:: 서버 실행
echo [3/3] 서버 시작 중...
echo.
echo  브라우저에서 접속: http://localhost:8765
echo  종료: Ctrl+C
echo.

start "" /min cmd /c "timeout /t 3 > nul && start http://localhost:8765"

python server.py
pause
