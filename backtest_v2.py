#!/usr/bin/env python3
"""
바이낸스 선물 백테스터 v2 — 대안 전략 실험실 (완전 독립 실행형)

🔥 이 파일 하나만 있으면 실행됩니다 (봇 파일 불필요, API 키 불필요).
   필요 라이브러리(pandas/numpy/requests)는 실행 시 자동 설치됩니다.

실행:
  python backtest_v2.py                 ← GUI 실행 (더블클릭도 가능)
  python backtest_v2.py --cli           ← 콘솔(CLI) 모드
  python backtest_v2.py --cli --days 365 --interval 1h --strategies trend,meanrev,combo,baseline

기존 봇(UT Bot + EMA, 5분봉, 고정 TP, 손절 없음)과 반대 철학의 전략 3종:

[1] trend — 돈치안 채널 돌파 추세추종 (터틀 방식)
    - 진입: 종가가 돈치안(기본 20봉) 상단 돌파 + EMA200 위 → 롱 (숏은 반대)
    - 손절: 진입가 - 2.5 × ATR(14)
    - 청산: 샹들리에 트레일링 스탑 (최고가 - 2.5 × ATR) — 수익은 끝까지 끌고 감
    - 왜: 가장 오래 검증된 우위. 거래 적어 수수료 부담 낮고, 큰 추세 한 번이 손실 다수를 덮음

[2] meanrev — 볼린저밴드 + RSI 역추세 (횡보장 전용)
    - 진입: ADX < 20 (횡보 확인) + 종가가 밴드(20, 2σ) 하단 이탈 + RSI(14) < 30 → 롱
    - 손절: 진입가 - 2.0 × ATR
    - 청산: 중심선(SMA20) 복귀 시 익절, 또는 최대 보유봉 초과 시 종가 청산
    - 왜: 코인은 대부분의 시간을 횡보로 보냄. 추세전략이 피 흘리는 구간에서 수익

[3] combo — 국면 라우터: ADX ≥ 25 구간은 trend 신호만, ADX < 20 구간은 meanrev 신호만

[baseline] 기존 UT Bot + EMA 크로스 (비교용, backtest.py와 동일 로직)

공통 리스크 관리 (기존 봇과의 핵심 차이):
- 포지션 크기 = 자본 × 리스크% ÷ 손절거리  → 어떤 코인이든 1회 손실이 자본의 리스크%로 고정
- 레버리지는 상한(캡)으로만 작동. 복리 반영.
- 같은 봉에서 손절/익절 둘 다 걸리면 손절로 처리 (보수적)
- 진입/손절 체결에 슬리피지 반영

정직한 검증을 위해:
- 모든 파라미터는 교과서 기본값 (돈치안 20, ATR 2.5배, BB 20/2σ, RSI 30/70) — 과최적화 방지
- 결과에 전반부/후반부 순손익을 따로 표시 — 한쪽에서만 수익이면 의심할 것
"""

import argparse
import os
import queue
import subprocess
import sys
import threading
import time

# ==================== 라이브러리 자동 설치 ====================
for _mod in ['pandas', 'numpy', 'requests']:
    try:
        __import__(_mod)
    except ImportError:
        print(f"📦 {_mod} 설치 중...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', _mod],
                              stdout=subprocess.DEVNULL)

import numpy as np
import pandas as pd
import requests

# ==================== 기본값 ====================
DEFAULTS = {
    # 자본/리스크
    'equity': 1000.0,     # 시작 자본 (USDT)
    'risk_pct': 0.7,      # 1회 거래 리스크 (자본의 %) — 손절 시 잃는 금액
    'max_lev': 5,         # 레버리지 상한 (배)
    'fee_pct': 0.04,      # 수수료 % (테이커, 진입/청산 각각)
    'slip_pct': 0.02,     # 슬리피지 % (진입/손절 체결 시 불리하게 적용)
    # 기간
    'days': 365,          # 백테스트 기간 (일)
    'interval': '1h',     # 시간봉 (추세/역추세 전략 기본 1h)
    # [1] trend — 돈치안 추세추종
    'dc_entry': 20,       # 돈치안 진입 채널 (봉)
    'atr_period': 14,     # ATR 기간
    'atr_mult': 2.5,      # 손절/트레일링 ATR 배수
    'ema_filter': 200,    # 추세 필터 EMA (0 = 필터 없음)
    # [2] meanrev — 볼린저 역추세
    'bb_len': 20,         # 볼린저 기간
    'bb_std': 2.0,        # 볼린저 표준편차
    'rsi_len': 14,        # RSI 기간
    'rsi_os': 30,         # RSI 과매도 (롱 조건)
    'rsi_ob': 70,         # RSI 과매수 (숏 조건)
    'mr_adx_max': 20,     # 역추세 허용 최대 ADX (횡보 확인)
    'mr_atr_mult': 2.0,   # 역추세 손절 ATR 배수
    'mr_max_bars': 48,    # 최대 보유봉 (초과 시 종가 청산)
    # [3] combo — 국면 라우터
    'combo_adx_trend': 25,  # 이 이상이면 trend 신호만 허용
    # ADX 공통
    'adx_period': 14,
    # [baseline] 기존 UT+EMA (backtest.py와 동일 파라미터)
    'ut_sens': 10.0,
    'ut_atr': 2,
    'ema_fast': 34,
    'ema_slow': 55,
    'base_tp_trend': 1.2,
    'base_tp_side': 1.0,
    'base_adx_period': 10,
    'base_adx_th': 21,
    'base_margin_frac': 5.0,  # 기존 방식 진입금 = 자본의 % (손절이 없어 리스크 사이징 불가)
    'base_lev': 3,
}

STRATEGIES = ['trend', 'meanrev', 'combo', 'baseline']
STRAT_LABEL = {
    'trend': '돈치안 추세추종',
    'meanrev': 'BB+RSI 역추세',
    'combo': '국면 라우터',
    'baseline': '기존 UT+EMA',
}

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
FALLBACK_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT',
    'DOGEUSDT', 'TRXUSDT', 'TONUSDT', 'LINKUSDT', 'AVAXUSDT', 'DOTUSDT',
    'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'APTUSDT', 'SUIUSDT', 'ARBUSDT',
    'OPUSDT', 'FILUSDT',
]
INTERVALS = ['5m', '15m', '30m', '1h', '2h', '4h', '1d']


# ==================== 데이터 (backtest.py와 동일) ====================
def fetch_symbols(log=print):
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=15)
        r.raise_for_status()
        syms = sorted(
            s['symbol'] for s in r.json()['symbols']
            if s.get('quoteAsset') == 'USDT'
            and s.get('contractType') == 'PERPETUAL'
            and s.get('status') == 'TRADING'
        )
        log(f"✅ 바이낸스 선물 심볼 {len(syms)}개 로드 완료")
        return syms
    except Exception as e:
        log(f"⚠️ 심볼 목록 로드 실패({e}) — 기본 20개 사용")
        return list(FALLBACK_SYMBOLS)


def fetch_klines(symbol, days, interval='1h', cache_dir='backtest_data', log=print):
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"{symbol}_{interval}_{days}d.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        log(f"  📂 캐시 사용: {symbol} ({len(df):,}봉)")
        return df

    url = "https://fapi.binance.com/fapi/v1/klines"
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    rows = []
    cur = start
    last_log = 0
    while cur < end:
        data = None
        for attempt in range(5):
            try:
                r = requests.get(url, params={
                    'symbol': symbol, 'interval': interval,
                    'startTime': cur, 'limit': 1500}, timeout=15)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                log(f"  ⚠️ 재시도 {attempt + 1}/5: {e}")
                time.sleep(2 * (attempt + 1))
        if data is None:
            raise RuntimeError(f"{symbol} 다운로드 실패 - 네트워크 확인 필요")
        if not data:
            break
        rows.extend(data)
        cur = data[-1][6] + 1
        if len(rows) - last_log >= 15000:
            log(f"  ⬇️  {symbol}: {len(rows):,}봉...")
            last_log = len(rows)
        time.sleep(0.15)
        if len(data) < 1500:
            break

    if not rows:
        raise RuntimeError(f"{symbol}: 데이터 없음 (신규 상장 코인일 수 있음)")

    df = pd.DataFrame(rows, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'qv', 'n', 'tb', 'tq', 'ig'])
    df = df[['ts', 'open', 'high', 'low', 'close', 'volume']].astype(float)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    df = df.set_index('ts')
    df = df[~df.index.duplicated(keep='first')].sort_index()
    df.to_csv(cache)
    log(f"  💾 {symbol}: {len(df):,}봉 다운로드 완료 (캐시 저장)")
    return df


# ==================== 지표 ====================
def atr_rma(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx_series(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    up = high - high.shift(1)
    down = low.shift(1) - low
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df.index)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(20.0)


def rsi_series(df, period):
    delta = df['close'].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def ut_bot(df, sensitivity, atr_period):
    """backtest.py와 동일 (baseline 비교용)"""
    atr = atr_rma(df, atr_period)
    close = df['close'].values
    n_loss = (sensitivity * atr).values
    stop = np.zeros(len(close))
    pos = np.zeros(len(close), dtype=int)
    stop[0] = close[0] - n_loss[0] if not np.isnan(n_loss[0]) else close[0]
    pos[0] = 1
    for i in range(1, len(close)):
        if np.isnan(n_loss[i]):
            stop[i] = stop[i - 1]
            pos[i] = pos[i - 1]
            continue
        prev_stop, cur, prev = stop[i - 1], close[i], close[i - 1]
        if cur > prev_stop and prev > prev_stop:
            stop[i] = max(prev_stop, cur - n_loss[i])
        elif cur < prev_stop and prev < prev_stop:
            stop[i] = min(prev_stop, cur + n_loss[i])
        elif cur > prev_stop:
            stop[i] = cur - n_loss[i]
        else:
            stop[i] = cur + n_loss[i]
        if prev < stop[i - 1] and cur > stop[i]:
            pos[i] = 1
        elif prev > stop[i - 1] and cur < stop[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]
    return pos


# ==================== 시뮬레이션 엔진 ====================
def run_strategy(df, p, strat):
    """단일 심볼 × 단일 전략 백테스트.

    체결 규칙 (선견편향 방지):
    - 신호는 완성봉 i 기준, 진입은 i+1 봉 시가 (+슬리피지)
    - 손절은 봉 내 저가/고가로 판정, 손절가 (+슬리피지 불리)로 체결
    - 트레일링 스탑은 '이전 봉까지'의 데이터로 계산된 값으로 현재 봉 판정
      (현재 봉 고가로 스탑을 올려놓고 같은 봉에서 판정하는 오류 방지)
    - 같은 봉에서 손절과 익절이 둘 다 걸리면 손절로 처리 (보수적)
    """
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    n = len(df)

    fee = p['fee_pct'] / 100.0
    slip = p['slip_pct'] / 100.0
    equity = p['equity']
    start_equity = equity

    atr = atr_rma(df, p['atr_period']).values
    adx = adx_series(df, p['adx_period']).values

    # ----- 전략별 신호 사전 계산 -----
    long_sig = np.zeros(n, dtype=bool)
    short_sig = np.zeros(n, dtype=bool)

    if strat in ('trend', 'combo'):
        dc_hi = df['high'].rolling(p['dc_entry']).max().shift(1).values
        dc_lo = df['low'].rolling(p['dc_entry']).min().shift(1).values
        if p['ema_filter'] > 0:
            ema_f = df['close'].ewm(span=int(p['ema_filter']), adjust=False).mean().values
            up_ok = c > ema_f
            dn_ok = c < ema_f
        else:
            up_ok = np.ones(n, dtype=bool)
            dn_ok = np.ones(n, dtype=bool)
        t_long = (c > dc_hi) & up_ok
        t_short = (c < dc_lo) & dn_ok

    if strat in ('meanrev', 'combo'):
        mid = df['close'].rolling(p['bb_len']).mean()
        sd = df['close'].rolling(p['bb_len']).std()
        upper = (mid + p['bb_std'] * sd).values
        lower = (mid - p['bb_std'] * sd).values
        mid_v = mid.values
        rsi = rsi_series(df, p['rsi_len']).values
        m_long = (c < lower) & (rsi < p['rsi_os']) & (adx < p['mr_adx_max'])
        m_short = (c > upper) & (rsi > p['rsi_ob']) & (adx < p['mr_adx_max'])

    if strat == 'trend':
        long_sig, short_sig = t_long, t_short
        sig_kind = np.full(n, 'T')
    elif strat == 'meanrev':
        long_sig, short_sig = m_long, m_short
        sig_kind = np.full(n, 'M')
    elif strat == 'combo':
        trend_zone = adx >= p['combo_adx_trend']
        long_sig = np.where(trend_zone, t_long, m_long)
        short_sig = np.where(trend_zone, t_short, m_short)
        sig_kind = np.where(trend_zone, 'T', 'M')
    elif strat == 'baseline':
        ut = ut_bot(df, p['ut_sens'], p['ut_atr'])
        ef = df['close'].ewm(span=int(p['ema_fast']), adjust=False).mean()
        es = df['close'].ewm(span=int(p['ema_slow']), adjust=False).mean()
        long_sig = (ut == 1) & (ef > es).values
        short_sig = (ut == -1) & (ef < es).values
        base_adx = adx_series(df, p['base_adx_period']).values
        sig_kind = np.full(n, 'B')

    warmup = max(60, int(p.get('ema_filter') or 0) + 5,
                 p['dc_entry'] + 5 if strat in ('trend', 'combo') else 0,
                 int(p['ema_slow']) + 5 if strat == 'baseline' else 0)

    trades = []
    pos = None
    eq_peak = equity
    max_dd_pct = 0.0

    def enter(side, i, kind):
        nonlocal equity
        entry = o[i + 1] * (1 + slip) if side == 'LONG' else o[i + 1] * (1 - slip)
        if strat == 'baseline':
            # 손절이 없어 리스크 사이징 불가 → 자본의 일정 % × 레버리지 (기존 봇 방식)
            notional = equity * p['base_margin_frac'] / 100.0 * p['base_lev']
            qty = notional / entry
            lev = p['base_lev']
            tp_pct = p['base_tp_trend'] if base_adx[i] >= p['base_adx_th'] else p['base_tp_side']
            tp_price = entry * (1 + tp_pct / 100) if side == 'LONG' else entry * (1 - tp_pct / 100)
            stop_price = entry * (1 - 1.0 / lev) if side == 'LONG' else entry * (1 + 1.0 / lev)  # 강제청산가
            return {'side': side, 'entry': entry, 'qty': qty, 'stop': stop_price,
                    'tp': tp_price, 'entry_i': i + 1, 'kind': kind, 'trail': False,
                    'extreme': entry}
        am = p['mr_atr_mult'] if kind == 'M' else p['atr_mult']
        dist = am * atr[i]
        if dist <= 0 or np.isnan(dist):
            return None
        stop_price = entry - dist if side == 'LONG' else entry + dist
        qty = equity * p['risk_pct'] / 100.0 / dist
        qty = min(qty, equity * p['max_lev'] / entry)  # 레버리지 캡
        if qty * entry < 5:  # 최소 주문금액 미달
            return None
        tp_price = mid_v[i] if kind == 'M' else None  # 역추세만 중심선 익절
        return {'side': side, 'entry': entry, 'qty': qty, 'stop': stop_price,
                'tp': tp_price, 'entry_i': i + 1, 'kind': kind,
                'trail': kind == 'T', 'atr_mult': am,
                'extreme': entry}

    def close(pp, exit_price, exit_i, reason):
        nonlocal equity, eq_peak, max_dd_pct
        if pp['side'] == 'LONG':
            gross = pp['qty'] * (exit_price - pp['entry'])
        else:
            gross = pp['qty'] * (pp['entry'] - exit_price)
        fee_amt = fee * pp['qty'] * (pp['entry'] + exit_price)
        net = gross - fee_amt
        equity += net
        eq_peak = max(eq_peak, equity)
        if eq_peak > 0:
            max_dd_pct = min(max_dd_pct, (equity - eq_peak) / eq_peak * 100)
        trades.append({
            '시각': df.index[exit_i], '전략신호': pp['kind'], '포지션': pp['side'],
            '진입가': round(pp['entry'], 6), '청산가': round(exit_price, 6),
            '수량': round(pp['qty'], 6), '순손익': round(net, 4),
            '수수료': round(fee_amt, 4), '유형': reason,
            '보유(봉)': exit_i - pp['entry_i'], '자본': round(equity, 2),
        })

    i = warmup
    while i < n - 1:
        if pos is None:
            side = 'LONG' if long_sig[i] else ('SHORT' if short_sig[i] else None)
            if side:
                pos = enter(side, i, sig_kind[i])
            i += 1
            continue

        j = max(i, pos['entry_i'])
        if j >= n:
            break
        exited = False

        if pos['side'] == 'LONG':
            # 1) 손절 (전 봉까지 계산된 스탑으로 판정 — 보수적 우선)
            if l[j] <= pos['stop']:
                close(pos, min(pos['stop'], o[j]) * (1 - slip), j,
                      '강제청산' if strat == 'baseline' else '손절')
                pos = None
                exited = True
            # 2) 익절 (고정 TP 또는 중심선)
            elif pos['tp'] is not None and h[j] >= pos['tp']:
                close(pos, max(pos['tp'], o[j]), j, '익절')
                pos = None
                exited = True
            # 3) 역신호 청산 (baseline 스위칭 / 추세 반대 돌파)
            elif short_sig[j] and (strat == 'baseline' or pos['kind'] == 'T'):
                close(pos, o[j + 1] if j + 1 < n else c[j], j, '스위칭')
                pos = enter('SHORT', j, sig_kind[j]) if j + 1 < n else None
                exited = True
            # 4) 역추세 시간 청산
            elif pos['kind'] == 'M' and j - pos['entry_i'] >= p['mr_max_bars']:
                close(pos, c[j], j, '시간청산')
                pos = None
                exited = True
            # 트레일링 갱신 (판정 후 현재 봉 데이터 반영)
            if not exited and pos and pos.get('trail'):
                pos['extreme'] = max(pos['extreme'], h[j])
                pos['stop'] = max(pos['stop'], pos['extreme'] - pos['atr_mult'] * atr[j])
        else:  # SHORT
            if h[j] >= pos['stop']:
                close(pos, max(pos['stop'], o[j]) * (1 + slip), j,
                      '강제청산' if strat == 'baseline' else '손절')
                pos = None
                exited = True
            elif pos['tp'] is not None and l[j] <= pos['tp']:
                close(pos, min(pos['tp'], o[j]), j, '익절')
                pos = None
                exited = True
            elif long_sig[j] and (strat == 'baseline' or pos['kind'] == 'T'):
                close(pos, o[j + 1] if j + 1 < n else c[j], j, '스위칭')
                pos = enter('LONG', j, sig_kind[j]) if j + 1 < n else None
                exited = True
            elif pos['kind'] == 'M' and j - pos['entry_i'] >= p['mr_max_bars']:
                close(pos, c[j], j, '시간청산')
                pos = None
                exited = True
            if not exited and pos and pos.get('trail'):
                pos['extreme'] = min(pos['extreme'], l[j])
                pos['stop'] = min(pos['stop'], pos['extreme'] + pos['atr_mult'] * atr[j])

        i = j + 1

    # 미청산 포지션은 마지막 종가로 정리
    if pos is not None and pos['entry_i'] < n:
        close(pos, c[n - 1], n - 1, '기간종료')

    return pd.DataFrame(trades), max_dd_pct, equity, start_equity


def summarize(trades, symbol, strat, max_dd_pct, final_eq, start_eq, df):
    if trades.empty:
        return None
    total = len(trades)
    wins = trades[trades['순손익'] > 0]
    losses = trades[trades['순손익'] <= 0]
    gross_win = wins['순손익'].sum()
    gross_loss = abs(losses['순손익'].sum())
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else float('inf')

    half_ts = df.index[len(df) // 2]
    first = trades[trades['시각'] < half_ts]['순손익'].sum()
    second = trades[trades['시각'] >= half_ts]['순손익'].sum()

    return {
        '심볼': symbol, '전략': STRAT_LABEL[strat], '거래수': total,
        '승률%': round(len(wins) / total * 100, 1),
        '평균익': round(wins['순손익'].mean(), 2) if len(wins) else 0.0,
        '평균손': round(losses['순손익'].mean(), 2) if len(losses) else 0.0,
        'PF': pf,
        '순손익': round(final_eq - start_eq, 2),
        '수익률%': round((final_eq / start_eq - 1) * 100, 1),
        '전반부': round(first, 2), '후반부': round(second, 2),
        '수수료': round(trades['수수료'].sum(), 2),
        '최대낙폭%': round(max_dd_pct, 1),
    }


def run_all(symbols, p, strategies, log=print, on_row=None):
    log("=" * 60)
    log(f"🔬 백테스트 v2: {len(symbols)}개 심볼 | {p['days']}일 | {p['interval']}봉")
    log(f"   전략: {', '.join(STRAT_LABEL[s] for s in strategies)}")
    log(f"   자본 {p['equity']:,.0f} USDT | 리스크 {p['risk_pct']}%/회 | "
        f"수수료 {p['fee_pct']}% | 슬리피지 {p['slip_pct']}%")
    log("=" * 60)

    summaries = []
    for k, sym in enumerate(symbols, 1):
        log(f"\n[{k}/{len(symbols)}] {sym} 데이터 준비...")
        try:
            df = fetch_klines(sym, p['days'], p['interval'], log=log)
        except Exception as e:
            log(f"  ❌ {sym} 건너뜀: {e}")
            continue
        if len(df) < 300:
            log(f"  ⚠️ {sym} 데이터 부족({len(df)}봉) — 건너뜀")
            continue
        for strat in strategies:
            trades, max_dd, final_eq, start_eq = run_strategy(df, p, strat)
            row = summarize(trades, sym, strat, max_dd, final_eq, start_eq, df)
            if row:
                summaries.append(row)
                if on_row:
                    on_row(row)
                log(f"  ▶ [{STRAT_LABEL[strat]:12s}] {row['거래수']:4d}회 | "
                    f"승률 {row['승률%']:5.1f}% | PF {row['PF']} | "
                    f"순손익 {row['순손익']:+10,.2f} | 낙폭 {row['최대낙폭%']}%")
                trades.to_csv(f"backtest_v2_trades_{sym}_{strat}.csv",
                              index=False, encoding='utf-8-sig')
            else:
                log(f"  ▶ [{STRAT_LABEL[strat]:12s}] 거래 없음")

    if summaries:
        result = pd.DataFrame(summaries)
        result.to_csv('backtest_v2_result.csv', index=False, encoding='utf-8-sig')
        log("\n" + "=" * 60)
        log("📊 완료! 요약: backtest_v2_result.csv 저장됨")
        for name, grp in result.groupby('전략', sort=False):
            log(f"   {name:14s}: 합산 순손익 {grp['순손익'].sum():+12,.2f} USDT | "
                f"전반부 {grp['전반부'].sum():+,.2f} / 후반부 {grp['후반부'].sum():+,.2f}")
        log("=" * 60)
        log("⚠️ 전반부/후반부 중 한쪽만 수익이면 과최적화 또는 특정 장세 의존을 의심하세요.")
    else:
        log("\n⚠️ 결과 없음")
    return summaries


# ==================== GUI ====================
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    BG, PANEL, FG, ACCENT = '#1e1e1e', '#2d2d2d', '#ffffff', '#00ff88'

    root = tk.Tk()
    root.title("📊 바이낸스 선물 백테스터 v2 — 대안 전략")
    root.geometry("1280x800")
    root.configure(bg=BG)

    log_q = queue.Queue()
    running = [False]
    all_symbols = list(FALLBACK_SYMBOLS)

    # ---------- 좌측: 설정 ----------
    left_wrap = tk.Frame(root, bg=PANEL)
    left_wrap.pack(side='left', fill='y', padx=8, pady=8)
    canvas = tk.Canvas(left_wrap, bg=PANEL, width=250, highlightthickness=0)
    lsb2 = tk.Scrollbar(left_wrap, orient='vertical', command=canvas.yview)
    left = tk.Frame(canvas, bg=PANEL)
    left.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0, 0), window=left, anchor='nw')
    canvas.configure(yscrollcommand=lsb2.set)
    canvas.pack(side='left', fill='both', expand=True)
    lsb2.pack(side='right', fill='y')

    tk.Label(left, text="⚙️ 공통 설정", bg=PANEL, fg=ACCENT,
             font=('Arial', 12, 'bold')).pack(pady=(8, 4))

    vars_ = {}

    def add_fields(parent, fields, fg_color=FG, font_size=10):
        form = tk.Frame(parent, bg=PANEL)
        form.pack(padx=10)
        for r, (label, key) in enumerate(fields):
            tk.Label(form, text=label, bg=PANEL, fg=fg_color,
                     font=('Arial', font_size), anchor='w'
                     ).grid(row=r, column=0, sticky='w', pady=1)
            v = tk.StringVar(value=str(DEFAULTS[key]))
            tk.Entry(form, textvariable=v, width=9, font=('Arial', font_size),
                     justify='center').grid(row=r, column=1, padx=6, pady=1)
            vars_[key] = v
        return form

    common = add_fields(left, [
        ('시작 자본 (USDT)', 'equity'), ('리스크 %/회', 'risk_pct'),
        ('레버리지 상한', 'max_lev'), ('수수료 % (편도)', 'fee_pct'),
        ('슬리피지 %', 'slip_pct'), ('기간 (일)', 'days'),
    ])
    tk.Label(common, text='시간봉', bg=PANEL, fg=FG, font=('Arial', 10),
             anchor='w').grid(row=6, column=0, sticky='w', pady=1)
    interval_var = tk.StringVar(value=DEFAULTS['interval'])
    ttk.Combobox(common, textvariable=interval_var, values=INTERVALS, width=7,
                 state='readonly').grid(row=6, column=1, padx=6, pady=1)

    # 전략 선택
    tk.Label(left, text="─" * 32, bg=PANEL, fg='#555555').pack()
    tk.Label(left, text="🎯 실행할 전략", bg=PANEL, fg=ACCENT,
             font=('Arial', 11, 'bold')).pack()
    strat_vars = {}
    for s in STRATEGIES:
        v = tk.BooleanVar(value=(s != 'baseline'))
        tk.Checkbutton(left, text=STRAT_LABEL[s], variable=v, bg=PANEL,
                       fg='#ffaa00' if s == 'baseline' else FG, selectcolor=BG,
                       font=('Arial', 10, 'bold'), activebackground=PANEL
                       ).pack(anchor='w', padx=14)
        strat_vars[s] = v

    tk.Label(left, text="─" * 32, bg=PANEL, fg='#555555').pack()
    tk.Label(left, text="📈 돈치안 추세추종", bg=PANEL, fg='#00ffff',
             font=('Arial', 10, 'bold')).pack(anchor='w', padx=10)
    add_fields(left, [
        ('돈치안 채널 (봉)', 'dc_entry'), ('ATR 기간', 'atr_period'),
        ('손절/트레일 ATR배수', 'atr_mult'), ('EMA 필터 (0=끔)', 'ema_filter'),
    ], fg_color='#aaaaaa', font_size=9)

    tk.Label(left, text="📉 BB+RSI 역추세", bg=PANEL, fg='#00ffff',
             font=('Arial', 10, 'bold')).pack(anchor='w', padx=10, pady=(6, 0))
    add_fields(left, [
        ('볼린저 기간', 'bb_len'), ('볼린저 σ', 'bb_std'),
        ('RSI 기간', 'rsi_len'), ('RSI 과매도', 'rsi_os'),
        ('RSI 과매수', 'rsi_ob'), ('허용 최대 ADX', 'mr_adx_max'),
        ('손절 ATR배수', 'mr_atr_mult'), ('최대 보유봉', 'mr_max_bars'),
    ], fg_color='#aaaaaa', font_size=9)

    tk.Label(left, text="🔀 국면 라우터 / ADX", bg=PANEL, fg='#00ffff',
             font=('Arial', 10, 'bold')).pack(anchor='w', padx=10, pady=(6, 0))
    add_fields(left, [
        ('추세 판정 ADX', 'combo_adx_trend'), ('ADX 기간', 'adx_period'),
    ], fg_color='#aaaaaa', font_size=9)

    tk.Label(left, text="🤖 기존 UT+EMA (비교용)", bg=PANEL, fg='#ffaa00',
             font=('Arial', 10, 'bold')).pack(anchor='w', padx=10, pady=(6, 0))
    add_fields(left, [
        ('UT Key Value', 'ut_sens'), ('UT ATR 기간', 'ut_atr'),
        ('EMA Fast', 'ema_fast'), ('EMA Slow', 'ema_slow'),
        ('진입금 (자본%)', 'base_margin_frac'), ('레버리지', 'base_lev'),
    ], fg_color='#aaaaaa', font_size=9)

    run_btn = tk.Button(left, text="▶️ 백테스트 시작", bg='#0066cc', fg='#ffffff',
                        font=('Arial', 13, 'bold'), padx=20, pady=8)
    run_btn.pack(pady=12)

    # ---------- 중앙: 코인 선택 ----------
    mid = tk.Frame(root, bg=PANEL)
    mid.pack(side='left', fill='y', padx=(0, 8), pady=8)

    tk.Label(mid, text="🪙 코인 선택", bg=PANEL, fg=ACCENT,
             font=('Arial', 12, 'bold')).pack(pady=(8, 4))

    search_var = tk.StringVar()
    tk.Entry(mid, textvariable=search_var, width=18, font=('Arial', 10)).pack(padx=10)
    tk.Label(mid, text="(검색: btc, doge ...)", bg=PANEL, fg='#888888',
             font=('Arial', 8)).pack()

    lb_frame = tk.Frame(mid, bg=PANEL)
    lb_frame.pack(fill='both', expand=True, padx=10, pady=4)
    sb = tk.Scrollbar(lb_frame)
    sb.pack(side='right', fill='y')
    listbox = tk.Listbox(lb_frame, selectmode='extended', width=18, height=25,
                         bg=BG, fg=FG, font=('Consolas', 10),
                         yscrollcommand=sb.set, exportselection=False)
    listbox.pack(side='left', fill='both', expand=True)
    sb.config(command=listbox.yview)

    count_label = tk.Label(mid, text="선택: 0개", bg=PANEL, fg='#ffaa00',
                           font=('Arial', 10, 'bold'))
    count_label.pack()

    btns = tk.Frame(mid, bg=PANEL)
    btns.pack(pady=6)

    def refresh_list(*_):
        kw = search_var.get().strip().upper()
        listbox.delete(0, 'end')
        for s in all_symbols:
            if kw in s:
                listbox.insert('end', s)
    search_var.trace('w', refresh_list)

    def update_count(*_):
        count_label.config(text=f"선택: {len(listbox.curselection())}개")
    listbox.bind('<<ListboxSelect>>', update_count)

    def select_all():
        listbox.select_set(0, 'end')
        update_count()

    def select_none():
        listbox.select_clear(0, 'end')
        update_count()

    def select_default():
        select_none()
        for i in range(listbox.size()):
            if listbox.get(i) in DEFAULT_SYMBOLS:
                listbox.select_set(i)
        update_count()

    tk.Button(btns, text="전체", command=select_all, bg='#3d3d3d', fg=FG,
              font=('Arial', 9), width=5).pack(side='left', padx=2)
    tk.Button(btns, text="해제", command=select_none, bg='#3d3d3d', fg=FG,
              font=('Arial', 9), width=5).pack(side='left', padx=2)
    tk.Button(btns, text="기본5종", command=select_default, bg='#3d3d3d', fg=FG,
              font=('Arial', 9), width=7).pack(side='left', padx=2)

    # ---------- 우측: 결과 + 로그 ----------
    right = tk.Frame(root, bg=BG)
    right.pack(side='left', fill='both', expand=True, pady=8, padx=(0, 8))

    tk.Label(right, text="📊 결과 (backtest_v2_result.csv 자동 저장)", bg=BG,
             fg=ACCENT, font=('Arial', 12, 'bold')).pack(anchor='w')

    cols = ['심볼', '전략', '거래수', '승률%', '평균익', '평균손', 'PF',
            '순손익', '수익률%', '전반부', '후반부', '수수료', '최대낙폭%']
    style = ttk.Style()
    try:
        style.theme_use('clam')
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=22)
        style.configure('Treeview.Heading', background='#3d3d3d', foreground=FG)
    except Exception:
        pass

    tree_frame = tk.Frame(right, bg=BG)
    tree_frame.pack(fill='both', expand=True)
    tsb = tk.Scrollbar(tree_frame)
    tsb.pack(side='right', fill='y')
    tree = ttk.Treeview(tree_frame, columns=cols, show='headings', height=14,
                        yscrollcommand=tsb.set)
    tsb.config(command=tree.yview)
    for col in cols:
        tree.heading(col, text=col)
        tree.column(col, width=90 if col in ('심볼', '전략') else 64,
                    anchor='center')
    tree.column('전략', width=120)
    tree.pack(side='left', fill='both', expand=True)
    tree.tag_configure('win', foreground='#00ff88')
    tree.tag_configure('lose', foreground='#ff6666')

    tk.Label(right, text="📋 진행 로그", bg=BG, fg=ACCENT,
             font=('Arial', 11, 'bold')).pack(anchor='w', pady=(8, 0))
    log_frame = tk.Frame(right, bg=BG)
    log_frame.pack(fill='both', expand=True)
    lsb = tk.Scrollbar(log_frame)
    lsb.pack(side='right', fill='y')
    log_text = tk.Text(log_frame, height=12, bg='#141414', fg='#cccccc',
                       font=('Consolas', 9), yscrollcommand=lsb.set, wrap='word')
    log_text.pack(side='left', fill='both', expand=True)
    lsb.config(command=log_text.yview)

    def gui_log(msg):
        log_q.put(('log', str(msg)))

    def poll_queue():
        try:
            while True:
                kind, payload = log_q.get_nowait()
                if kind == 'log':
                    log_text.insert('end', payload + '\n')
                    log_text.see('end')
                elif kind == 'row':
                    row = payload
                    tag = 'win' if row['순손익'] >= 0 else 'lose'
                    tree.insert('', 'end', values=[row.get(col, '') for col in cols],
                                tags=(tag,))
                elif kind == 'done':
                    running[0] = False
                    run_btn.config(state='normal', text="▶️ 백테스트 시작")
        except queue.Empty:
            pass
        root.after(100, poll_queue)

    # ---------- 실행 ----------
    def read_params():
        p = dict(DEFAULTS)
        int_keys = {'max_lev', 'days', 'dc_entry', 'atr_period', 'ema_filter',
                    'bb_len', 'rsi_len', 'rsi_os', 'rsi_ob', 'mr_adx_max',
                    'mr_max_bars', 'combo_adx_trend', 'adx_period', 'ut_atr',
                    'ema_fast', 'ema_slow', 'base_lev'}
        for key, v in vars_.items():
            raw = v.get().strip()
            p[key] = int(float(raw)) if key in int_keys else float(raw)
        p['interval'] = interval_var.get()
        return p

    def start():
        if running[0]:
            return
        try:
            p = read_params()
        except ValueError as e:
            messagebox.showwarning("입력 오류", f"숫자를 확인해주세요!\n\n{e}")
            return
        strategies = [s for s in STRATEGIES if strat_vars[s].get()]
        if not strategies:
            messagebox.showwarning("전략 선택", "전략을 1개 이상 선택해주세요!")
            return
        sel = [listbox.get(i) for i in listbox.curselection()]
        if not sel:
            messagebox.showwarning("코인 선택", "코인을 1개 이상 선택해주세요!")
            return
        if len(sel) > 20:
            if not messagebox.askyesno(
                    "확인", f"{len(sel)}개 코인 다운로드에 시간이 오래 걸릴 수 "
                    "있습니다.\n계속할까요?"):
                return
        for item in tree.get_children():
            tree.delete(item)
        running[0] = True
        run_btn.config(state='disabled', text="⏳ 실행 중...")

        def work():
            try:
                run_all(sel, p, strategies, log=gui_log,
                        on_row=lambda row: log_q.put(('row', row)))
            except Exception as e:
                gui_log(f"❌ 오류: {e}")
            finally:
                log_q.put(('done', None))

        threading.Thread(target=work, daemon=True).start()

    run_btn.config(command=start)

    def load_symbols():
        nonlocal all_symbols
        syms = fetch_symbols(log=gui_log)
        all_symbols = syms
        root.after(0, lambda: (refresh_list(), select_default()))

    refresh_list()
    select_default()
    threading.Thread(target=load_symbols, daemon=True).start()

    gui_log("✅ 준비 완료! 전략과 코인 선택 후 [▶️ 백테스트 시작]을 누르세요.")
    gui_log("   추천: 1h봉 365일, 전략 전부 체크 → 4종 직접 비교")
    gui_log("   ⚠️ 결과표의 '전반부/후반부'가 둘 다 +여야 신뢰할 수 있습니다.")
    poll_queue()
    root.mainloop()


# ==================== CLI ====================
def run_cli():
    ap = argparse.ArgumentParser(description='바이낸스 선물 백테스터 v2 (CLI)')
    ap.add_argument('--cli', action='store_true', help='CLI 모드 강제')
    ap.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    ap.add_argument('--days', type=int, default=DEFAULTS['days'])
    ap.add_argument('--interval', default=DEFAULTS['interval'])
    ap.add_argument('--strategies', default='trend,meanrev,combo,baseline',
                    help=f"쉼표 구분: {','.join(STRATEGIES)}")
    ap.add_argument('--equity', type=float, default=DEFAULTS['equity'])
    ap.add_argument('--risk', type=float, default=DEFAULTS['risk_pct'])
    ap.add_argument('--all', action='store_true', help='바이낸스 전체 코인')
    args = ap.parse_args()

    p = dict(DEFAULTS)
    p['days'] = args.days
    p['interval'] = args.interval
    p['equity'] = args.equity
    p['risk_pct'] = args.risk

    strategies = [s.strip() for s in args.strategies.split(',') if s.strip() in STRATEGIES]
    symbols = fetch_symbols() if args.all else \
        [s.strip().upper() for s in args.symbols.split(',')]
    run_all(symbols, p, strategies)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        run_cli()
    else:
        try:
            launch_gui()
        except ImportError:
            print("⚠️ tkinter 없음 → CLI 모드로 실행합니다.")
            print("   (GUI를 쓰려면: Linux는 sudo apt install python3-tk)")
            run_cli()
