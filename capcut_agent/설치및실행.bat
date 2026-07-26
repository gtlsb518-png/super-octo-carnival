@echo off
chcp 65001 > nul
title CapCut Agent - 설치 및 실행
cd /d "%~dp0"

echo.
echo  ============================================
echo   CapCut Agent  -  무음 자동 컷편집 + 자막
echo  ============================================
echo.
echo  처음 실행이면 Python과 ffmpeg를 자동으로 내려받습니다.
echo  (관리자 권한 필요 없음, 이 폴더의 runtime\ 안에만 설치됨)
echo  인터넷이 되는 상태에서 잠시 기다려 주세요...
echo.

:: ── 자동 설치 (portable Python + ffmpeg + 패키지) ──
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo  [오류] 설치에 실패했습니다.
    echo         인터넷 연결을 확인한 뒤 이 파일을 다시 실행하세요.
    echo         회사망/방화벽 때문이면 다른 네트워크에서 한 번만 실행하면 됩니다.
    pause
    exit /b 1
)

:: ── 폴더 준비 ──
if not exist "outputs" mkdir outputs
if not exist "uploads" mkdir uploads

:: ── 로컬 runtime 을 이 세션 PATH 앞에 추가 ──
set "PATH=%~dp0runtime\ffmpeg\bin;%PATH%"
set "PYTHONPATH=%~dp0"

echo.
echo  ============================================
echo   서버 시작!  브라우저에서 자동으로 열립니다.
echo   안 열리면 직접 접속: http://localhost:8765
echo   종료하려면 이 창에서 Ctrl+C
echo  ============================================
echo.

"%~dp0runtime\python\python.exe" "%~dp0server.py"
pause
