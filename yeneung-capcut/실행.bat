@echo off
cd /d "%~dp0"
title yeneung 실행

if "%~1"=="" (
  echo ============================================
  echo   yeneung 실행
  echo ============================================
  echo.
  echo 영상 파일을 이 창에 끌어다 놓고 엔터를 누르세요.
  echo 다음부터는 실행.bat 위에 영상을 끌어다 놓으면 바로 실행됩니다.
  echo.
  set /p VIDEO="영상 파일: "
) else (
  set "VIDEO=%~1"
)

if not defined VIDEO (
  echo 영상 파일을 지정하지 않았습니다.
  pause
  exit /b 1
)

rem 끌어다 놓으면 따옴표가 붙는 경우가 있어 제거한다
set VIDEO=%VIDEO:"=%

if not exist "%VIDEO%" (
  echo [오류] 파일을 찾을 수 없습니다: %VIDEO%
  pause
  exit /b 1
)

echo.
python -m yeneung run "%VIDEO%"
set CODE=%errorlevel%

echo.
if not "%CODE%"=="0" (
  echo [실패] 위 메시지를 그대로 알려주시면 고쳐드립니다.
) else (
  echo 캡컷을 열어서 확인하세요.
  echo 프로젝트 목록에 안 보이면 아무 프로젝트나 열었다 나오거나
  echo 캡컷을 재시작하면 갱신됩니다.
)
echo.
pause
