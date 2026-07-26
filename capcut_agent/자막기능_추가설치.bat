@echo off
chcp 65001 > nul
title CapCut Agent - 자막(음성인식) 기능 설치
cd /d "%~dp0"

if not exist "%~dp0runtime\python\python.exe" (
    echo  먼저 "설치및실행.bat" 을 한 번 실행해 기본 설치를 끝내주세요.
    pause
    exit /b 1
)

echo.
echo  Whisper 음성인식(자막) 기능을 설치합니다.
echo  용량이 크고(수 GB) 시간이 걸립니다. 인터넷 연결 상태로 기다려 주세요.
echo  (자막 기능을 안 쓰면 설치 안 해도 컷편집/파일삽입은 됩니다.)
echo.

"%~dp0runtime\python\python.exe" -m pip install --no-warn-script-location faster-whisper
if errorlevel 1 (
    echo.
    echo  [오류] 설치 실패. 인터넷 연결을 확인하고 다시 실행하세요.
    pause
    exit /b 1
)

echo.
echo  ✅ 자막 기능 설치 완료! 이제 "설치및실행.bat" 으로 실행하고
echo     자막 옵션을 켜서 사용하세요. (처음 사용 시 모델 다운로드 ~3GB)
pause
