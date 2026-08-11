@echo off
cd /d "%~dp0"
title yeneung 점검 / 재설치

echo ============================================
echo   yeneung 점검 / 재설치
echo ============================================
echo.
echo 뭔가 이상할 때 쓰세요. 설치된 것을 지우고 처음부터 다시 깝니다.
echo 만들어 둔 효과음과 설정은 지우지 않습니다.
echo.
choice /c YN /m "다시 설치할까요"
if errorlevel 2 goto onlycheck

if exist "%~dp0.venv" rmdir /s /q "%~dp0.venv"
echo 지웠습니다. 실행.bat 이 알아서 다시 설치합니다.
echo.
call "%~dp0실행.bat"
exit /b %errorlevel%

:onlycheck
echo.
if not exist "%~dp0.venv\Scripts\python.exe" goto notinstalled
"%~dp0.venv\Scripts\python.exe" -m yeneung doctor
echo.
pause
exit /b 0

:notinstalled
echo 아직 설치되지 않았습니다. 실행.bat 을 먼저 실행하세요.
echo.
pause
exit /b 1
