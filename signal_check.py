#!/usr/bin/env python3
"""신호 점검 — 코인마다 봇 신호가 트레이딩뷰(UT Bot Alerts 10/5 + EMA 34/55)와 같은지 실제 바이낸스 차트로 확인

  python signal_check.py              (봇의 20개 코인)
  python signal_check.py HBAR FIL     (원하는 코인만)
  python signal_check.py --days 7     (최근 7일 스위칭 비교, 기본 3일)

세 가지를 나란히 계산한다.
  · 트뷰  : 긴 기록(1,500봉) 전체로 계산 — 트레이딩뷰 차트와 같은 값
  · 새 봇 : 최근 499봉 (지금 봇)
  · 옛 봇 : 최근 200봉 (예전 봇 — UT 시작 상태를 잘못 잡아 '혼자 스위칭'이 났음)

API 키 필요 없음 (공개 차트만 읽음). 결과는 신호점검.txt 에도 저장.
"""
import importlib.util
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests

MAINNET = os.environ.get('SIGNAL_CHECK_MAINNET', 'https://fapi.binance.com')
HERE = os.path.dirname(os.path.abspath(__file__))
TF = '15m'
BAR_MS = 15 * 60 * 1000
UT_KEY, UT_ATR, EMA_F, EMA_S = 10, 5, 34, 55
NEW_BARS, OLD_BARS, HIST = 499, 200, 1500

_spec = importlib.util.spec_from_file_location('ind', os.path.join(HERE, '3_indicators.py'))
_ind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ind)
I = _ind.Indicators


def bot_coins():
    """5_gui.py 의 프로그램 1·2 코인 목록"""
    try:
        src = open(os.path.join(HERE, '5_gui.py'), encoding='utf-8').read()
        block = src[src.index('PROGRAM_COINS = {'):src.index('3: [')]
        return re.findall(r"'symbol': '([A-Z0-9]+)/USDT'", block)
    except Exception:
        return ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'TRX', 'SUI', 'LINK',
                'AVAX', 'LTC', 'BCH', 'DOT', 'XLM', 'HBAR', 'ETC', 'FIL', 'AAVE', 'ATOM']


def klines(coin):
    r = requests.get(f'{MAINNET}/fapi/v1/klines',
                     params={'symbol': f'{coin}USDT', 'interval': TF, 'limit': HIST}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame([[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in rows],
                      columns=['t', 'open', 'high', 'low', 'close', 'volume'])
    return df.set_index('t')


def direction(ut_pos, ema_up):
    if ut_pos == 1 and ema_up:
        return 1
    if ut_pos == -1 and not ema_up:
        return -1
    return 0


def window_dir(closed, t, n):
    """마감봉 t 까지 최근 n 봉만으로 계산한 신호 방향 (봉 마감 기준)"""
    w = closed.iloc[max(0, t - n + 1):t + 1]
    s = I.get_signals(w, UT_KEY, UT_ATR, EMA_F, EMA_S)
    return 1 if (s['ut_position_long'] and s['ema_long']) else (-1 if (s['ut_position_short'] and s['ema_short']) else 0)


def switches(dirs, times):
    """방향(롱↔숏)이 바뀐 봉 → [(시각, 1/-1)] — 신호 없음(0)은 이전 방향 유지"""
    out, last = [], None
    for d, t in zip(dirs, times):
        if d == 0:
            continue
        if last is not None and d != last:
            out.append((t, d))
        last = d
    return out


def hm(ms):
    return datetime.fromtimestamp((ms + BAR_MS) / 1000).strftime('%m/%d %H:%M')   # 봉이 끝난 시각 (내 PC 시간)


NAME = {1: '롱', -1: '숏', 0: '대기'}


def check(coin, days):
    df = klines(coin)
    closed = df.iloc[:-1]
    ut_full = I.ut_bot(closed, UT_KEY, UT_ATR).values
    ef = closed['close'].ewm(span=EMA_F, adjust=False).mean().values
    es = closed['close'].ewm(span=EMA_S, adjust=False).mean().values

    # 지금 (봇과 같은 방식: UT 는 마감봉, EMA 는 진행 중인 봉 포함)
    live_f = df['close'].ewm(span=EMA_F, adjust=False).mean().iloc[-1]
    live_s = df['close'].ewm(span=EMA_S, adjust=False).mean().iloc[-1]
    tv_now = direction(ut_full[-1], live_f > live_s)
    w = df.iloc[-NEW_BARS:]
    s = I.get_signals(w, UT_KEY, UT_ATR, EMA_F, EMA_S, ut_df=w.iloc[:-1])
    new_ut = 1 if s['ut_position_long'] else (-1 if s['ut_position_short'] else 0)
    new_now = direction(new_ut, s['ema_long'])

    # 최근 days 일 스위칭 (봉 마감 기준)
    n = max(0, min(len(closed) - NEW_BARS, days * 96))
    idx = range(len(closed) - n, len(closed))
    times = [closed.index[t] for t in idx]
    tv = switches([direction(ut_full[t], ef[t] > es[t]) for t in idx], times)
    new = switches([window_dir(closed, t, NEW_BARS) for t in idx], times)
    old = switches([window_dir(closed, t, OLD_BARS) for t in idx], times)
    tvset = set(tv)
    fake_old = [x for x in old if x not in tvset]
    fake_new = [x for x in new if x not in tvset]

    lines = []
    ok = (new_now == tv_now) and (new_ut == ut_full[-1])
    lines.append(f"{coin:<5} 지금 트뷰 {NAME[tv_now]:<2} (UT {'Buy' if ut_full[-1] == 1 else 'Sell'} · EMA34 {'위' if live_f > live_s else '아래'})"
                 f" | 새 봇 {NAME[new_now]:<2} {'✅ 같음' if ok else '❌ 다름'}")
    lines.append(f"      최근 {days}일 스위칭: 트뷰 {len(tv)}번 · 새 봇 {len(new)}번"
                 f"{' (트뷰와 다른 것 ' + str(len(fake_new)) + '번)' if fake_new else ''}"
                 f" · 예전 봇 {len(old)}번" + (f" — 가짜 {len(fake_old)}번: " + ', '.join(f"{hm(t)} {NAME[d]}" for t, d in fake_old[:6])
                                             + (' …' if len(fake_old) > 6 else '') if fake_old else ''))
    return ok and not fake_new, len(fake_old), lines


def main():
    args = [a for a in sys.argv[1:]]
    days = 3
    if '--days' in args:
        i = args.index('--days')
        days = max(1, int(args[i + 1]))
        del args[i:i + 2]
    coins = [a.upper().replace('USDT', '') for a in args] or bot_coins()
    out = [f"🔍 신호 점검 {datetime.now():%Y-%m-%d %H:%M} — {TF} · UT Bot {UT_KEY}/{UT_ATR} · EMA {EMA_F}/{EMA_S}",
           f"   트뷰 = 긴 기록 {HIST}봉 (트레이딩뷰와 같은 값) · 새 봇 = 최근 {NEW_BARS}봉 · 예전 봇 = 최근 {OLD_BARS}봉",
           "   시각은 봉이 끝난 시각 (내 PC 시간). 스위칭 비교는 봉 마감 기준", '']
    for line in out:
        print(line)
    good = fake_total = 0
    for coin in coins:
        try:
            ok, nf, lines = check(coin, days)
            good += ok
            fake_total += nf
        except Exception as e:
            lines = [f"{coin:<5} ⚠️ 확인 실패: {e}"]
        for line in lines:
            print(line, flush=True)
        out += lines
        time.sleep(0.3)
    tail = ['', f"결과: {good}/{len(coins)} 코인 새 봇 = 트뷰 · 예전 봇 가짜 스위칭 합계 {fake_total}번 (최근 {days}일)"]
    for line in tail:
        print(line)
    out += tail
    try:
        with open(os.path.join(HERE, '신호점검.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
        print(f"📄 저장: {os.path.join(HERE, '신호점검.txt')}")
    except Exception as e:
        print(f"⚠️ 파일 저장 실패: {e}")


if __name__ == '__main__':
    main()
