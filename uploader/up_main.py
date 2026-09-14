#!/usr/bin/env python3
"""
유튜브 / 틱톡 자동 업로드 프로그램 - 실행 파일

실행 방법:
  python up_main.py
  또는 더블클릭!

필요한 파일 (같은 폴더):
  up_browser.py
  up_youtube.py
  up_tiktok.py
  up_gui.py
  up_main.py (이 파일)
"""

import os
import sys
import subprocess

print("=" * 60)
print("🎬 유튜브 / 틱톡 자동 업로드 프로그램")
print("=" * 60)
print()

# ==================== 파이썬 버전 확인 ====================

if sys.version_info < (3, 8):
    print("❌ 파이썬 3.8 이상이 필요합니다.")
    print(f"   현재 버전: {sys.version.split()[0]}")
    input("\n엔터를 누르면 종료합니다...")
    sys.exit(1)

# ==================== 자동 설치 ====================

print("🔍 필수 라이브러리 확인 중...")

required = {
    "playwright": "playwright",   # 브라우저 자동 조작
}

missing = []
for module_name, package_name in required.items():
    try:
        __import__(module_name)
        print(f"  ✅ {package_name}")
    except ImportError:
        print(f"  ❌ {package_name} (미설치)")
        missing.append(package_name)

if missing:
    print()
    print(f"📦 {len(missing)}개 라이브러리를 설치합니다...")
    for package in missing:
        print(f"   설치 중: {package}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
            )
            print(f"   ✅ {package} 설치 완료")
        except subprocess.CalledProcessError:
            print(f"   ❌ {package} 설치 실패")
            print(f"      직접 설치하세요: pip install {package}")
            input("\n엔터를 누르면 종료합니다...")
            sys.exit(1)

# tkinter 는 pip 설치가 안 되므로 따로 안내
try:
    import tkinter  # noqa: F401
except ImportError:
    print()
    print("❌ tkinter 가 없습니다.")
    print("   윈도우: 파이썬 재설치 시 'tcl/tk' 옵션 체크")
    print("   우분투: sudo apt install python3-tk")
    input("\n엔터를 누르면 종료합니다...")
    sys.exit(1)

print()
print("✅ 준비 완료! 프로그램을 실행합니다.")
print()
print("-" * 60)
print("사용 순서")
print("  1) 크롬을 완전히 종료 → [① 크롬 로그인 정보 가져오기]  (최초 1회만)")
print("  2) [② 업로드용 크롬 열기] → 로그인 상태 확인")
print("  3) 제목/상세정보/태그/파일/썸네일 입력")
print("  4) [유튜브 업로드] 또는 [틱톡 업로드] 버튼 클릭")
print("-" * 60)
print()

# ==================== GUI 실행 ====================

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import up_gui  # noqa: E402

up_gui.main()
