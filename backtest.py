#!/usr/bin/env python3
"""
바이낸스 선물 백테스터 — 완전 독립 실행형 (GUI)

🔥 이 파일 하나만 있으면 실행됩니다 (봇 파일 불필요, API 키 불필요).
   필요 라이브러리(pandas/numpy/requests)는 실행 시 자동 설치됩니다.

실행:
  python backtest.py            ← GUI 실행 (더블클릭도 가능)
  python backtest.py --compare  ← 콘솔(CLI) 모드

기능:
- 모든 수치(진입금/레버리지/수수료/TP/ADX/UT/EMA/기간/시간봉) GUI에서 수정 가능
  → 기본값은 현재 봇 설정과 동일
- 바이낸스 USDT 선물 전체 코인 목록 자동 로드, 검색/다중선택/전체선택
- EMA 4종 비교 모드 (설정값 / 12,26 / 9,21 / 하이브리드)
- 결과는 표로 표시 + backtest_result.csv / backtest_trades_{심볼}_{설정}.csv 저장

전략 (봇 5_gui.py와 동일 로직):
- 진입: 완성봉 기준 UT Bot 방향 + EMA 크로스 AND 조건
- 익절: 진입 시 ADX >= 기준이면 추세장 TP, 아니면 횡보장 TP
        (거래소 TP 주문 = TP 가격에 정확히 청산되는 것으로 모델링)
- 손절: 고정 손절 없음. 역신호 시 청산 + 즉시 반대 진입(스위칭)
- 하이브리드(선택): 손실 중일 때만 빠른 EMA 크로스로 조기청산
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

# ==================== 기본값 (현재 봇 설정과 동일) ====================
DEFAULTS = {
    'amount': 50.0,        # 진입금 (USDT)
    'leverage': 3,         # 레버리지
    'fee_pct': 0.04,       # 수수료 % (테이커, 진입/청산 각각)
    'tp_trend': 1.2,       # 추세장 TP %
    'tp_sideways': 1.0,    # 횡보장 TP %
    'adx_period': 10,      # ADX 기간
    'adx_th': 21,          # ADX 추세장 기준
    'ut_sens': 10.0,       # UT Bot Key Value
    'ut_atr': 2,           # UT Bot ATR Period
    'ema_fast': 34,        # EMA Fast
    'ema_slow': 55,        # EMA Slow
    'days': 365,           # 백테스트 기간 (일)
    'interval': '5m',      # 시간봉
    'hybrid': False,       # 하이브리드 조기청산
    'hybrid_fast': 9,      # 하이브리드 빠른 EMA Fast
    'hybrid_slow': 21,     # 하이브리드 빠른 EMA Slow
    'hybrid_roi_th': -5.0, # 조기청산 발동 ROI % (이하 손실일 때만)
}

DEFAULT_SYMBOLS = ['BTCUSDT', 'XRPUSDT', 'DOGEUSDT']
FALLBACK_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT',
    'DOGEUSDT', 'TRXUSDT', 'TONUSDT', 'LINKUSDT', 'AVAXUSDT', 'DOTUSDT',
    'LTCUSDT', 'BCHUSDT', 'NEARUSDT', 'APTUSDT', 'SUIUSDT', 'ARBUSDT',
    'OPUSDT', 'FILUSDT',
]
INTERVALS = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h']


# ==================== 데이터 ====================
def fetch_symbols(log=print):
    """바이낸스 USDT 선물 무기한 전체 심볼"""
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


def fetch_klines(symbol, days, interval='5m', cache_dir='backtest_data', log=print):
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


# ==================== 지표 (봇 3_indicators.py와 동일 공식) ====================
def atr_rma(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def ut_bot(df, sensitivity, atr_period):
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


def ema_pair(df, fast, slow):
    return (df['close'].ewm(span=fast, adjust=False).mean(),
            df['close'].ewm(span=slow, adjust=False).mean())


def adx_full_series(df, period):
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


# ==================== 시뮬레이션 ====================
def run_backtest(df, p):
    """p: 파라미터 dict (DEFAULTS 형식)"""
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values

    fee_rate = p['fee_pct'] / 100.0
    amount = p['amount']
    lev = p['leverage']
    liq_move = 100.0 / lev
    warmup = max(60, int(p['ema_slow']) + 5)

    ut = ut_bot(df, p['ut_sens'], p['ut_atr'])
    fast, slow = ema_pair(df, p['ema_fast'], p['ema_slow'])
    ema_long = (fast > slow).values
    ema_short = (fast < slow).values
    adx = adx_full_series(df, p['adx_period']).values

    long_sig = (ut == 1) & ema_long
    short_sig = (ut == -1) & ema_short

    if p.get('hybrid'):
        hf, hs = ema_pair(df, p['hybrid_fast'], p['hybrid_slow'])
        h_dead = (hf < hs).values
        h_gold = (hf > hs).values
        roi_th = p['hybrid_roi_th']

    trades = []
    pos = None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    def tp_pct_at(i):
        return p['tp_trend'] if adx[i] >= p['adx_th'] else p['tp_sideways']

    def open_pos(side, i):
        entry = o[i + 1]
        tp = tp_pct_at(i)
        tp_price = entry * (1 + tp / 100) if side == 'LONG' else entry * (1 - tp / 100)
        return {'side': side, 'entry': entry, 'qty': amount * lev / entry,
                'tp_price': tp_price, 'tp_pct': tp, 'entry_i': i + 1, 'min_roi': 0.0}

    def close_pos(pp, exit_price, exit_i, reason):
        nonlocal equity, peak, max_dd
        if pp['side'] == 'LONG':
            gross = pp['qty'] * (exit_price - pp['entry'])
        else:
            gross = pp['qty'] * (pp['entry'] - exit_price)
        fee = fee_rate * pp['qty'] * (pp['entry'] + exit_price)
        net = gross - fee
        equity += net
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        trades.append({
            '시각': df.index[exit_i], '포지션': pp['side'], '진입가': pp['entry'],
            '청산가': exit_price, 'TP%': pp['tp_pct'],
            'ROI%': round(gross / amount * 100, 2), '수익': round(gross, 4),
            '수수료': round(fee, 4), '순손익': round(net, 4),
            '최저ROI%': round(pp['min_roi'], 2), '유형': reason,
            '보유(봉)': exit_i - pp['entry_i'],
        })

    i = warmup
    while i < len(df) - 1:
        if pos is None:
            if long_sig[i]:
                pos = open_pos('LONG', i)
            elif short_sig[i]:
                pos = open_pos('SHORT', i)
            i += 1
            continue

        j = max(i, pos['entry_i'])
        if j >= len(df) - 1:
            break

        if pos['side'] == 'LONG':
            liq_price = pos['entry'] * (1 - liq_move / 100)
            roi_low = (l[j] - pos['entry']) / pos['entry'] * 100 * lev
            roi_close = (c[j] - pos['entry']) / pos['entry'] * 100 * lev
            pos['min_roi'] = min(pos['min_roi'], roi_low)
            if l[j] <= liq_price:
                close_pos(pos, liq_price, j, '강제청산')
                pos = None
            elif h[j] >= pos['tp_price']:
                close_pos(pos, max(pos['tp_price'], o[j]), j, 'TP익절')
                pos = None
            elif short_sig[j]:
                close_pos(pos, o[j + 1], j, '스위칭')
                pos = open_pos('SHORT', j)
            elif p.get('hybrid') and roi_close <= roi_th and h_dead[j]:
                close_pos(pos, o[j + 1], j, '조기청산')
                pos = None
        else:
            liq_price = pos['entry'] * (1 + liq_move / 100)
            roi_low = (pos['entry'] - h[j]) / pos['entry'] * 100 * lev
            roi_close = (pos['entry'] - c[j]) / pos['entry'] * 100 * lev
            pos['min_roi'] = min(pos['min_roi'], roi_low)
            if h[j] >= liq_price:
                close_pos(pos, liq_price, j, '강제청산')
                pos = None
            elif l[j] <= pos['tp_price']:
                close_pos(pos, min(pos['tp_price'], o[j]), j, 'TP익절')
                pos = None
            elif long_sig[j]:
                close_pos(pos, o[j + 1], j, '스위칭')
                pos = open_pos('LONG', j)
            elif p.get('hybrid') and roi_close <= roi_th and h_gold[j]:
                close_pos(pos, o[j + 1], j, '조기청산')
                pos = None

        i = j + 1

    return pd.DataFrame(trades), max_dd


def summarize(trades, symbol, cfg_name, max_dd, p):
    if trades.empty:
        return None
    total = len(trades)
    wins = int((trades['순손익'] > 0).sum())
    win_rate = wins / total * 100
    return {
        '심볼': symbol, '설정': cfg_name, '거래수': total,
        '승률%': round(win_rate, 1),
        'TP익절': int((trades['유형'] == 'TP익절').sum()),
        '스위칭': int((trades['유형'] == '스위칭').sum()),
        '조기청산': int((trades['유형'] == '조기청산').sum()),
        '강제청산': int((trades['유형'] == '강제청산').sum()),
        '총수익': round(trades['수익'].sum(), 2),
        '수수료': round(trades['수수료'].sum(), 2),
        '순손익': round(trades['순손익'].sum(), 2),
        '최대단일손실': round(trades['순손익'].min(), 2),
        '최저ROI%': round(trades['최저ROI%'].min(), 1),
        '최대낙폭': round(max_dd, 2),
    }


def make_configs(p, compare):
    """실행할 설정 목록. compare=True면 4종 비교"""
    base_name = f"EMA{int(p['ema_fast'])}-{int(p['ema_slow'])}"
    if p.get('hybrid'):
        base_name += "+하이브리드"
    if not compare:
        return [dict(p, name=base_name)]
    return [
        dict(p, name=f"EMA{int(p['ema_fast'])}-{int(p['ema_slow'])}(설정값)", hybrid=False),
        dict(p, name='EMA12-26', ema_fast=12, ema_slow=26, hybrid=False),
        dict(p, name='EMA9-21', ema_fast=9, ema_slow=21, hybrid=False),
        dict(p, name='하이브리드', hybrid=True),
    ]


def run_all(symbols, p, compare, log=print, on_row=None):
    """심볼 목록 전체 백테스트. on_row(row)로 결과 행 전달"""
    configs = make_configs(p, compare)
    log("=" * 50)
    log(f"🔬 백테스트: {len(symbols)}개 심볼 | {p['days']}일 | {p['interval']}봉")
    log(f"   설정 {len(configs)}종: {', '.join(c['name'] for c in configs)}")
    log("=" * 50)

    summaries = []
    for k, sym in enumerate(symbols, 1):
        log(f"\n[{k}/{len(symbols)}] {sym} 데이터 준비...")
        try:
            df = fetch_klines(sym, p['days'], p['interval'], log=log)
        except Exception as e:
            log(f"  ❌ {sym} 건너뜀: {e}")
            continue
        if len(df) < 200:
            log(f"  ⚠️ {sym} 데이터 부족({len(df)}봉) — 건너뜀")
            continue
        for cfg in configs:
            trades, max_dd = run_backtest(df, cfg)
            row = summarize(trades, sym, cfg['name'], max_dd, cfg)
            if row:
                summaries.append(row)
                if on_row:
                    on_row(row)
                log(f"  ▶ [{cfg['name']}] {row['거래수']}회 | 승률 {row['승률%']}% "
                    f"| 순손익 {row['순손익']:+,.2f} | 수수료 -{row['수수료']:,.2f}")
                safe = cfg['name'].replace('/', '-')
                trades.to_csv(f"backtest_trades_{sym}_{safe}.csv",
                              index=False, encoding='utf-8-sig')

    if summaries:
        result = pd.DataFrame(summaries)
        result.to_csv('backtest_result.csv', index=False, encoding='utf-8-sig')
        log("\n" + "=" * 50)
        log("📊 완료! 요약: backtest_result.csv 저장됨")
        for name, grp in result.groupby('설정', sort=False):
            log(f"   {name:20s}: 합산 순손익 {grp['순손익'].sum():+10,.2f} USDT"
                f" (수수료 -{grp['수수료'].sum():,.2f})")
        log("=" * 50)
    else:
        log("\n⚠️ 결과 없음")
    return summaries


# ==================== GUI ====================
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    BG, PANEL, FG, ACCENT = '#1e1e1e', '#2d2d2d', '#ffffff', '#00ff88'

    root = tk.Tk()
    root.title("📊 바이낸스 선물 백테스터")
    root.geometry("1150x760")
    root.configure(bg=BG)

    log_q = queue.Queue()
    running = [False]
    all_symbols = list(FALLBACK_SYMBOLS)

    # ---------- 좌측: 설정 ----------
    left = tk.Frame(root, bg=PANEL)
    left.pack(side='left', fill='y', padx=8, pady=8)

    tk.Label(left, text="⚙️ 설정 (모두 수정 가능)", bg=PANEL, fg=ACCENT,
             font=('Arial', 12, 'bold')).pack(pady=(8, 4))

    form = tk.Frame(left, bg=PANEL)
    form.pack(padx=10)

    fields = [
        ('진입금 (USDT)', 'amount'), ('레버리지 (배)', 'leverage'),
        ('수수료 % (편도)', 'fee_pct'), ('추세장 TP %', 'tp_trend'),
        ('횡보장 TP %', 'tp_sideways'), ('ADX 기간', 'adx_period'),
        ('ADX 추세 기준', 'adx_th'), ('UT Key Value', 'ut_sens'),
        ('UT ATR 기간', 'ut_atr'), ('EMA Fast', 'ema_fast'),
        ('EMA Slow', 'ema_slow'), ('기간 (일)', 'days'),
    ]
    vars_ = {}
    for r, (label, key) in enumerate(fields):
        tk.Label(form, text=label, bg=PANEL, fg=FG, font=('Arial', 10),
                 anchor='w').grid(row=r, column=0, sticky='w', pady=2)
        v = tk.StringVar(value=str(DEFAULTS[key]))
        tk.Entry(form, textvariable=v, width=10, font=('Arial', 10),
                 justify='center').grid(row=r, column=1, padx=6, pady=2)
        vars_[key] = v

    tk.Label(form, text='시간봉', bg=PANEL, fg=FG, font=('Arial', 10),
             anchor='w').grid(row=len(fields), column=0, sticky='w', pady=2)
    interval_var = tk.StringVar(value=DEFAULTS['interval'])
    ttk.Combobox(form, textvariable=interval_var, values=INTERVALS, width=8,
                 state='readonly').grid(row=len(fields), column=1, padx=6, pady=2)

    # 하이브리드
    tk.Label(left, text="─" * 30, bg=PANEL, fg='#555555').pack()
    hybrid_var = tk.BooleanVar(value=DEFAULTS['hybrid'])
    tk.Checkbutton(left, text="하이브리드 조기청산 사용", variable=hybrid_var,
                   bg=PANEL, fg='#ffaa00', selectcolor=BG, font=('Arial', 10, 'bold'),
                   activebackground=PANEL).pack(anchor='w', padx=10)
    hform = tk.Frame(left, bg=PANEL)
    hform.pack(padx=10)
    hfields = [('빠른 EMA Fast', 'hybrid_fast'), ('빠른 EMA Slow', 'hybrid_slow'),
               ('발동 ROI % 이하', 'hybrid_roi_th')]
    for r, (label, key) in enumerate(hfields):
        tk.Label(hform, text=label, bg=PANEL, fg='#aaaaaa', font=('Arial', 9),
                 anchor='w').grid(row=r, column=0, sticky='w', pady=1)
        v = tk.StringVar(value=str(DEFAULTS[key]))
        tk.Entry(hform, textvariable=v, width=10, font=('Arial', 9),
                 justify='center').grid(row=r, column=1, padx=6, pady=1)
        vars_[key] = v

    tk.Label(left, text="─" * 30, bg=PANEL, fg='#555555').pack()
    compare_var = tk.BooleanVar(value=False)
    tk.Checkbutton(left, text="EMA 4종 비교 모드", variable=compare_var,
                   bg=PANEL, fg='#00ffff', selectcolor=BG, font=('Arial', 10, 'bold'),
                   activebackground=PANEL).pack(anchor='w', padx=10)
    tk.Label(left, text="(설정값 / 12,26 / 9,21 / 하이브리드)", bg=PANEL,
             fg='#888888', font=('Arial', 8)).pack(anchor='w', padx=26)

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
    tk.Button(btns, text="기본3종", command=select_default, bg='#3d3d3d', fg=FG,
              font=('Arial', 9), width=7).pack(side='left', padx=2)

    # ---------- 우측: 결과 + 로그 ----------
    right = tk.Frame(root, bg=BG)
    right.pack(side='left', fill='both', expand=True, pady=8, padx=(0, 8))

    tk.Label(right, text="📊 결과 (backtest_result.csv 자동 저장)", bg=BG, fg=ACCENT,
             font=('Arial', 12, 'bold')).pack(anchor='w')

    cols = ['심볼', '설정', '거래수', '승률%', 'TP익절', '스위칭', '조기청산',
            '강제청산', '총수익', '수수료', '순손익', '최대낙폭', '최저ROI%']
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
        tree.column(col, width=78 if col in ('심볼', '설정') else 62,
                    anchor='center')
    tree.column('설정', width=120)
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
        int_keys = {'leverage', 'adx_period', 'ut_atr', 'ema_fast', 'ema_slow',
                    'days', 'hybrid_fast', 'hybrid_slow'}
        for key, v in vars_.items():
            raw = v.get().strip()
            p[key] = int(float(raw)) if key in int_keys else float(raw)
        p['interval'] = interval_var.get()
        p['hybrid'] = hybrid_var.get()
        return p

    def start():
        if running[0]:
            return
        try:
            p = read_params()
        except ValueError as e:
            messagebox.showwarning("입력 오류", f"숫자를 확인해주세요!\n\n{e}")
            return
        sel = [listbox.get(i) for i in listbox.curselection()]
        if not sel:
            messagebox.showwarning("코인 선택", "코인을 1개 이상 선택해주세요!")
            return
        if len(sel) > 20:
            est = len(sel) * max(1, p['days'] // 5)
            if not messagebox.askyesno(
                    "확인", f"{len(sel)}개 코인 × {p['days']}일 = 다운로드에 "
                    f"시간이 오래 걸릴 수 있습니다 (약 {est}회 API 요청).\n계속할까요?"):
                return
        for item in tree.get_children():
            tree.delete(item)
        running[0] = True
        run_btn.config(state='disabled', text="⏳ 실행 중...")

        def work():
            try:
                run_all(sel, p, compare_var.get(), log=gui_log,
                        on_row=lambda row: log_q.put(('row', row)))
            except Exception as e:
                gui_log(f"❌ 오류: {e}")
            finally:
                log_q.put(('done', None))

        threading.Thread(target=work, daemon=True).start()

    run_btn.config(command=start)

    # 심볼 목록 백그라운드 로드
    def load_symbols():
        nonlocal all_symbols
        syms = fetch_symbols(log=gui_log)
        all_symbols = syms
        root.after(0, lambda: (refresh_list(), select_default()))

    refresh_list()
    select_default()
    threading.Thread(target=load_symbols, daemon=True).start()

    gui_log("✅ 준비 완료! 설정 확인 후 [▶️ 백테스트 시작]을 누르세요.")
    gui_log("   기본값 = 현재 봇 설정 (50 USDT × 3배, TP 1.2/1.0%, EMA 34/55)")
    poll_queue()
    root.mainloop()


# ==================== CLI ====================
def run_cli():
    ap = argparse.ArgumentParser(description='바이낸스 선물 백테스터 (CLI 모드)')
    ap.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    ap.add_argument('--days', type=int, default=DEFAULTS['days'])
    ap.add_argument('--interval', default=DEFAULTS['interval'])
    ap.add_argument('--ema', default=f"{DEFAULTS['ema_fast']},{DEFAULTS['ema_slow']}")
    ap.add_argument('--compare', action='store_true')
    ap.add_argument('--all', action='store_true', help='바이낸스 전체 코인')
    args = ap.parse_args()

    p = dict(DEFAULTS)
    p['days'] = args.days
    p['interval'] = args.interval
    p['ema_fast'], p['ema_slow'] = [int(x) for x in args.ema.split(',')]

    symbols = fetch_symbols() if args.all else \
        [s.strip().upper() for s in args.symbols.split(',')]
    run_all(symbols, p, args.compare)


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
