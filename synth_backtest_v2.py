#!/usr/bin/env python3
"""
합성 데이터 백테스트 — combo(1+2 결합) vs 개별 전략 vs 기존 봇
실데이터 API가 차단된 환경이므로, 여러 장세를 인공 생성해 비교한다.

⚠️ 합성 데이터라 절대적 수익률은 의미 없음. '어느 전략이 어느 장세에서
   강한가'의 상대 비교와 리스크 관리 동작 확인이 목적.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import backtest_v2 as bt


def synth(n, seed, kind):
    """장세별 1시간봉 OHLC 생성"""
    rng = np.random.default_rng(seed)
    vol = 0.009
    if kind == 'bull':          # 꾸준한 상승 추세
        drift = np.full(n, 0.0005)
        rets = drift + rng.normal(0, vol, n)
        close = 100 * np.exp(np.cumsum(rets))
    elif kind == 'bear':        # 꾸준한 하락 추세
        drift = np.full(n, -0.0005)
        rets = drift + rng.normal(0, vol, n)
        close = 100 * np.exp(np.cumsum(rets))
    elif kind == 'sideways':    # 평균회귀 횡보
        log_p = np.zeros(n)
        for i in range(1, n):
            log_p[i] = log_p[i-1] * 0.99 + rng.normal(0, vol)
        close = 100 * np.exp(log_p)
    elif kind == 'choppy':      # 강한 노이즈 + 약한 추세 (가짜 신호 많음)
        log_p = np.zeros(n)
        for i in range(1, n):
            log_p[i] = log_p[i-1] * 0.97 + rng.normal(0, vol*1.4)
        close = 100 * np.exp(log_p)
    else:  # cycle: 상승→횡보→하락→반등 (실제 시장과 유사)
        q = n // 4
        seg = [np.full(q, 0.0007), np.zeros(q),
               np.full(q, -0.0006), np.full(n-3*q, 0.0004)]
        rets = np.concatenate(seg) + rng.normal(0, vol, n)
        close = 100 * np.exp(np.cumsum(rets))
    op = np.roll(close, 1); op[0] = close[0]
    wick = np.abs(rng.normal(0, vol/2, n)) * close
    high = np.maximum(op, close) + wick
    low = np.minimum(op, close) - wick
    idx = pd.date_range('2024-01-01', periods=n, freq='h')
    return pd.DataFrame({'open': op, 'high': high, 'low': low,
                         'close': close, 'volume': np.ones(n)}, index=idx)


N = 8760  # 1년치 1시간봉
scenarios = [
    ('상승추세', 'bull'), ('하락추세', 'bear'), ('횡보', 'sideways'),
    ('난조(노이즈)', 'choppy'), ('사이클(상→횡→하→반등)', 'cycle'),
]
# 장세별 고정 오프셋 (재현 가능한 결정론적 시드)
KIND_OFFSET = {'bull': 11, 'bear': 23, 'sideways': 37,
               'choppy': 53, 'cycle': 71}
strategies = ['trend', 'meanrev', 'combo', 'baseline']
p = dict(bt.DEFAULTS)

print("="*92)
print("합성 데이터 백테스트 — 각 장세 3개 시드 평균 | 시작자본 1000 USDT | 리스크 0.7%/회")
print("="*92)
print(f"{'장세':<20}{'전략':<14}{'거래':>5}{'승률%':>7}{'PF':>6}"
      f"{'순손익':>10}{'낙폭%':>8}{'전반부':>9}{'후반부':>9}")
print("-"*92)

grand = {s: {'net': 0.0, 'first': 0.0, 'second': 0.0, 'dd': []} for s in strategies}

for label, kind in scenarios:
    agg = {s: {'net': [], 'wr': [], 'pf': [], 'dd': [], 'tr': [],
               'first': [], 'second': []} for s in strategies}
    for seed in (1, 2, 3):
        df = synth(N, seed * 7 + KIND_OFFSET[kind], kind)
        for s in strategies:
            trades, dd, final, start = bt.run_strategy(df, p, s)
            row = bt.summarize(trades, 'SYNTH', s, dd, final, start, df)
            if row:
                agg[s]['net'].append(row['순손익'])
                agg[s]['wr'].append(row['승률%'])
                agg[s]['pf'].append(row['PF'] if row['PF'] != float('inf') else 5.0)
                agg[s]['dd'].append(row['최대낙폭%'])
                agg[s]['tr'].append(row['거래수'])
                agg[s]['first'].append(row['전반부'])
                agg[s]['second'].append(row['후반부'])
    for s in strategies:
        a = agg[s]
        if not a['net']:
            continue
        net = np.mean(a['net'])
        first = np.mean(a['first']); second = np.mean(a['second'])
        grand[s]['net'] += net
        grand[s]['first'] += first
        grand[s]['second'] += second
        grand[s]['dd'].append(np.mean(a['dd']))
        mark = '🟢' if net > 0 else '🔴'
        print(f"{label:<20}{bt.STRAT_LABEL[s]:<14}{np.mean(a['tr']):>5.0f}"
              f"{np.mean(a['wr']):>7.1f}{np.mean(a['pf']):>6.2f}"
              f"{net:>+10.1f}{np.mean(a['dd']):>8.1f}"
              f"{first:>+9.1f}{second:>+9.1f} {mark}")
    print("-"*92)

print("\n" + "="*92)
print("전체 장세 합산 (5개 장세 × 3시드 평균의 합)")
print("="*92)
print(f"{'전략':<16}{'합산순손익':>12}{'전반부합':>11}{'후반부합':>11}{'평균낙폭%':>11}  판정")
print("-"*92)
for s in strategies:
    g = grand[s]
    both_pos = g['first'] > 0 and g['second'] > 0
    verdict = ('✅ 양 기간 수익' if both_pos else
               '⚠️ 한 기간만 수익' if (g['first'] > 0 or g['second'] > 0) else
               '❌ 양 기간 손실')
    print(f"{bt.STRAT_LABEL[s]:<16}{g['net']:>+12.1f}{g['first']:>+11.1f}"
          f"{g['second']:>+11.1f}{np.mean(g['dd']):>11.1f}  {verdict}")
print("="*92)
print("해석: 합성 데이터라 절대 수익률은 무의미. 장세별 강약 패턴과 낙폭 크기만 참고.")
