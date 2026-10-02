#!/usr/bin/env python3
"""실제 바이낸스 차트로 '익절 후 바로 재진입' vs '다음 봉 재진입' 비교

  python real_chart_check.py              ← 프로그램 1·2 코인 20개, 최근 2년
  python real_chart_check.py --days 365   ← 기간 바꾸기
  python real_chart_check.py --coins BTC ETH SOL

- 바이낸스 선물 15분봉을 받아서(키 필요 없음) 1시간봉 봇과 똑같은 규칙으로 계산합니다.
  (봉 안에서 익절·재진입·스위칭이 언제 일어났는지 15분 단위로 따라감)
- 받은 차트는 real_data 폴더에 저장 → 두 번째부터는 새 봉만 받아서 빠릅니다.
- 봇이 돌고 있어도 됩니다. API 사용량을 보면서 천천히 받습니다 (봇 몫을 남겨둠).
- 결과는 화면과 '실제차트_비교결과.txt' 에 남습니다.

⚠️ 펀딩비는 실제 기록이 아니라 8시간마다 0.01% 로 계산합니다 (대부분 기간의 기본값).
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import backtest as bt  # noqa: E402

COINS = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'TRX', 'SUI', 'LINK',
         'AVAX', 'LTC', 'BCH', 'DOT', 'XLM', 'HBAR', 'ETC', 'NEAR', 'AAVE', 'ATOM']
AMOUNT = {'BTC': 60}            # 나머지 50 (1_config.py 기본값과 같게)
DATA_DIR = os.path.join(BASE, 'real_data')
OUT_TXT = os.path.join(BASE, '실제차트_비교결과.txt')

# 비교할 설정: (이름, 레버리지, 신호, 재진입)
CONFIGS = [
    ('예전: 3배·즉시신호·바로재진입', 3, 'live', 'immediate'),
    ('5배·UT마감·바로재진입', 5, 'ut_confirmed', 'immediate'),
    ('지금: 5배·UT마감·다음봉', 5, 'ut_confirmed', 'next_bar'),
    ('3배·UT마감·다음봉', 3, 'ut_confirmed', 'next_bar'),
]

_lines = []


def out(s=''):
    print(s)
    _lines.append(s)


# ==================== 차트 받기 (API 사용량 보면서) ====================
def _get(session, params):
    import requests
    for attempt in range(6):
        try:
            r = session.get('https://fapi.binance.com/fapi/v1/klines', params=params, timeout=20)
            used = int(r.headers.get('X-MBX-USED-WEIGHT-1M', 0) or 0)
            if r.status_code in (418, 429):
                wait = int(r.headers.get('Retry-After', 60) or 60)
                print(f"  ⏸️ 바이낸스 요청 한도 — {wait}초 쉼")
                time.sleep(wait)
                continue
            r.raise_for_status()
            # 같은 IP 에서 봇이 돌고 있을 수 있으니 분당 1,000 넘으면 쉬어 간다 (한도 2,400)
            if used > 1000:
                time.sleep(20)
            else:
                time.sleep(0.4)
            return r.json()
        except requests.RequestException as e:
            print(f"  ⚠️ 재시도 {attempt + 1}/6: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError('차트 다운로드 실패 — 인터넷 연결을 확인하세요')


def load_15m(coin, days, offline=False):
    """real_data/{COIN}USDT_15m.csv 를 쓰고, 모자란 앞·뒤 기간만 새로 받는다"""
    os.makedirs(DATA_DIR, exist_ok=True)
    sym = f'{coin}USDT'
    path = os.path.join(DATA_DIR, f'{sym}_15m.csv')
    df = None
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    if offline:
        if df is None:
            raise RuntimeError(f'{sym}: 저장된 차트가 없습니다 (--offline)')
        return df
    import requests
    s = requests.Session()
    now = int(time.time() * 1000)
    want_start = now - days * 86400 * 1000
    rows = []

    def grab(start, end):
        cur = start
        while cur < end:
            data = _get(s, {'symbol': sym, 'interval': '15m', 'startTime': cur, 'endTime': end, 'limit': 1500})
            if not data:
                break
            rows.extend(data)
            cur = data[-1][6] + 1
            if len(data) < 1500:
                break

    have_start = int(df.index[0].timestamp() * 1000) if df is not None and len(df) else None
    have_end = int(df.index[-1].timestamp() * 1000) if df is not None and len(df) else None
    if have_start is None:
        grab(want_start, now)
    else:
        if want_start < have_start - 15 * 60 * 1000:
            grab(want_start, have_start)
        grab(have_end + 1, now)
    if rows:
        new = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume',
                                          'ct', 'qv', 'n', 'tb', 'tq', 'ig'])
        new = new[['ts', 'open', 'high', 'low', 'close', 'volume']].astype(float)
        new['ts'] = pd.to_datetime(new['ts'], unit='ms')
        new = new.set_index('ts')
        df = new if df is None else pd.concat([df, new])
        df = df[~df.index.duplicated(keep='last')].sort_index()
        df.to_csv(path)
    if df is None or df.empty:
        raise RuntimeError(f'{sym}: 차트가 없습니다 (상장 전이거나 이름이 틀림)')
    return df


# ==================== 계산 ====================
def params(lev, mode, reentry, amount):
    p = dict(bt.DEFAULTS)
    p.update(amount=float(amount), leverage=lev, fee_pct=0.04, ut_sens=10.0, ut_atr=5,
             ema_fast=34, ema_slow=55, adx_period=10, adx_th=21, tp_trend=1.5, tp_sideways=1.2,
             funding_on=True, funding_pct=0.01, funding_hours=8,
             signal_mode=mode, reentry=reentry)
    return p


def main():
    ap = argparse.ArgumentParser(description='실제 차트로 재진입 방식 비교')
    ap.add_argument('--days', type=int, default=730, help='기간 (일, 기본 730 = 2년)')
    ap.add_argument('--coins', nargs='*', default=COINS, help='코인 (예: BTC ETH SOL)')
    ap.add_argument('--offline', action='store_true', help='저장된 차트만 사용 (다운로드 안 함)')
    a = ap.parse_args()
    coins = [c.upper().replace('USDT', '').replace('/', '') for c in a.coins]

    out(f"📊 실제 바이낸스 차트 비교 — 최근 {a.days}일, 코인 {len(coins)}개, 1시간봉 (15분 단위로 따라감)")
    out(f"   진입금 BTC 60 / 나머지 50 USDT · TP 1.2%(횡보)/1.5%(추세) · 손절 없음 · 펀딩 0.01%/8h 가정")
    out('')

    data = {}
    for i, c in enumerate(coins, 1):
        print(f"[{i}/{len(coins)}] {c} 차트 준비 중...", flush=True)
        try:
            df = load_15m(c, a.days, a.offline)
        except Exception as e:
            out(f"  ⚠️ {c}: {e} → 제외")
            continue
        cut = df.index[-1] - pd.Timedelta(days=a.days)
        df = df[df.index >= cut]
        if len(df) < 4 * 24 * 60:
            out(f"  ⚠️ {c}: 차트가 60일보다 짧아서 제외")
            continue
        data[c] = df
    if not data:
        out('❌ 계산할 차트가 없습니다')
        return 1

    res = {name: {} for name, *_ in CONFIGS}       # name → coin → trades
    t0 = time.time()
    for i, (c, df) in enumerate(data.items(), 1):
        print(f"  계산 {i}/{len(data)}: {c}", flush=True)
        for name, lev, mode, reentry in CONFIGS:
            t, _ = bt.run_backtest_live(df, params(lev, mode, reentry, AMOUNT.get(c, 50)), bar='1h')
            res[name][c] = t
    print(f"  (계산 {time.time() - t0:.0f}초)")

    # ---------- 코인별 ----------
    names = [n for n, *_ in CONFIGS]
    short = ['예전3배', '5배바로', '지금', '3배다음봉']
    out('━' * 92)
    out('코인별 순손익 (USDT, 수수료·펀딩 포함)   ※ 강제청산 횟수는 괄호')
    out(f"{'코인':6s} {'기간':>6s} " + ' '.join(f"{s:>14s}" for s in short))
    for c, df in data.items():
        cells = []
        for n in names:
            t = res[n][c]
            net = t['순손익'].sum() if len(t) else 0.0
            lq = int((t['유형'] == '강제청산').sum()) if len(t) else 0
            cells.append(f"{net:+9,.0f} ({lq:>2d})")
        out(f"{c:6s} {(df.index[-1] - df.index[0]).days:>5d}일 " + ' '.join(f"{x:>14s}" for x in cells))

    # ---------- 전체 합 ----------
    out('━' * 92)
    out('전체 (코인 전부 합친 계좌 기준)')
    out(f"{'설정':26s} {'순손익':>9s} {'최대낙폭':>9s} {'최악의달':>9s} {'손실달':>6s} {'거래':>7s} {'승률':>5s} "
        f"{'수수료':>7s} {'펀딩':>6s} {'청산':>5s}")
    summary = {}
    for n in names:
        all_t = pd.concat([t.assign(코인=c) for c, t in res[n].items() if len(t)], ignore_index=True)
        if all_t.empty:
            out(f"{n:26s}  거래 없음")
            continue
        all_t = all_t.sort_values('시각')
        eq = np.r_[0.0, all_t['순손익'].cumsum().values]
        mdd = (eq - np.maximum.accumulate(eq)).min()
        monthly = all_t.groupby(all_t['시각'].dt.to_period('M'))['순손익'].sum()
        summary[n] = dict(net=all_t['순손익'].sum(), mdd=mdd, monthly=monthly)
        out(f"{n:26s} {all_t['순손익'].sum():+9,.0f} {mdd:+9,.0f} {monthly.min():+9,.0f} "
            f"{(monthly < 0).sum():>3d}/{len(monthly):<2d} {len(all_t):7,d} {(all_t['순손익'] > 0).mean() * 100:4.0f}% "
            f"{all_t['수수료'].sum():7,.0f} {all_t['펀딩비'].sum():6,.0f} {(all_t['유형'] == '강제청산').sum():5d}")

    # ---------- 핵심 비교: 같은 5배·UT마감에서 재진입만 다르게 ----------
    a_n, b_n = '5배·UT마감·바로재진입', '지금: 5배·UT마감·다음봉'
    if a_n in summary and b_n in summary:
        out('━' * 92)
        out('핵심 비교 — 다른 건 다 같고 익절 후 재진입만 다름 (5배 · UT 봉마감 + EMA 실시간)')
        win = sum(1 for c in data if res[b_n][c]['순손익'].sum() > res[a_n][c]['순손익'].sum())
        out(f"  다음 봉 재진입이 더 번 코인: {win}/{len(data)}개")
        out(f"  전체 순손익: 바로 {summary[a_n]['net']:+,.0f}  →  다음 봉 {summary[b_n]['net']:+,.0f}")
        out(f"  최대 낙폭:   바로 {summary[a_n]['mdd']:+,.0f}  →  다음 봉 {summary[b_n]['mdd']:+,.0f}")
        ma, mb = summary[a_n]['monthly'], summary[b_n]['monthly']
        both = ma.index.intersection(mb.index)
        out(f"  달별로 다음 봉이 더 번 달: {(mb[both] > ma[both]).sum()}/{len(both)}개월")
        out('')
        out('  월별 순손익 (바로 → 다음 봉)')
        for m in both:
            out(f"    {m}  {ma[m]:+8,.0f} → {mb[m]:+8,.0f}   {'▲' if mb[m] > ma[m] else '▼'}")

    out('')
    out('※ 과거 결과이며 앞으로도 같다는 보장은 없습니다. 펀딩비는 0.01% 고정 가정입니다.')
    try:
        with open(OUT_TXT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(_lines) + '\n')
        print(f"\n💾 결과 저장: {OUT_TXT}")
    except Exception as e:
        print(f"⚠️ 결과 파일 저장 실패: {e}")
    return 0


if __name__ == '__main__':
    try:
        code = main()
    except KeyboardInterrupt:
        code = 1
    if os.name == 'nt' and sys.stdin and sys.stdin.isatty():
        input('\n엔터를 누르면 닫힙니다...')
    sys.exit(code)
