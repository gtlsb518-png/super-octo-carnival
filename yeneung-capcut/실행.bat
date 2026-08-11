@echo off
cd /d "%~dp0"
title yeneung

set "PY=%~dp0.venv\Scripts\python.exe"
set "STAMP=%~dp0.venv\.installed.txt"

if exist "%PY%" goto ready

rem ============================================================ 첫 실행 준비
echo ============================================
echo   처음 실행 - 필요한 것을 자동으로 설치합니다
echo ============================================
echo.
echo 몇 분 걸립니다. 한 번만 하면 다음부터는 바로 실행됩니다.
echo.

set "BASE="
py -3 --version >nul 2>&1
if not errorlevel 1 set "BASE=py -3"
if defined BASE goto havepython
python --version >nul 2>&1
if not errorlevel 1 set "BASE=python"

:havepython
if defined BASE goto makevenv

echo [오류] 파이썬이 없습니다. 이것만 직접 설치해 주세요.
echo.
echo   1^) https://www.python.org/downloads/ 에서 내려받기
echo   2^) 설치 화면 맨 아래 "Add Python to PATH" 를 반드시 체크
echo   3^) 설치가 끝나면 이 창을 닫고 실행.bat 을 다시 실행
echo.
start https://www.python.org/downloads/
pause
exit /b 1

:makevenv
echo 파이썬을 찾았습니다. 전용 실행 환경을 만듭니다...
%BASE% -m venv "%~dp0.venv"
if exist "%PY%" goto install

echo.
echo [오류] 실행 환경을 만들지 못했습니다.
echo        위 메시지를 그대로 알려주시면 고쳐드립니다.
pause
exit /b 1

rem ============================================================ 라이브러리 설치
:install
echo.
echo 라이브러리를 내려받습니다. 인터넷 속도에 따라 5~20분 걸립니다...
echo.
"%PY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto installfail

copy /y "%~dp0requirements.txt" "%STAMP%" >nul

echo.
echo 기본 효과음을 만듭니다...
"%PY%" -m yeneung sfx generate

echo.
echo ============================================
echo   준비 끝. 이제 바로 쓰시면 됩니다.
echo ============================================
echo.
goto run

:installfail
echo.
echo [오류] 설치에 실패했습니다. 위 메시지를 그대로 알려주시면 고쳐드립니다.
echo        인터넷 연결이나 회사 방화벽 때문일 수도 있습니다.
pause
exit /b 1

rem ============================================================ 최신인지 확인
:ready
fc /b "%~dp0requirements.txt" "%STAMP%" >nul 2>&1
if not errorlevel 1 goto run
echo 프로그램이 갱신되어 필요한 것을 다시 설치합니다...
goto install

rem ============================================================ 실행
:run
if not "%~1"=="" set "VIDEO=%~1"
if defined VIDEO goto gotvideo

echo ============================================
echo   yeneung - 컷편집 영상을 예능으로
echo ============================================
echo.
echo 영상 파일을 이 창에 끌어다 놓고 엔터를 누르세요.
echo (다음부터는 실행.bat 아이콘 위에 영상을 끌어다 놓아도 됩니다)
echo.
set /p VIDEO="영상 파일: "

:gotvideo
if not defined VIDEO goto novideo
set VIDEO=%VIDEO:"=%
if not exist "%VIDEO%" goto notfound

echo.
"%PY%" -m yeneung run "%VIDEO%"
set CODE=%errorlevel%

echo.
if not "%CODE%"=="0" goto failed
echo 캡컷을 열어서 확인하세요.
echo 프로젝트 목록에 안 보이면 아무 프로젝트나 열었다 나오거나
echo 캡컷을 재시작하면 갱신됩니다.
echo.
pause
exit /b 0

:failed
echo [실패] 위 메시지를 그대로 알려주시면 고쳐드립니다.
echo.
pause
exit /b %CODE%

:novideo
echo 영상 파일을 지정하지 않았습니다.
pause
exit /b 1

:notfound
echo [오류] 파일을 찾을 수 없습니다: %VIDEO%
pause
exit /b 1
