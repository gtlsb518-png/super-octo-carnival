#!/usr/bin/env python3
"""테스트넷 호가 점검 — 코인마다 '시장가로 사고팔면 실제로 얼마에 체결되는지' 확인

  python testnet_book_check.py            (봇의 20개 코인 + 교체 후보)
  python testnet_book_check.py NEAR FIL   (원하는 코인만)

테스트넷은 연습용이라 코인에 따라 호가가 얇거나 실제 시장과 몇 % 떨어져 있다 (예: NEAR).
그런 코인은 봇이 익절 주문을 내도 시장가가 크게 미끄러져 '익절인데 손해'가 난다.
API 키는 필요 없다 (공개 정보만 읽음). 결과는 테스트넷_호가점검.txt 에도 저장.

판정 (진입금 50 × 5배 = 약 $250 를 시장가로 샀다가 바로 판다고 칠 때 왕복 비용):
  ✅ 0.15% 이하   정상 (실제 시장 수준)
  ⚠️ 0.15~0.5%    조금 불리 (익절 몫이 줄어듦)
  ❌ 0.5% 넘음    비정상 — 이 코인은 테스트넷에서 익절해도 손해가 날 수 있음
"""
import os
import re
import sys
import time

import requests

TESTNET = os.environ.get('BOOK_CHECK_TESTNET', 'https://testnet.binancefuture.com')
MAINNET = os.environ.get('BOOK_CHECK_MAINNET', 'https://fapi.binance.com')
NOTIONAL = 250.0                       # 진입금 50 × 5배
CANDIDATES = ['NEAR', 'FIL', 'APT', 'ARB', 'OP', 'UNI', 'INJ', 'ICP', 'ALGO', 'FET']
HERE = os.path.dirname(os.path.abspath(__file__))


def bot_coins():
    """5_gui.py 의 프로그램 1·2 코인 목록"""
    try:
        src = open(os.path.join(HERE, '5_gui.py'), encoding='utf-8').read()
        block = src[src.index('PROGRAM_COINS = {'):src.index('3: [')]
        return re.findall(r"'symbol': '([A-Z0-9]+)/USDT'", block)
    except Exception:
        return ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'TRX', 'SUI', 'LINK',
                'AVAX', 'LTC', 'BCH', 'DOT', 'XLM', 'HBAR', 'ETC', 'FIL', 'AAVE', 'ATOM']


def get(base, path, **params):
    r = requests.get(base + path, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def fill_price(levels, notional):
    """호가를 위에서부터 먹어 들어갈 때 평균 체결가. 호가가 모자라면 None"""
    left, cost, qty = notional, 0.0, 0.0
    for px, q in levels:
        px, q = float(px), float(q)
        take = min(left, px * q)
        cost += take
        qty += take / px
        left -= take
        if left <= 1e-9:
            return cost / qty
    return None


def check(coin):
    sym = coin + 'USDT'
    main = float(get(MAINNET, '/fapi/v1/ticker/price', symbol=sym)['price'])
    book = get(TESTNET, '/fapi/v1/depth', symbol=sym, limit=50)
    mark = float(get(TESTNET, '/fapi/v1/premiumIndex', symbol=sym)['markPrice'])
    buy = fill_price(book.get('asks', []), NOTIONAL)
    sell = fill_price(book.get('bids', []), NOTIONAL)
    if not buy or not sell:
        return dict(coin=coin, main=main, mark=mark, buy=buy, sell=sell, rt=None, gap=None, verdict='❌ 호가 부족')
    rt = (buy - sell) / main * 100                       # 샀다가 바로 팔 때 왕복 비용
    gap = ((buy + sell) / 2 - main) / main * 100         # 테스트넷 가격이 실제 시장과 얼마나 다른지
    worst = max(rt, abs(gap) * 2)
    verdict = '✅ 정상' if worst <= 0.15 else '⚠️ 조금 불리' if worst <= 0.5 else '❌ 비정상'
    return dict(coin=coin, main=main, mark=mark, buy=buy, sell=sell, rt=rt, gap=gap, verdict=verdict)


def fmt(p):
    if p is None:
        return '-'
    return f"{p:,.2f}" if p >= 100 else f"{p:.4f}" if p >= 1 else f"{p:.6f}"


def main():
    args = [a.upper().replace('USDT', '') for a in sys.argv[1:]]
    mine = bot_coins()
    coins = args or (mine + [c for c in CANDIDATES if c not in mine])
    lines = [f"테스트넷 호가 점검 ({time.strftime('%Y-%m-%d %H:%M')}) — 약 ${NOTIONAL:.0f} 시장가로 샀다가 판다고 칠 때",
             f"{'코인':<6} {'실제시장':>12} {'테스트넷 살때':>13} {'팔때':>12} {'왕복비용':>8} {'시장과차이':>9}  판정"]
    print('\n'.join(lines), flush=True)
    rows = []
    for c in coins:
        try:
            r = check(c)
        except requests.HTTPError as e:
            r = dict(coin=c, verdict=f"❌ 조회 실패 ({e.response.status_code} — 테스트넷에 없는 코인일 수 있음)")
        except Exception as e:
            r = dict(coin=c, verdict=f"❌ 조회 실패 ({type(e).__name__})")
        rows.append(r)
        tag = '' if c in mine else '  (후보)'
        if r.get('rt') is None:
            lines.append(f"{c:<6} {fmt(r.get('main')):>12} {fmt(r.get('buy')):>13} {fmt(r.get('sell')):>12} "
                         f"{'-':>8} {'-':>9}  {r['verdict']}{tag}")
        else:
            lines.append(f"{c:<6} {fmt(r['main']):>12} {fmt(r['buy']):>13} {fmt(r['sell']):>12} "
                         f"{r['rt']:>7.2f}% {r['gap']:>+8.2f}%  {r['verdict']}{tag}")
        print(lines[-1], flush=True)
        time.sleep(0.2)

    summary = ['']
    bad = [r['coin'] for r in rows if r['coin'] in mine and not r['verdict'].startswith('✅')]
    good = sorted([r for r in rows if r['coin'] not in mine and r.get('rt') is not None and r['verdict'].startswith('✅')],
                  key=lambda r: r['rt'])
    if bad:
        summary.append(f"⚠️ 봇 코인 중 테스트넷 호가가 정상이 아닌 것: {', '.join(bad)}")
        summary.append("   → 테스트넷에서는 익절해도 손해가 날 수 있습니다. 메인넷은 해당 없음.")
        if good:
            summary.append(f"   교체 후보(테스트넷 정상, 왕복비용 낮은 순): {', '.join(r['coin'] for r in good[:5])}")
    else:
        summary.append("✅ 봇 코인 모두 테스트넷 호가 정상")
    print('\n'.join(summary))
    lines += summary
    try:
        with open(os.path.join(HERE, '테스트넷_호가점검.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print(f"\n📄 결과 저장: 테스트넷_호가점검.txt")
    except Exception:
        pass


if __name__ == '__main__':
    print(__doc__.split('\n\n')[0])
    main()
    if os.name == 'nt' and not sys.argv[1:]:
        input('\n[엔터]를 누르면 창이 닫힙니다...')
