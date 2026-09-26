#!/usr/bin/env python3
"""
바이낸스 선물 자동매매 봇 - 실행 파일

실행 방법:
  python 9_main.py
  또는 더블클릭!

필요한 파일 (같은 폴더):
  1_config.py
  2_api.py
  3_indicators.py
  4_bot.py
  5_gui.py
  9_main.py (이 파일)

오류가 나도 창이 바로 닫히지 않고, 내용이 '오류기록.txt'에 남습니다.
"""

import sys
import os
import traceback
import threading
import faulthandler
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ERROR_LOG = os.path.join(BASE_DIR, '오류기록.txt')

# 콘솔이 이모지·한글을 못 찍어도 죽지 않게
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

# 🧯 파이썬 자체가 강제 종료되는 경우(메시지 없이 꺼짐)까지 파일에 남긴다
try:
    _crash_file = open(ERROR_LOG, 'a', encoding='utf-8')
    faulthandler.enable(_crash_file)
except Exception:
    _crash_file = None


import re

def mask_keys(text):
    """API 키처럼 긴 영문·숫자 덩어리는 앞뒤만 남기고 가린다 (기록을 남에게 보내도 안전하게)."""
    return re.sub(r'[A-Za-z0-9]{32,}', lambda m: m.group(0)[:4] + '…(가림)…' + m.group(0)[-3:], str(text))


def write_error_log(title, text):
    """오류 내용을 오류기록.txt에 덧붙인다. (API 키는 가려서 저장)"""
    text = mask_keys(text)
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {title}\n")
            f.write("=" * 70 + "\n")
            f.write(text + "\n")
    except Exception:
        pass


def pause_and_exit(code=1):
    """창이 바로 닫히지 않게 엔터를 기다렸다가 종료."""
    try:
        input("\n[엔터]를 누르면 창이 닫힙니다...")
    except Exception:
        pass
    sys.exit(code)


def explain_config_error(exc):
    """문법 오류를 알아보기 쉽게 설명. 1_config.py가 아닌 파일이면 원본으로 되돌리라고 안내."""
    lineno = getattr(exc, 'lineno', None)
    bad = mask_keys((getattr(exc, 'text', '') or '').rstrip())
    fname = os.path.basename(str(getattr(exc, 'filename', '') or ''))
    if fname and fname != '1_config.py':
        print()
        print("=" * 60)
        print(f"❌ {fname} 파일이 수정되어 실행할 수 없습니다")
        print("=" * 60)
        if lineno:
            print(f"   {lineno}번째 줄: {bad}")
        print(f"   오류: {getattr(exc, 'msg', exc)}")
        print()
        print(f"   {fname} 은(는) 고치는 파일이 아닙니다.")
        print(f"   → 받은 압축파일에서 {fname} 만 꺼내 다시 덮어쓰세요.")
        print()
        print("   🔑 API 키는 1_config.py 에만 넣습니다:")
        print('      API_KEY = "키"')
        print('      API_SECRET = "시크릿"')
        return
    print()
    print("=" * 60)
    print("❌ 1_config.py 파일에 문제가 있습니다")
    print("=" * 60)
    if lineno:
        print(f"   {lineno}번째 줄: {bad}")
    print(f"   오류: {getattr(exc, 'msg', exc)}")
    print()
    print("   자주 있는 원인:")
    print('   1) 따옴표가 빠짐        →  API_KEY = "키"   처럼 앞뒤 모두 " 로 감싸기')
    print('   2) 둥근 따옴표 “ ” 사용  →  키보드의 일반 따옴표 " 로 바꾸기')
    print("   3) 키 안에 줄바꿈/공백   →  키를 한 줄로 붙여넣기")
    print("   4) 저장 인코딩 문제      →  메모장 '다른 이름으로 저장' → 인코딩 'UTF-8'")
    print()
    print("   고친 뒤 저장하고 다시 실행하세요.")


def on_uncaught(exc_type, exc, tb):
    """어디서든 잡히지 않은 오류 → 기록하고 창 유지."""
    if issubclass(exc_type, KeyboardInterrupt):
        print("\n프로그램을 종료합니다...")
        return
    text = ''.join(traceback.format_exception(exc_type, exc, tb))
    write_error_log("프로그램 오류로 종료", text)
    fname = str(getattr(exc, 'filename', '') or '')
    if issubclass(exc_type, SyntaxError) and os.path.dirname(os.path.abspath(fname)) == BASE_DIR:
        explain_config_error(exc)
    else:
        print("\n" + "=" * 60)
        print("❌ 프로그램 실행 중 오류 발생!")
        print("=" * 60)
        print(mask_keys(text))
    print(f"📄 오류 내용이 저장됐습니다: {ERROR_LOG}")
    print("   이 파일 내용을 보내주시면 원인을 찾을 수 있습니다. (API 키는 가려져 있습니다)")
    try:
        input("\n[엔터]를 누르면 창이 닫힙니다...")
    except Exception:
        pass


def on_thread_error(args):
    """백그라운드 스레드 오류 — 프로그램은 계속 돌고, 기록만 남긴다."""
    text = ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    name = getattr(args.thread, 'name', '?')
    write_error_log(f"백그라운드 작업 오류 (스레드 {name}) — 프로그램은 계속 실행", text)
    print(f"\n⚠️ 백그라운드 작업 오류 (프로그램은 계속 실행): {args.exc_value}")
    print(f"   자세한 내용: {ERROR_LOG}")


sys.excepthook = on_uncaught
threading.excepthook = on_thread_error


# ==================== 자동 설치 ====================
import subprocess

print("=" * 60)
print("🚀 바이낸스 선물 자동매매 봇")
print("=" * 60)
print()
print("🔍 필수 라이브러리 확인 중...")

required_packages = {
    'pandas': 'pandas',
    'numpy': 'numpy',
    'requests': 'requests',
    'openpyxl': 'openpyxl'
}

missing_packages = []

for module_name, package_name in required_packages.items():
    try:
        __import__(module_name)
        print(f"   ✅ {package_name}")
    except ImportError:
        print(f"   ❌ {package_name} - 누락됨")
        missing_packages.append(package_name)

if missing_packages:
    print()
    print(f"📦 {len(missing_packages)}개 라이브러리 자동 설치 시작...")
    print()

    for package in missing_packages:
        try:
            print(f"   설치 중: {package}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print(f"   ✅ {package} 설치 완료!")
        except Exception as e:
            print(f"   ❌ {package} 설치 실패: {e}")
            print()
            print("수동 설치: pip install pandas numpy requests openpyxl")
            write_error_log("라이브러리 설치 실패", f"{package}: {e}")
            pause_and_exit(1)

    print()
    print("✅ 모든 라이브러리 설치 완료!")
    print()
else:
    print()
    print("✅ 모든 라이브러리 준비 완료!")
    print()

# ==================== 모듈 Import ====================
print("📂 모듈 로딩 중...")

try:
    import tkinter as tk
    print("   ✅ tkinter")
except ImportError:
    print("   ❌ tkinter 필요")
    print("Linux: sudo apt-get install python3-tk")
    pause_and_exit(1)

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import importlib

# 1_config.py는 사용자가 직접 고치는 파일이라 따로 먼저 검사한다
try:
    config_module = importlib.import_module('1_config')
    print("   ✅ 1_config.py")
except (SyntaxError, UnicodeDecodeError) as e:
    write_error_log("1_config.py 문법 오류", traceback.format_exc())
    explain_config_error(e)
    pause_and_exit(1)
except ImportError as e:
    print(f"   ❌ 1_config.py 를 찾을 수 없습니다: {e}")
    print("   9_main.py 와 같은 폴더에 있는지 확인하세요.")
    pause_and_exit(1)

try:
    api_module = importlib.import_module('2_api')
    print("   ✅ 2_api.py")

    indicators_module = importlib.import_module('3_indicators')
    print("   ✅ 3_indicators.py")

    bot_module = importlib.import_module('4_bot')
    print("   ✅ 4_bot.py")

    gui_module = importlib.import_module('5_gui')
    App = gui_module.App
    print("   ✅ 5_gui.py")

except ImportError as e:
    print(f"   ❌ 모듈 로딩 실패: {e}")
    print()
    print("다음 파일들이 같은 폴더에 있는지 확인하세요:")
    print("  1_config.py, 2_api.py, 3_indicators.py")
    print("  4_bot.py, 5_gui.py, 9_main.py")
    write_error_log("모듈 로딩 실패", traceback.format_exc())
    pause_and_exit(1)
except SyntaxError as e:
    write_error_log("모듈 로딩 중 문법 오류", traceback.format_exc())
    explain_config_error(e)
    print(f"\n📄 오류 내용: {ERROR_LOG}")
    pause_and_exit(1)
except Exception:
    # 파일이 깨졌거나 버전이 섞인 경우 등 — 메시지 보여주고 창 유지
    write_error_log("모듈 로딩 중 오류", traceback.format_exc())
    print("\n❌ 프로그램 파일을 불러오는 중 오류가 났습니다:\n")
    print(mask_keys(traceback.format_exc()))
    print("\n   압축파일의 모든 파일을 한 폴더에 다시 덮어써 보세요.")
    print(f"📄 오류 내용: {ERROR_LOG}")
    pause_and_exit(1)

print()
print("=" * 60)
print("✅ 모든 준비 완료! GUI 시작...")
print("=" * 60)
print()


# ==================== 메인 실행 ====================
def main():
    app = None
    try:
        root = tk.Tk()

        # 화면 버튼·타이머 안에서 난 오류도 기록 (프로그램은 계속 실행)
        def on_tk_error(exc, val, tb):
            text = ''.join(traceback.format_exception(exc, val, tb))
            write_error_log("화면 동작 중 오류 — 프로그램은 계속 실행", text)
            print(f"\n⚠️ 화면 동작 중 오류 (프로그램은 계속 실행): {val}")
            print(f"   자세한 내용: {ERROR_LOG}")
        root.report_callback_exception = on_tk_error

        app = App(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다...")
        return
    except Exception:
        text = traceback.format_exc()
        write_error_log("프로그램 실행 중 오류", text)
        print("\n" + "=" * 60)
        print("❌ 프로그램 실행 중 오류 발생!")
        print("=" * 60)
        print(text)
        print(f"📄 오류 내용이 저장됐습니다: {ERROR_LOG}")
        pause_and_exit(1)

    # 사용자가 X 버튼으로 닫은 게 아닌데 화면이 사라졌다면 알려준다
    if app is not None and not getattr(app, '_closing_by_user', False):
        write_error_log("화면이 예상치 않게 닫힘", "mainloop 종료 (사용자 종료 아님)")
        print("\n⚠️ 화면이 예상치 않게 닫혔습니다.")
        print(f"📄 {ERROR_LOG} 파일 내용을 보내주세요.")
        pause_and_exit(1)


if __name__ == "__main__":
    main()
