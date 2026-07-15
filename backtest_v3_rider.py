#!/usr/bin/env python3
"""
전략 v3 "RIDER" — 승률보다 '길게·크게' 버는 추세 라이딩 백테스터

철학: 이길 때 추세 끝까지 들고 가 크게 먹고, 질 때 작게 자른다.
      → 승률은 30~45%로 낮아도, 큰 승리 몇 번이 다수의 작은 손실을 덮는다.
      (기존 봇: 승률 90% / 익절 1.2% / 손실 무제한 → 정반대 구조)

실행:
  python backtest_v3_rider.py --synth        ← 실데이터 없이 5개 인공 장세 비교 (네트워크 불필요)
  python backtest_v3_rider.py --cli --symbols BTCUSDT,ETHUSDT --days 365 --interval 4h
  python backtest_v3_rider.py --cli --pyramid  ← 피라미딩 켜고 실데이터 백테스트

조합 (여러 지표 합의로 진입을 까다롭게 → 거래 적고 각 거래는 강한 추세):
- 방향/트레일링: Supertrend(기간 10, ATR 3배) — 라인 자체가 트레일링 스탑
- 상위 추세 필터: EMA200이 우상향(기울기 +)이고 종가가 그 위 → 롱만 (하락은 반대)
- 모멘텀 확인: ADX 상승 중(추세 강화) + MACD 히스토그램이 방향과 일치
- 진입: 위 조건이 모두 참이고 Supertrend가 해당 방향일 때 (플랫 상태에서만)
- 손절/청산: Supertrend 라인 이탈 또는 방향 전환 → 청산. 고정 익절 없음(추세 끝까지)
- 피라미딩(옵션): 수익이 +1 ATR씩 날 때마다 추가 진입(최대 N유닛), 전체 스탑은
  Supertrend 라인으로 통일 → "이기는 포지션에 얹어 크게"

리스크 관리 (bot_v2와 동일 철학):
- 포지션 크기 = 자본 × 리스크% ÷ 손절거리 → 1회 손실을 자본의 리스크%로 고정
- Supertrend 스탑이 넓어(≈3×ATR) 자연히 유닛이 작아짐 = 흔들림에 잘 안 털림
- 레버리지 캡, 복리, 슬리피지/수수료 반영
- 결과에 전반부/후반부 분리 + 최대연속손실 표시 (심리적 감내 구간 확인용)

⚠️ 합성 데이터의 절대 수익률은 무의미. 실데이터로 재확인 필수.
"""

import argparse
import os
import sys

# backtest_v2의 검증된 함수 재사용 (데이터/지표)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import backtest_v2 as bt2
except Exception as e:
    print(f"backtest_v2.py 를 찾을 수 없습니다: {e}")
    sys.exit(1)

import numpy as np
import pandas as pd

DEFAULTS = {
    'equity': 1000.0,
    'risk_pct': 0.7,       # 첫 유닛 리스크 (자본 %)
    'add_risk_pct': 0.4,   # 피라미딩 추가 유닛 리스크 (첫 유닛보다 작게)
    'max_lev': 5,
    'fee_pct': 0.04,
    'slip_pct': 0.02,
    'days': 365,
    'interval': '4h',      # 길게 타려면 상위 시간봉 권장
    # Supertrend
    'st_period': 10,
    'st_mult': 3.0,
    # 상위 추세 필터
    'ema_htf': 200,
    'slope_lookback': 20,  # EMA200 기울기 판정 구간 (봉)
    # 모멘텀
    'adx_period': 14,
    'adx_min': 18,         # 이 이상 + 상승 중일 때만
    'macd_fast': 12,
    'macd_slow': 26,
    'macd_signal': 9,
    # 피라미딩
    'pyramid': False,
    'max_units': 3,        # 최대 유닛 수 (첫 진입 포함)
    'add_step_atr': 1.0,   # 몇 ATR 오를 때마다 추가할지
}

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']


# ==================== 지표 ====================
def supertrend(df, period, mult):
    """표준 Supertrend. 반환: (방향 배열 +1/-1, 라인 배열). 인과적(미래 미참조)."""
    atr = bt2.atr_rma(df, period).values
    hl2 = ((df['high'] + df['low']) / 2).values
    close = df['close'].values
    n = len(df)
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    fu = upper.copy()
    fl = lower.copy()
    direction = np.ones(n, dtype=int)
    line = np.zeros(n)
    for i in range(1, n):
        fu[i] = min(upper[i], fu[i - 1]) if close[i - 1] <= fu[i - 1] else upper[i]
        fl[i] = max(lower[i], fl[i - 1]) if close[i - 1] >= fl[i - 1] else lower[i]
        if close[i] > fu[i - 1]:
            direction[i] = 1
        elif close[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    line[0] = fl[0]
    return direction, line


def macd_hist(df, fast, slow, signal):
    ema_f = df['close'].ewm(span=fast, adjust=False).mean()
    ema_s = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=signal, adjust=False).mean()
    return (macd - sig).values


# ==================== 시뮬레이션 ====================
def run_rider(df, p):
    """RIDER 전략 백테스트. 반환: (trades_df, max_dd_pct, final_eq, start_eq, extra)"""
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    n = len(df)

    fee = p['fee_pct'] / 100.0
    slip = p['slip_pct'] / 100.0
    equity = float(p['equity'])
    start_equity = equity

    atr = bt2.atr_rma(df, p['st_period']).values
    st_dir, st_line = supertrend(df, p['st_period'], p['st_mult'])
    adx = bt2.adx_series(df, p['adx_period']).values
    hist = macd_hist(df, p['macd_fast'], p['macd_slow'], p['macd_signal'])
    ema_htf = df['close'].ewm(span=int(p['ema_htf']), adjust=False).mean().values
    sl = int(p['slope_lookback'])

    warmup = max(int(p['ema_htf']) + sl + 5, p['st_period'] + 5)

    trades = []
    pos = None  # {'side','units':[{entry,qty}], 'stop', 'last_add', 'entry_i', 'bars'}
    eq_peak = equity
    max_dd_pct = 0.0
    consec_loss = 0
    max_consec_loss = 0

    def entry_ok(i, side):
        if np.isnan(ema_htf[i]) or np.isnan(ema_htf[i - sl]):
            return False
        slope_up = ema_htf[i] > ema_htf[i - sl]
        adx_rising = adx[i] > adx[i - 1] and adx[i] >= p['adx_min']
        if side == 'LONG':
            return (st_dir[i] == 1 and c[i] > ema_htf[i] and slope_up
                    and adx_rising and hist[i] > 0)
        else:
            return (st_dir[i] == -1 and c[i] < ema_htf[i] and (not slope_up)
                    and adx_rising and hist[i] < 0)

    def unit_qty(i, entry, dist, risk_pct):
        if dist <= 0 or np.isnan(dist):
            return 0.0
        qty = equity * risk_pct / 100.0 / dist
        return qty

    def total_notional(pos, price):
        return sum(u['qty'] for u in pos['units']) * price

    def open_first(side, i):
        entry = o[i + 1] * (1 + slip) if side == 'LONG' else o[i + 1] * (1 - slip)
        stop = st_line[i]  # Supertrend 라인이 초기 스탑
        dist = abs(entry - stop)
        qty = unit_qty(i, entry, dist, p['risk_pct'])
        # 레버리지 캡
        qty = min(qty, equity * p['max_lev'] / entry)
        if qty * entry < 5:
            return None
        return {'side': side, 'units': [{'entry': entry, 'qty': qty}],
                'stop': stop, 'last_add': entry, 'entry_i': i + 1, 'bars': 0}

    def try_add(pos, i):
        if not p['pyramid'] or len(pos['units']) >= p['max_units']:
            return
        price = c[i]
        moved = (price - pos['last_add']) if pos['side'] == 'LONG' \
            else (pos['last_add'] - price)
        if moved < p['add_step_atr'] * atr[i]:
            return
        entry = o[i + 1] * (1 + slip) if pos['side'] == 'LONG' else o[i + 1] * (1 - slip)
        stop = st_line[i]
        dist = abs(entry - stop)
        qty = unit_qty(i, entry, dist, p['add_risk_pct'])
        # 전체 노셔널 레버리지 캡 확인
        cap = equity * p['max_lev'] / entry
        cur = sum(u['qty'] for u in pos['units'])
        qty = min(qty, max(0.0, cap - cur))
        if qty * entry < 5:
            return
        pos['units'].append({'entry': entry, 'qty': qty})
        pos['last_add'] = entry

    def close_all(pos, exit_price, exit_i, reason):
        nonlocal equity, eq_peak, max_dd_pct, consec_loss, max_consec_loss
        gross = 0.0
        fee_amt = 0.0
        total_qty = 0.0
        avg_entry_num = 0.0
        for u in pos['units']:
            if pos['side'] == 'LONG':
                gross += u['qty'] * (exit_price - u['entry'])
            else:
                gross += u['qty'] * (u['entry'] - exit_price)
            fee_amt += fee * u['qty'] * (u['entry'] + exit_price)
            total_qty += u['qty']
            avg_entry_num += u['entry'] * u['qty']
        net = gross - fee_amt
        equity += net
        eq_peak = max(eq_peak, equity)
        if eq_peak > 0:
            max_dd_pct = min(max_dd_pct, (equity - eq_peak) / eq_peak * 100)
        if net > 0:
            consec_loss = 0
        else:
            consec_loss += 1
            max_consec_loss = max(max_consec_loss, consec_loss)
        avg_entry = avg_entry_num / total_qty if total_qty else 0
        trades.append({
            '시각': df.index[exit_i], '포지션': pos['side'], '유닛수': len(pos['units']),
            '평균진입가': round(avg_entry, 6), '청산가': round(exit_price, 6),
            '순손익': round(net, 4), '수수료': round(fee_amt, 4), '유형': reason,
            '보유(봉)': exit_i - pos['entry_i'], '자본': round(equity, 2),
        })

    i = warmup
    while i < n - 1:
        if pos is None:
            side = 'LONG' if entry_ok(i, 'LONG') else ('SHORT' if entry_ok(i, 'SHORT') else None)
            if side:
                pos = open_first(side, i)
            i += 1
            continue

        j = max(i, pos['entry_i'])
        if j >= n:
            break
        pos['bars'] = j - pos['entry_i']
        exited = False

        if pos['side'] == 'LONG':
            # 1) 스탑(=이전 봉 Supertrend 라인) 이탈: 봉 저가로 판정
            if l[j] <= pos['stop']:
                close_all(pos, min(pos['stop'], o[j]) * (1 - slip), j, '스탑청산')
                pos = None
                exited = True
            # 2) Supertrend 방향 전환: 다음 봉 시가 청산
            elif st_dir[j] == -1:
                close_all(pos, o[j + 1] if j + 1 < n else c[j], j, '추세전환')
                pos = None
                exited = True
            if not exited and pos:
                # 트레일링: 스탑을 Supertrend 라인으로 상향(하향 금지)
                pos['stop'] = max(pos['stop'], st_line[j])
                try_add(pos, j)
        else:
            if h[j] >= pos['stop']:
                close_all(pos, max(pos['stop'], o[j]) * (1 + slip), j, '스탑청산')
                pos = None
                exited = True
            elif st_dir[j] == 1:
                close_all(pos, o[j + 1] if j + 1 < n else c[j], j, '추세전환')
                pos = None
                exited = True
            if not exited and pos:
                pos['stop'] = min(pos['stop'], st_line[j])
                try_add(pos, j)

        i = j + 1

    if pos is not None and pos['entry_i'] < n:
        close_all(pos, c[n - 1], n - 1, '기간종료')

    extra = {'max_consec_loss': max_consec_loss}
    return pd.DataFrame(trades), max_dd_pct, equity, start_equity, extra


def summarize(trades, symbol, max_dd, final_eq, start_eq, extra, df):
    if trades.empty:
        return None
    total = len(trades)
    wins = trades[trades['순손익'] > 0]
    losses = trades[trades['순손익'] <= 0]
    gw = wins['순손익'].sum()
    gl = abs(losses['순손익'].sum())
    pf = round(gw / gl, 2) if gl > 0 else float('inf')
    avg_win = wins['순손익'].mean() if len(wins) else 0.0
    avg_loss = abs(losses['순손익'].mean()) if len(losses) else 0.0
    payoff = round(avg_win / avg_loss, 2) if avg_loss > 0 else float('inf')
    half = df.index[len(df) // 2]
    first = trades[trades['시각'] < half]['순손익'].sum()
    second = trades[trades['시각'] >= half]['순손익'].sum()
    return {
        '심볼': symbol, '거래수': total,
        '승률%': round(len(wins) / total * 100, 1),
        '평균익': round(avg_win, 2), '평균손': round(-avg_loss, 2),
        '손익비': payoff,  # 평균익/평균손 — RIDER의 핵심 지표
        'PF': pf,
        '최대익': round(trades['순손익'].max(), 2),
        '최장보유': int(trades['보유(봉)'].max()),
        '최대연속손실': extra['max_consec_loss'],
        '순손익': round(final_eq - start_eq, 2),
        '수익률%': round((final_eq / start_eq - 1) * 100, 1),
        '전반부': round(first, 2), '후반부': round(second, 2),
        '최대낙폭%': round(max_dd, 1),
    }


# ==================== 합성 비교 ====================
def synth(n, seed, kind):
    rng = np.random.default_rng(seed)
    vol = 0.009
    if kind == 'bull':
        rets = np.full(n, 0.0005) + rng.normal(0, vol, n)
        close = 100 * np.exp(np.cumsum(rets))
    elif kind == 'bear':
        rets = np.full(n, -0.0005) + rng.normal(0, vol, n)
        close = 100 * np.exp(np.cumsum(rets))
    elif kind == 'sideways':
        log_p = np.zeros(n)
        for i in range(1, n):
            log_p[i] = log_p[i - 1] * 0.99 + rng.normal(0, vol)
        close = 100 * np.exp(log_p)
    elif kind == 'choppy':
        log_p = np.zeros(n)
        for i in range(1, n):
            log_p[i] = log_p[i - 1] * 0.97 + rng.normal(0, vol * 1.4)
        close = 100 * np.exp(log_p)
    else:  # cycle
        q = n // 4
        seg = [np.full(q, 0.0007), np.zeros(q),
               np.full(q, -0.0006), np.full(n - 3 * q, 0.0004)]
        rets = np.concatenate(seg) + rng.normal(0, vol, n)
        close = 100 * np.exp(np.cumsum(rets))
    op = np.roll(close, 1); op[0] = close[0]
    wick = np.abs(rng.normal(0, vol / 2, n)) * close
    high = np.maximum(op, close) + wick
    low = np.minimum(op, close) - wick
    idx = pd.date_range('2024-01-01', periods=n, freq='h')
    return pd.DataFrame({'open': op, 'high': high, 'low': low,
                         'close': close, 'volume': np.ones(n)}, index=idx)


def run_synth(pyramid=False):
    N = 8760
    KIND_OFFSET = {'bull': 11, 'bear': 23, 'sideways': 37, 'choppy': 53, 'cycle': 71}
    scenarios = [('상승추세', 'bull'), ('하락추세', 'bear'), ('횡보', 'sideways'),
                 ('난조(노이즈)', 'choppy'), ('사이클', 'cycle')]
    p = dict(DEFAULTS)
    p['pyramid'] = pyramid
    p['interval'] = '1h'  # 합성은 1h로 통일

    title = "RIDER (피라미딩 ON)" if pyramid else "RIDER (단일 유닛)"
    print("=" * 100)
    print(f"합성 백테스트 — {title} | 5개 장세 × 3시드 평균 | 자본 1000 | 리스크 0.7%")
    print("=" * 100)
    print(f"{'장세':<14}{'거래':>5}{'승률%':>7}{'손익비':>7}{'PF':>6}{'최대익':>8}"
          f"{'최장보유':>8}{'연속손실':>8}{'순손익':>9}{'낙폭%':>7}  판정")
    print("-" * 100)
    grand = {'net': 0.0, 'first': 0.0, 'second': 0.0, 'dd': [], 'wr': []}
    for label, kind in scenarios:
        accs = {k: [] for k in ('tr', 'wr', 'po', 'pf', 'mx', 'hold',
                                'cl', 'net', 'dd', 'first', 'second')}
        for seed in (1, 2, 3):
            df = synth(N, seed * 7 + KIND_OFFSET[kind], kind)
            trades, dd, final, start, extra = run_rider(df, p)
            row = summarize(trades, 'SYNTH', dd, final, start, extra, df)
            if not row:
                continue
            accs['tr'].append(row['거래수']); accs['wr'].append(row['승률%'])
            accs['po'].append(row['손익비'] if row['손익비'] != float('inf') else 9.9)
            accs['pf'].append(row['PF'] if row['PF'] != float('inf') else 9.9)
            accs['mx'].append(row['최대익']); accs['hold'].append(row['최장보유'])
            accs['cl'].append(row['최대연속손실']); accs['net'].append(row['순손익'])
            accs['dd'].append(row['최대낙폭%'])
            accs['first'].append(row['전반부']); accs['second'].append(row['후반부'])
        if not accs['net']:
            print(f"{label:<14}  거래 없음(진입 조건 까다로움)")
            continue
        net = np.mean(accs['net'])
        grand['net'] += net
        grand['first'] += np.mean(accs['first'])
        grand['second'] += np.mean(accs['second'])
        grand['dd'].append(np.mean(accs['dd']))
        grand['wr'].append(np.mean(accs['wr']))
        mark = '🟢' if net > 0 else '🔴'
        print(f"{label:<14}{np.mean(accs['tr']):>5.0f}{np.mean(accs['wr']):>7.1f}"
              f"{np.mean(accs['po']):>7.2f}{np.mean(accs['pf']):>6.2f}"
              f"{np.mean(accs['mx']):>+8.1f}{np.mean(accs['hold']):>8.0f}"
              f"{np.mean(accs['cl']):>8.1f}{net:>+9.1f}{np.mean(accs['dd']):>7.1f} {mark}")
    print("-" * 100)
    both = grand['first'] > 0 and grand['second'] > 0
    verdict = ('✅ 양 기간 수익' if both else '⚠️ 한 기간만 수익'
               if (grand['first'] > 0 or grand['second'] > 0) else '❌ 양 기간 손실')
    print(f"{'합계':<14}{'':>5}{np.mean(grand['wr']):>7.1f}{'':>13}{'':>16}"
          f"{grand['net']:>+9.1f}{np.mean(grand['dd']):>7.1f}  {verdict}")
    print(f"   전반부 {grand['first']:+.1f} / 후반부 {grand['second']:+.1f}")
    print("=" * 100)
    print("해석: RIDER는 승률이 낮게(30~45%) 나오는 게 정상. '손익비'(평균익/평균손)와")
    print("      '최대익'이 크고, 추세장에서 크게 벌면 설계대로 동작하는 것.")
    print("      횡보/난조장 손실은 상위 추세 필터가 걸러 추세추종 단독보다 작아야 정상.")


# ==================== 실데이터 CLI ====================
def run_cli(args):
    p = dict(DEFAULTS)
    p['days'] = args.days
    p['interval'] = args.interval
    p['pyramid'] = args.pyramid
    symbols = [s.strip().upper() for s in args.symbols.split(',')]

    rows = []
    for k, sym in enumerate(symbols, 1):
        print(f"\n[{k}/{len(symbols)}] {sym} 데이터 준비...")
        try:
            df = bt2.fetch_klines(sym, p['days'], p['interval'])
        except Exception as e:
            print(f"  ❌ {sym} 건너뜀: {e}")
            continue
        if len(df) < 300:
            print(f"  ⚠️ {sym} 데이터 부족 — 건너뜀")
            continue
        trades, dd, final, start, extra = run_rider(df, p)
        row = summarize(trades, sym, dd, final, start, extra, df)
        if row:
            rows.append(row)
            print(f"  ▶ 거래 {row['거래수']} | 승률 {row['승률%']}% | "
                  f"손익비 {row['손익비']} | 최대익 {row['최대익']:+} | "
                  f"순손익 {row['순손익']:+} | 낙폭 {row['최대낙폭%']}%")
            trades.to_csv(f"backtest_v3_trades_{sym}.csv", index=False,
                          encoding='utf-8-sig')
    if rows:
        res = pd.DataFrame(rows)
        res.to_csv('backtest_v3_result.csv', index=False, encoding='utf-8-sig')
        print(f"\n📊 저장: backtest_v3_result.csv | 합산 순손익 "
              f"{res['순손익'].sum():+,.2f} USDT")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='RIDER 전략 백테스터 (v3)')
    ap.add_argument('--synth', action='store_true', help='합성 데이터 비교 (네트워크 불필요)')
    ap.add_argument('--cli', action='store_true', help='실데이터 백테스트')
    ap.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    ap.add_argument('--days', type=int, default=DEFAULTS['days'])
    ap.add_argument('--interval', default=DEFAULTS['interval'])
    ap.add_argument('--pyramid', action='store_true', help='피라미딩 켜기')
    args = ap.parse_args()

    if args.synth:
        run_synth(pyramid=args.pyramid)
    elif args.cli:
        run_cli(args)
    else:
        print("사용법:")
        print("  python backtest_v3_rider.py --synth              # 합성 비교(권장 시작점)")
        print("  python backtest_v3_rider.py --synth --pyramid    # 피라미딩 켜고 합성")
        print("  python backtest_v3_rider.py --cli --interval 4h  # 실데이터")
