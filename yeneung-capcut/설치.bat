@echo off
cd /d "%~dp0"
title yeneung 설치

echo ============================================
echo   yeneung 설치
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [오류] python 을 찾을 수 없습니다.
  echo.
  echo   https://www.python.org/downloads/ 에서 설치하세요.
  echo   설치할 때 "Add Python to PATH" 를 반드시 체크해야 합니다.
  echo.
  pause
  exit /b 1
)
python --version
echo.

echo 필요한 것들을 설치합니다. 몇 분 걸립니다...
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [오류] 설치에 실패했습니다. 위 메시지를 그대로 알려주세요.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   환경 점검
echo ============================================
echo.
python -m yeneung doctor

echo.
echo 설치가 끝났습니다.
echo.
echo 다음 할 일:
echo   1^) API 키 등록. cmd 에서 아래를 실행하고 cmd 를 껐다 켜세요.
echo         setx ANTHROPIC_API_KEY "sk-ant-..."
echo   2^) 실행.bat 위에 영상 파일을 끌어다 놓기
echo.
pause
