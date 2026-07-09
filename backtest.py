#!/usr/bin/env python3
"""
바이낸스 선물 백테스트 — 봇과 동일한 전략 (UT Bot + EMA + ADX 동적 TP)

사용법:
  python backtest.py                                    # BTC/XRP/DOGE, 365일
  python backtest.py --symbols BTCUSDT --days 180
  python backtest.py --symbols BTCUSDT,XRPUSDT,DOGEUSDT --days 365

- 데이터: 바이낸스 선물 메인넷 5분봉 자동 다운로드 (API 키 불필요, 심볼당 약 70회 요청)
- 다운로드한 데이터는 backtest_data/ 폴더에 캐시되어 재실행 시 재사용
- 결과: 콘솔 요약 + backtest_result.csv (요약) + backtest_trades_{심볼}.csv (전체 거래)

전략 (봇 5_gui.py와 동일):
- 진입: 완성된 5분봉 기준 UT Bot(10, 2) 방향 + EMA(34/55) 크로스 AND 조건
- 익절: 진입 시 ADX(10) >= 21이면 TP 1.2%(추세장), 아니면 1.0%(횡보장)
        → 거래소 TP 주문 방식 = TP 가격에 정확히 청산되는 것으로 모델링
- 손절: 고정 손절 없음. 역신호(UT+EMA) 발생 시 청산 후 즉시 반대 진입(스위칭)
- 수수료: 테이커 0.04% × 진입/청산 (왕복 0.08%)
- 사이즈: 진입금 50 USDT × 레버리지 3배 (봇 기본값)
"""

import argparse
import os
import time

import numpy as np
import pandas as pd
import requests

# ==================== 설정 (봇 기본값과 동일) ====================
FEE_RATE = 0.0004      # 테이커 0.04%
AMOUNT = 50.0          # 진입금 (USDT)
LEVERAGE = 3           # 레버리지
TP_TREND = 1.2         # 추세장 TP % (ADX >= 21)
TP_SIDEWAYS = 1.0      # 횡보장 TP % (ADX < 21)
ADX_PERIOD = 10
ADX_TREND_TH = 21
UT_SENS = 10
UT_ATR = 2
EMA_FAST = 34
EMA_SLOW = 55
WARMUP = 60            # 지표 안정화 구간 (봉)
# 격리마진 근사 강제청산: 가격이 100/레버리지 % 역행하면 증거금 전액 손실
LIQ_MOVE_PCT = 100.0 / LEVERAGE


# ==================== 데이터 다운로드 ====================
def fetch_klines(symbol, days, interval='5m', cache_dir='backtest_data'):
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"{symbol}_{interval}_{days}d.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        print(f"  📂 캐시 사용: {cache} ({len(df):,}봉)")
        return df

    url = "https://fapi.binance.com/fapi/v1/klines"
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    rows = []
    cur = start
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
                print(f"\n  ⚠️ 재시도 {attempt + 1}/5: {e}")
                time.sleep(2 * (attempt + 1))
        if data is None:
            raise RuntimeError(f"{symbol} 다운로드 실패 - 네트워크 확인 필요")
        if not data:
            break
        rows.extend(data)
        cur = data[-1][6] + 1  # 마지막 봉 close_time + 1ms
        print(f"\r  ⬇️  {symbol} 다운로드 중: {len(rows):,}봉", end='')
        time.sleep(0.15)  # 요청 제한 회피
        if len(data) < 1500:
            break
    print()

    df = pd.DataFrame(rows, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'qv', 'n', 'tb', 'tq', 'ig'])
    df = df[['ts', 'open', 'high', 'low', 'close', 'volume']].astype(float)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    df = df.set_index('ts')
    df = df[~df.index.duplicated(keep='first')].sort_index()
    df.to_csv(cache)
    print(f"  💾 캐시 저장: {cache}")
    return df


# ==================== 지표 (3_indicators.py와 동일 공식) ====================
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


# ==================== 시뮬레이션 ====================
def run_backtest(df, symbol):
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values

    ut = ut_bot(df, UT_SENS, UT_ATR)
    fast = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    slow = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    ema_long = (fast > slow).values
    ema_short = (fast < slow).values
    adx = adx_series(df, ADX_PERIOD).values

    long_sig = (ut == 1) & ema_long
    short_sig = (ut == -1) & ema_short

    trades = []
    pos = None  # {'side','entry','qty','tp_price','tp_pct','entry_i','min_roi'}
    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    def tp_pct_at(i):
        return TP_TREND if adx[i] >= ADX_TREND_TH else TP_SIDEWAYS

    def open_pos(side, i):
        """봉 i 신호 → 봉 i+1 시가 진입 (봇은 봉 마감 직후 시장가 진입)"""
        entry = o[i + 1]
        tp = tp_pct_at(i)
        tp_price = entry * (1 + tp / 100) if side == 'LONG' else entry * (1 - tp / 100)
        return {'side': side, 'entry': entry, 'qty': AMOUNT * LEVERAGE / entry,
                'tp_price': tp_price, 'tp_pct': tp, 'entry_i': i + 1, 'min_roi': 0.0}

    def close_pos(p, exit_price, exit_i, reason):
        nonlocal equity, peak, max_dd
        if p['side'] == 'LONG':
            gross = p['qty'] * (exit_price - p['entry'])
        else:
            gross = p['qty'] * (p['entry'] - exit_price)
        fee = FEE_RATE * p['qty'] * (p['entry'] + exit_price)
        net = gross - fee
        equity += net
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        trades.append({
            '시각': df.index[exit_i], '포지션': p['side'], '진입가': p['entry'],
            '청산가': exit_price, 'TP%': p['tp_pct'],
            'ROI%': round(gross / AMOUNT * 100, 2), '수익': round(gross, 4),
            '수수료': round(fee, 4), '순손익': round(net, 4),
            '최저ROI%': round(p['min_roi'], 2), '유형': reason,
            '보유(봉)': exit_i - p['entry_i'],
        })

    i = WARMUP
    while i < len(df) - 1:
        if pos is None:
            if long_sig[i]:
                pos = open_pos('LONG', i)
            elif short_sig[i]:
                pos = open_pos('SHORT', i)
            i += 1
            continue

        # 보유 중 — 진입 봉부터 평가
        j = max(i, pos['entry_i'])
        if j >= len(df) - 1:
            break

        if pos['side'] == 'LONG':
            liq_price = pos['entry'] * (1 - LIQ_MOVE_PCT / 100)
            roi_low = (l[j] - pos['entry']) / pos['entry'] * 100 * LEVERAGE
            pos['min_roi'] = min(pos['min_roi'], roi_low)
            if l[j] <= liq_price:                       # 강제청산 (근사)
                close_pos(pos, liq_price, j, '강제청산')
                pos = None
            elif h[j] >= pos['tp_price']:               # 거래소 TP 체결
                exit_price = max(pos['tp_price'], o[j])  # 갭 상승 시 시가 체결
                close_pos(pos, exit_price, j, 'TP익절')
                pos = None
            elif short_sig[j] and j >= pos['entry_i']:  # 역신호 → 스위칭
                close_pos(pos, o[j + 1], j, '스위칭')
                pos = open_pos('SHORT', j)
        else:  # SHORT
            liq_price = pos['entry'] * (1 + LIQ_MOVE_PCT / 100)
            roi_low = (pos['entry'] - h[j]) / pos['entry'] * 100 * LEVERAGE
            pos['min_roi'] = min(pos['min_roi'], roi_low)
            if h[j] >= liq_price:
                close_pos(pos, liq_price, j, '강제청산')
                pos = None
            elif l[j] <= pos['tp_price']:
                exit_price = min(pos['tp_price'], o[j])  # 갭 하락 시 시가 체결
                close_pos(pos, exit_price, j, 'TP익절')
                pos = None
            elif long_sig[j] and j >= pos['entry_i']:
                close_pos(pos, o[j + 1], j, '스위칭')
                pos = open_pos('LONG', j)

        i = j + 1

    return pd.DataFrame(trades), max_dd


def summarize(trades, symbol, max_dd, days):
    if trades.empty:
        print(f"\n### {symbol}: 거래 없음")
        return None
    total = len(trades)
    wins = (trades['순손익'] > 0).sum()
    losses = total - wins
    win_rate = wins / total * 100
    gross = trades['수익'].sum()
    fees = trades['수수료'].sum()
    net = trades['순손익'].sum()
    tp_n = (trades['유형'] == 'TP익절').sum()
    sw_n = (trades['유형'] == '스위칭').sum()
    liq_n = (trades['유형'] == '강제청산').sum()
    avg_win = trades.loc[trades['순손익'] > 0, '순손익'].mean() if wins else 0
    avg_loss = trades.loc[trades['순손익'] <= 0, '순손익'].mean() if losses else 0
    worst = trades['순손익'].min()
    worst_roi = trades['최저ROI%'].min()

    print(f"\n{'=' * 62}")
    print(f"### {symbol} — {days}일 백테스트 (진입금 {AMOUNT:.0f} USDT × {LEVERAGE}x)")
    print(f"{'=' * 62}")
    print(f"  거래 횟수     : {total:,}회 (TP익절 {tp_n} / 스위칭 {sw_n} / 강제청산 {liq_n})")
    print(f"  승 / 패       : {wins} / {losses}  → 승률 {win_rate:.1f}%")
    print(f"  총 수익(수수료 전): {gross:+,.2f} USDT")
    print(f"  총 수수료     : -{fees:,.2f} USDT")
    print(f"  순손익        : {net:+,.2f} USDT  (진입금 대비 {net / AMOUNT * 100:+.1f}%)")
    print(f"  평균 승 / 패  : {avg_win:+.2f} / {avg_loss:+.2f} USDT")
    print(f"  최대 단일 손실: {worst:+.2f} USDT | 보유 중 최저 ROI: {worst_roi:+.1f}%")
    print(f"  누적 최대 낙폭: {max_dd:+.2f} USDT")

    return {
        '심볼': symbol, '거래수': total, '승': wins, '패': losses,
        '승률%': round(win_rate, 1), 'TP익절': tp_n, '스위칭': sw_n, '강제청산': liq_n,
        '총수익USDT': round(gross, 2), '총수수료USDT': round(fees, 2),
        '순손익USDT': round(net, 2), '평균승': round(avg_win, 2),
        '평균패': round(avg_loss, 2), '최대단일손실': round(worst, 2),
        '최저ROI%': round(worst_roi, 1), '최대낙폭USDT': round(max_dd, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='BTCUSDT,XRPUSDT,DOGEUSDT')
    ap.add_argument('--days', type=int, default=365)
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',')]

    print("=" * 62)
    print(f"🔬 백테스트 시작: {', '.join(symbols)} | 최근 {args.days}일 | 5분봉")
    print(f"   전략: UT({UT_SENS},{UT_ATR}) + EMA({EMA_FAST}/{EMA_SLOW})"
          f" | TP 추세 {TP_TREND}% / 횡보 {TP_SIDEWAYS}% | 수수료 {FEE_RATE * 100:.2f}%×2")
    print("=" * 62)

    summaries = []
    for sym in symbols:
        print(f"\n[{sym}] 데이터 준비...")
        df = fetch_klines(sym, args.days)
        if len(df) < WARMUP + 10:
            print(f"  ⚠️ 데이터 부족: {len(df)}봉")
            continue
        trades, max_dd = run_backtest(df, sym)
        row = summarize(trades, sym, max_dd, args.days)
        if row:
            summaries.append(row)
            out = f"backtest_trades_{sym}.csv"
            trades.to_csv(out, index=False, encoding='utf-8-sig')
            print(f"  📝 거래 내역 저장: {out}")

    if summaries:
        result = pd.DataFrame(summaries)
        result.to_csv('backtest_result.csv', index=False, encoding='utf-8-sig')
        print(f"\n{'=' * 62}")
        print("📊 종합 요약 (backtest_result.csv 저장됨)")
        print(f"{'=' * 62}")
        print(result.to_string(index=False))
        total_net = result['순손익USDT'].sum()
        total_fee = result['총수수료USDT'].sum()
        print(f"\n  ➡️  전체 순손익: {total_net:+,.2f} USDT | 전체 수수료: -{total_fee:,.2f} USDT")


if __name__ == '__main__':
    main()
