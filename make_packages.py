#!/usr/bin/env python3
"""본인용 압축파일 만들기 (배포 안 함)

  python make_packages.py

  dist/바이낸스봇_전체_키포함.zip  → 프로그램1·2 폴더 둘 다, 1_config.py 의 키 그대로
      프로그램1_코인1-10   (BTC ETH BNB SOL XRP ADA DOGE TRX SUI LINK)
      프로그램2_코인11-20  (AVAX LTC BCH DOT XLM HBAR ETC FIL AAVE ATOM)

  키를 뺀 프로그램별 압축이 필요할 때만: python make_packages.py --keyless

두 프로그램의 파일은 전부 같고, 9_main.py 의 DEFAULT_PROGRAM 한 줄만 다르다.
"""
import os
import re
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
FILES = ['1_config.py', '2_api.py', '3_indicators.py', '4_bot.py', '5_gui.py',
         '6_gui_panels.py', '7_gui_controls.py', '8_gui_signals.py', '9_main.py',
         'telegram_bot.py', 'requirements.txt', 'README.md', '실행방법.md']
EXTRA_1 = ['backtest.py', 'btc_1year.py', 'goldfib_bot.py', 'real_chart_check.py', 'testnet_book_check.py']   # 별개 프로그램 (1번에만)
PACKS = {1: '프로그램1_코인1-10', 2: '프로그램2_코인11-20'}
# 3: '프로그램3_코인21-30'  ← 🚧 준비 중 (지금은 UNI 1개). 코인 다 정하면 주석 풀기


def _add_program(z, num, name, keep_keys=False):
    files = FILES + (EXTRA_1 if num == 1 else [])
    for f in files:
        with open(os.path.join(BASE, f), encoding='utf-8') as fh:
            text = fh.read()
        if f == '1_config.py' and not keep_keys:
            # 🔑 저장소의 키를 압축에 넣지 않는다 (덮어쓰면 다른 계정으로 주문이 나가던 문제)
            text, n1 = re.subn(r'^API_KEY = ".*"', 'API_KEY = "여기에_API_키"', text, flags=re.M)
            text, n2 = re.subn(r'^API_SECRET = ".*"', 'API_SECRET = "여기에_시크릿_키"', text, flags=re.M)
            assert n1 == 1 and n2 == 1, 'API 키 줄을 못 찾음'
        if f == '9_main.py':
            text, n = re.subn(r'^DEFAULT_PROGRAM = \d+', f'DEFAULT_PROGRAM = {num}', text, flags=re.M)
            assert n == 1, 'DEFAULT_PROGRAM 줄을 못 찾음'
        z.writestr(f'{name}/{f}', text.encode('utf-8'))


def build(num, name):
    os.makedirs(os.path.join(BASE, 'dist'), exist_ok=True)
    out = os.path.join(BASE, 'dist', f'{name}.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        _add_program(z, num, name)
    print(f'✅ {out}')
    return out


def build_all_with_keys():
    """본인용: 프로그램 1·2를 한 압축에, 1_config.py 의 키 그대로 (기본)"""
    os.makedirs(os.path.join(BASE, 'dist'), exist_ok=True)
    out = os.path.join(BASE, 'dist', '바이낸스봇_전체_키포함.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for num, name in PACKS.items():
            _add_program(z, num, name, keep_keys=True)
    print(f'✅ {out}')
    return out


if __name__ == '__main__':
    import sys
    if '--keyless' in sys.argv:
        for num, name in PACKS.items():
            build(num, name)
    build_all_with_keys()
