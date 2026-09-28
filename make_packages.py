#!/usr/bin/env python3
"""배포용 압축파일 2개 만들기

  python make_packages.py

  dist/프로그램1_코인1-10.zip   → 프로그램1 폴더 (BTC ETH BNB SOL XRP ADA DOGE TRX SUI LINK)
  dist/프로그램2_코인11-20.zip  → 프로그램2 폴더 (AVAX LTC BCH DOT XLM HBAR ETC NEAR AAVE ATOM)

두 프로그램의 파일은 전부 같고, 9_main.py 의 DEFAULT_PROGRAM 한 줄만 다르다.
"""
import os
import re
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
FILES = ['1_config.py', '2_api.py', '3_indicators.py', '4_bot.py', '5_gui.py',
         '6_gui_panels.py', '7_gui_controls.py', '8_gui_signals.py', '9_main.py',
         'requirements.txt', 'README.md', '실행방법.md']
EXTRA_1 = ['backtest.py', 'btc_1year.py', 'goldfib_bot.py']   # 별개 프로그램 (1번에만)
PACKS = {1: '프로그램1_코인1-10', 2: '프로그램2_코인11-20'}
# 3: '프로그램3_코인21-30'  ← 🚧 준비 중 (지금은 UNI 1개). 코인 다 정하면 주석 풀기


def build(num, name):
    os.makedirs(os.path.join(BASE, 'dist'), exist_ok=True)
    out = os.path.join(BASE, 'dist', f'{name}.zip')
    files = FILES + (EXTRA_1 if num == 1 else [])
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            with open(os.path.join(BASE, f), encoding='utf-8') as fh:
                text = fh.read()
            if f == '9_main.py':
                text, n = re.subn(r'^DEFAULT_PROGRAM = \d+', f'DEFAULT_PROGRAM = {num}', text, flags=re.M)
                assert n == 1, 'DEFAULT_PROGRAM 줄을 못 찾음'
            z.writestr(f'{name}/{f}', text.encode('utf-8'))
    print(f'✅ {out}')
    return out


if __name__ == '__main__':
    for num, name in PACKS.items():
        build(num, name)
