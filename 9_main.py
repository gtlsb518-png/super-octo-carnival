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
"""

import subprocess
import sys
import os

# ==================== 자동 설치 ====================
print("=" * 60)
print("🚀 바이낸스 선물 자동매매 봇")
print("=" * 60)
print()
print("🔍 필수 라이브러리 확인 중...")

# 필요한 라이브러리 목록
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
            input("아무 키나 눌러 종료...")
            sys.exit(1)
    
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
    input("\n아무 키나 눌러 종료...")
    sys.exit(1)

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    import importlib
    
    config_module = importlib.import_module('1_config')
    print("   ✅ 1_config.py")
    
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
    input("\n아무 키나 눌러 종료...")
    sys.exit(1)

print()
print("=" * 60)
print("✅ 모든 준비 완료! GUI 시작...")
print("=" * 60)
print()

# ==================== 메인 실행 ====================
def main():
    try:
        root = tk.Tk()
        app = App(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다...")
    except Exception as e:
        print("\n" + "=" * 60)
        print("❌ 프로그램 실행 중 오류 발생!")
        print("=" * 60)
        print(f"\n오류 내용: {e}\n")
        import traceback
        traceback.print_exc()
        input("\n아무 키나 눌러 종료...")

if __name__ == "__main__":
    main()
