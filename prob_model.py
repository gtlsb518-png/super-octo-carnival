#!/usr/bin/env python3
"""
확률(%) 기반 매매 분석기 — 독립 실행 도구

기존 백테스터(backtest.py)와 별개로 동작한다.
"신호가 떴다/안 떴다"(이진)가 아니라 "이 상태에서 이길 확률 몇 %"를 집계하고,
그 숫자가 믿을 만한지 검증한 뒤, 기댓값이 양수일 때만 매매한다.

핵심 개념 3가지
---------------
1) 랜덤 기준선
   익절 +a%, 손절 -b%일 때 무작위 가격(랜덤워크)에서도
   승률은 b/(a+b)가 나온다. 손익분기 승률도 같은 값이다.
   → 승률 60%가 나왔다면 예측력은 0이다. 기준선을 넘은 만큼만 우위다.

2) 캘리브레이션
   모델이 "65%"라 말한 경우들이 실제로 65% 이겨야 그 숫자에 의미가 있다.
   신뢰도 곡선과 Brier score로 검증한다. 어긋나면 그 %는 장식일 뿐이다.

3) Walk-forward
   과거 구간에서 확률표를 만들고, 본 적 없는 구간에서 평가한다.
   같은 데이터로 만들고 평가하면 무조건 좋아 보인다.

실행:
    python prob_model.py --multi-tf                 # 15m/1h/4h/1d 비교 (권장)
    python prob_model.py --multi-tf --synthetic     # 버그 검사 (우위 0이어야 정상)
    python prob_model.py --sweep                    # TP/SL 조합 비교
    python prob_model.py --interval 1h --days 1500  # 단일 시간봉 상세 분석

--multi-tf 는 각 시간봉의 ATR에 맞춰 TP/SL을 자동 산출한다.
고정 %를 쓰면 시간봉마다 도달 난이도가 완전히 달라져 비교가 성립하지 않는다.
"""

import os
import sys
import time
import argparse
import subprocess

# ==================== 라이브러리 자동 설치 ====================
def _ensure(packages):
    """없는 라이브러리를 자동 설치한다 (9_main.py와 같은 방식)."""
    missing = []
    for mod, pkg in packages.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if not missing:
        return
    print(f"📦 라이브러리 설치 중: {', '.join(missing)}")
    for pkg in missing:
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"   ✅ {pkg}")
        except Exception as e:
            print(f"   ❌ {pkg} 설치 실패: {e}")
            print(f"   수동 설치: pip install {' '.join(missing)}")
            input("\n엔터를 누르면 종료합니다...")
            sys.exit(1)
    print()


_ensure({'numpy': 'numpy', 'pandas': 'pandas', 'requests': 'requests'})

import numpy as np
import pandas as pd


def _pause_exit(code=0):
    """더블클릭 실행 시 창이 바로 닫히지 않도록 대기."""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("\n엔터를 누르면 종료합니다...")
    except Exception:
        pass
    sys.exit(code)

BINANCE_HOSTS = [
    'https://fapi.binance.com/fapi/v1/klines',   # USDT-M 선물
    'https://api.binance.com/api/v3/klines',     # 현물 (폴백)
]

INTERVAL_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000,
    '30m': 1_800_000, '1h': 3_600_000, '2h': 7_200_000,
    '4h': 14_400_000, '1d': 86_400_000,
}

# 시간봉별 기본 조회 기간 — 긴 봉일수록 표본을 모으려면 더 긴 기간이 필요하다
TF_DEFAULT_DAYS = {
    '15m': 900,    # ≈ 86,000봉
    '1h': 1500,    # ≈ 36,000봉
    '4h': 2200,    # ≈ 13,000봉
    '1d': 3000,    # ≈  3,000봉  ← 통계적으로 빠듯함
}

DEFAULT_TIMEFRAMES = ['15m', '1h', '4h', '1d']


# ==================== 데이터 ====================
def load_csv(path, log=print):
    """로컬 CSV 로드. ts/open/high/low/close/volume 컬럼 필요."""
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get('ts') or cols.get('timestamp') or cols.get('time') or df.columns[0]
    df = df.rename(columns={tcol: 'ts'})
    need = ['open', 'high', 'low', 'close', 'volume']
    for c in need:
        if c not in df.columns:
            raise ValueError(f"CSV에 '{c}' 컬럼이 없습니다. 필요: ts,{','.join(need)}")
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts')[need].astype(float)
    df = df[~df.index.duplicated(keep='first')].sort_index()
    log(f"  ✅ CSV 로드: {len(df):,}봉 ({df.index[0]} ~ {df.index[-1]})")
    return df


def synthetic_klines(n=60000, interval='5m', vol_pct=None, seed=42, drift=0.0, log=print):
    """합성 랜덤워크 — 도구 자체 검증용.

    가격에 예측 가능한 구조가 전혀 없으므로, 올바른 분석기라면
    승률은 정확히 랜덤 기준선 b/(a+b)에 수렴하고 skill score는 0이어야 한다.
    여기서 우위가 나온다면 그건 코드 버그(미래 참조 등)다.
    """
    rng = np.random.default_rng(seed)
    step = INTERVAL_MS.get(interval, 300_000) // 1000

    # 변동성은 시간의 제곱근에 비례 — 5분봉 0.12% 기준으로 환산
    if vol_pct is None:
        vol_pct = 0.12 * np.sqrt(INTERVAL_MS.get(interval, 300_000) / 300_000)

    # 변동성 군집(GARCH 유사) — 실제 시장의 유일하게 예측 가능한 성질
    # ω를 목표 변동성에 맞춰 잡아야 무조건부 변동성이 vol_pct로 수렴한다
    alpha, beta = 0.08, 0.90
    target_var = (vol_pct / 100) ** 2
    omega = target_var * (1 - alpha - beta)

    sigma = np.zeros(n)
    sigma[0] = vol_pct / 100
    for i in range(1, n):
        sigma[i] = np.sqrt(omega + alpha * (sigma[i-1] * rng.normal())**2
                           + beta * sigma[i-1]**2)

    ret = rng.normal(drift / 100, 1.0, n) * sigma
    close = 50000 * np.exp(np.cumsum(ret))

    # 봉 내부 고저는 종가 주변 노이즈로 생성
    noise = np.abs(rng.normal(0, 1, (n, 2))) * (sigma * close)[:, None]
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + noise[:, 0]
    low = np.minimum(open_, close) - noise[:, 1]
    volume = np.abs(rng.lognormal(3, 1, n)) * 100

    idx = pd.to_datetime(
        np.arange(n) * step + int(time.time()) - n * step, unit='s')
    df = pd.DataFrame({'open': open_, 'high': high, 'low': low,
                       'close': close, 'volume': volume}, index=idx)
    df.index.name = 'ts'
    log(f"  🎲 합성 랜덤워크 생성: {n:,}봉 (예측 가능한 방향성 없음)")
    return df


def fetch_klines(symbol, days, interval='5m', log=print):
    """바이낸스 klines 다운로드 (로컬 CSV 캐시)."""
    import requests

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_data')
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f'{symbol}_{interval}_{days}d.csv')

    if os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 12:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
            log(f"  💾 {symbol}: 캐시 사용 ({len(df):,}봉)")
            return df

    step = INTERVAL_MS.get(interval, 300_000)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000

    rows, cursor = [], start_ms
    last_err = None
    for host in BINANCE_HOSTS:
        rows, cursor, last_err = [], start_ms, None
        try:
            while cursor < end_ms:
                r = requests.get(host, timeout=20, params={
                    'symbol': symbol, 'interval': interval,
                    'startTime': cursor, 'limit': 1500})
                r.raise_for_status()
                batch = r.json()
                if not batch:
                    break
                rows.extend(batch)
                cursor = batch[-1][0] + step
                if len(batch) < 1500:
                    break
                time.sleep(0.12)
            if rows:
                log(f"  ✅ {symbol}: {len(rows):,}봉 다운로드 ({host.split('/')[2]})")
                break
        except Exception as e:
            last_err = e
            log(f"  ⚠️ {host.split('/')[2]} 실패: {e}")

    if not rows:
        raise RuntimeError(
            f"{symbol} 데이터를 받지 못했습니다.\n"
            f"    원인: {last_err}\n"
            f"    확인할 것:\n"
            f"      1) 인터넷 연결 / 방화벽 / VPN\n"
            f"      2) 심볼 이름이 맞는지 (예: BTCUSDT, ETHUSDT — 'BTC'만 쓰면 실패)\n"
            f"      3) 바이낸스 접속이 막힌 환경이면 --csv 로 로컬 파일 사용\n"
            f"      4) 도구 동작만 확인하려면 --synthetic")

    df = pd.DataFrame(rows, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'qv', 'n', 'tb', 'tq', 'ig'])
    df = df[['ts', 'open', 'high', 'low', 'close', 'volume']].astype(float)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    df = df.set_index('ts')
    df = df[~df.index.duplicated(keep='first')].sort_index()
    try:
        df.to_csv(cache)
    except Exception:
        pass
    return df


# ==================== 지표 (backtest.py / 3_indicators.py와 동일 공식) ====================
def atr_rma(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def ut_bot(df, sensitivity=1.0, atr_period=10):
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


def adx_series(df, period=14):
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


# ==================== 특징(상태) 생성 ====================
def build_features(df, cfg):
    """봉 i의 특징은 봉 i 종가까지의 정보만 사용한다 (미래 참조 없음).

    반환: 원본 df에 특징 컬럼이 붙은 DataFrame
    """
    f = df.copy()

    f['ut'] = ut_bot(f, cfg['ut_sens'], cfg['ut_atr'])
    ema_f = f['close'].ewm(span=cfg['ema_fast'], adjust=False).mean()
    ema_s = f['close'].ewm(span=cfg['ema_slow'], adjust=False).mean()
    f['ema_up'] = (ema_f > ema_s).astype(int)
    f['adx'] = adx_series(f, cfg['adx_period'])

    # 변동성: ATR을 가격으로 정규화 (%)
    f['atr_pct'] = atr_rma(f, 14) / f['close'] * 100

    # 거래량: 20봉 이동평균 대비 비율
    f['vol_ratio'] = f['volume'] / f['volume'].rolling(20).mean()

    # 모멘텀: 최근 N봉 수익률 (%)
    f['ret_n'] = f['close'].pct_change(cfg['mom_lookback']) * 100

    # 캔들 실체 비율: 방향성 있는 움직임의 비중
    rng = (f['high'] - f['low']).replace(0, np.nan)
    f['body'] = (f['close'] - f['open']) / rng

    # 세션 (UTC 기준)
    hour = f.index.hour
    f['session'] = np.select(
        [(hour >= 0) & (hour < 8), (hour >= 8) & (hour < 16)],
        [0, 1], default=2)   # 0=아시아 1=유럽 2=미국

    return f


# 분위수 경계는 학습 구간에서만 계산해 검증 구간에 적용한다
# (전체 데이터로 계산하면 미래 정보가 새어든다)
QUANTILE_COLS = ['atr_pct', 'vol_ratio', 'ret_n', 'body']


def fit_bins(train_df, n_bins):
    """학습 구간에서 분위수 경계를 학습한다."""
    edges = {}
    qs = np.linspace(0, 1, n_bins + 1)[1:-1]
    for c in QUANTILE_COLS:
        vals = train_df[c].replace([np.inf, -np.inf], np.nan).dropna()
        edges[c] = np.unique(np.quantile(vals, qs)) if len(vals) > 100 else np.array([0.0])
    return edges


def apply_bins(df, edges, cfg):
    """학습된 경계로 연속값을 구간 번호로 바꾼다."""
    s = pd.DataFrame(index=df.index)
    for c in QUANTILE_COLS:
        s[c] = np.digitize(df[c].fillna(0).values, edges[c])
    s['ut'] = df['ut']
    s['ema_up'] = df['ema_up']
    s['adx'] = np.digitize(df['adx'].fillna(20).values, cfg['adx_edges'])
    s['session'] = df['session']
    return s


def state_key(binned, cols):
    """선택된 컬럼들을 하나의 상태 문자열로 합친다."""
    return binned[cols].astype(int).astype(str).agg('|'.join, axis=1)


# ==================== 삼중 배리어 라벨링 ====================
def triple_barrier(df, tp_pct, sl_pct, max_hold, side=1):
    """각 봉 i에 대해: i+1 시가로 진입했을 때의 결과.

    반환 (outcome, bars_held, pnl)
      outcome  1 = 익절 도달, 0 = 손절 도달, 2 = 시간 초과 청산
              -1 = 데이터 부족(표본 아님)
      pnl     실현 손익 % (수수료 제외, 롱/숏 방향 반영)

    ⚠️ 시간 초과 건을 표본에서 빼면 안 된다.
       빼면 '가까운 배리어에 먼저 닿은 건'만 남아 승률이 크게 부풀려진다.
       실제 매매에서는 보유 한도가 되면 시장가로 나가며 손익이 확정되므로,
       그 시점 종가 기준 손익을 그대로 기록한다.

    같은 봉 안에서 익절·손절이 모두 닿으면 어느 쪽이 먼저인지 알 수 없으므로
    보수적으로 손절로 처리한다.
    """
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    n = len(df)

    outcome = np.full(n, -1, dtype=int)
    held = np.full(n, -1, dtype=int)
    pnl = np.full(n, np.nan)

    for i in range(n - 1):
        entry = o[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue

        if side == 1:
            tp_price = entry * (1 + tp_pct / 100)
            sl_price = entry * (1 - sl_pct / 100)
        else:
            tp_price = entry * (1 - tp_pct / 100)
            sl_price = entry * (1 + sl_pct / 100)

        end = min(i + 1 + max_hold, n)
        if end <= i + 1:
            continue

        done = False
        for j in range(i + 1, end):
            if side == 1:
                hit_tp = h[j] >= tp_price
                hit_sl = l[j] <= sl_price
            else:
                hit_tp = l[j] <= tp_price
                hit_sl = h[j] >= sl_price

            if hit_sl:               # 동시 도달 시 손절 우선 (보수적)
                outcome[i], held[i], pnl[i] = 0, j - i, -sl_pct
                done = True
                break
            if hit_tp:
                outcome[i], held[i], pnl[i] = 1, j - i, tp_pct
                done = True
                break

        if not done:                 # 시간 초과 → 마지막 봉 종가로 청산
            exit_price = c[end - 1]
            ret = (exit_price / entry - 1) * 100 * side
            outcome[i], held[i], pnl[i] = 2, end - 1 - i, ret

    return outcome, held, pnl


# ==================== 평가 지표 ====================
def brier_score(prob, outcome):
    return float(np.mean((prob - outcome) ** 2))


def calibration_table(prob, outcome, n_bins=10):
    """신뢰도 곡선: 예측 확률 구간별 실제 승률."""
    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(prob, bins) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({
            '예측구간': f'{bins[b]*100:.0f}~{bins[b+1]*100:.0f}%',
            '건수': int(m.sum()),
            '평균예측': float(prob[m].mean()) * 100,
            '실제승률': float(outcome[m].mean()) * 100,
        })
    t = pd.DataFrame(rows)
    if not t.empty:
        t['오차'] = t['실제승률'] - t['평균예측']
    return t


def top_pct_masks(prob, levels):
    """예측 확률 상위 q%를 고르는 마스크들.

    고정 임계값(0.6 등)을 쓰면 확률 분포가 그보다 낮은 설정에서
    표본이 0이 되어 비교가 불가능해진다. 분위수 기준이면 항상 동작한다.
    """
    out = []
    for q in levels:
        thr = float(np.quantile(prob, 1 - q / 100))
        out.append((q, thr, prob >= thr))
    return out


def kelly_fraction(p, tp, sl):
    """켈리 비율. b = 이길 때 배당 / 질 때 손실."""
    b = tp / sl
    f = (p * b - (1 - p)) / b
    return max(0.0, f)


# ==================== Walk-forward ====================
def walk_forward(feat, labels, valid, cfg, log=print):
    """학습 구간에서 확률표를 만들고 다음 구간에서 평가한다."""
    n = len(feat)
    fold_size = n // (cfg['n_folds'] + 1)

    preds, actuals, states_used, test_idx = [], [], [], []

    for k in range(cfg['n_folds']):
        tr_end = fold_size * (k + 1)
        te_end = min(fold_size * (k + 2), n)
        if te_end - tr_end < 50:
            continue

        tr_idx = np.arange(0, tr_end)
        te_idx = np.arange(tr_end, te_end)
        tr_idx = tr_idx[valid[tr_idx]]
        te_idx = te_idx[valid[te_idx]]
        if len(tr_idx) < cfg['min_samples'] * 4 or len(te_idx) == 0:
            continue

        # 분위수 경계와 확률표 모두 학습 구간에서만 만든다
        edges = fit_bins(feat.iloc[tr_idx], cfg['n_bins'])
        binned = apply_bins(feat, edges, cfg)
        keys = state_key(binned, cfg['state_cols'])

        tr_keys = keys.iloc[tr_idx].values
        tr_y = labels[tr_idx]

        base_rate = float(tr_y.mean())

        # 상태별 승률 집계 + 라플라스 평활(표본 적은 상태를 기준선 쪽으로 당김)
        tab = pd.DataFrame({'k': tr_keys, 'y': tr_y}).groupby('k')['y'].agg(['sum', 'count'])
        m = cfg['smoothing']
        tab['p'] = (tab['sum'] + base_rate * m) / (tab['count'] + m)

        p_map = tab['p'].to_dict()
        cnt_map = tab['count'].to_dict()

        te_keys = keys.iloc[te_idx].values
        # 학습 구간에 표본이 부족한 상태는 기준선으로 대체 (추측하지 않음)
        p_hat = np.array([
            p_map[k_] if cnt_map.get(k_, 0) >= cfg['min_samples'] else base_rate
            for k_ in te_keys])

        preds.append(p_hat)
        actuals.append(labels[te_idx])
        test_idx.append(te_idx)
        states_used.append(np.array([cnt_map.get(k_, 0) >= cfg['min_samples']
                                     for k_ in te_keys]))

        log(f"  fold {k+1}: 학습 {len(tr_idx):,} → 검증 {len(te_idx):,} "
            f"(학습 기준승률 {base_rate*100:.2f}%)")

    if not preds:
        return None

    return (np.concatenate(preds),
            np.concatenate(actuals),
            np.concatenate(test_idx),
            np.concatenate(states_used))


# ==================== 리포트 ====================
def report(prob, actual, gross, known, cfg, log=print):
    """actual = 순손익>0 여부(0/1), gross = 수수료 제외 전 실현손익 %"""
    tp, sl, fee = cfg['tp'], cfg['sl'], cfg['fee']
    net = gross - fee                              # 실제 손에 쥐는 손익
    base_pnl = float(net.mean())
    actual_base = float(actual.mean())

    log("\n" + "=" * 66)
    log("📊 기준선 — 우위가 0이면 나와야 하는 값")
    log("=" * 66)
    log(f"  랜덤워크의 매매당 기대손익 : {-fee:+.4f}%   ← 정확히 수수료만큼 잃음")
    log(f"  검증구간 실측 기대손익     : {base_pnl:+.4f}%   ← 필터 없이 전부 진입")
    log(f"  차이(= 시장 자체의 방향성) : {base_pnl+fee:+.4f}%p")
    log(f"  참고) 실측 승률 {actual_base*100:.2f}% / "
        f"익절 도달 {float((gross >= tp - 1e-9).mean())*100:.1f}% / "
        f"손절 도달 {float((gross <= -sl + 1e-9).mean())*100:.1f}%")
    log("  ※ 승률은 배리어 거리와 보유한도에 따라 달라지므로 단독으로는 의미가 없다.")
    log("    판단 기준은 언제나 '매매당 기대손익'이다.")

    log("\n" + "=" * 66)
    log("📊 확률 모델에 정보가 있는가 (검증구간, out-of-sample)")
    log("=" * 66)
    bs_model = brier_score(prob, actual)
    bs_base = brier_score(np.full_like(prob, actual_base), actual)
    skill = (1 - bs_model / bs_base) * 100 if bs_base > 0 else 0.0

    log(f"  Brier score (모델)  : {bs_model:.5f}   ← 낮을수록 좋음")
    log(f"  Brier score (기준선): {bs_base:.5f}")
    log(f"  Skill score         : {skill:+.2f}%   ← 양수여야 모델에 정보가 있음")
    if skill <= 0:
        log("  ⚠️ 0 이하 = 이 특징들은 기준선보다 나은 정보를 담고 있지 않음")

    log("\n  [신뢰도 곡선] 예측이 맞으면 '평균예측'과 '실제승률'이 비슷해야 함")
    ct = calibration_table(prob, actual, cfg['calib_bins'])
    if not ct.empty:
        for _, r in ct.iterrows():
            flag = '✅' if abs(r['오차']) < 2.0 else ('⚠️' if abs(r['오차']) < 5.0 else '❌')
            log(f"    {flag} {r['예측구간']:>10} | {int(r['건수']):>7,}건 | "
                f"예측 {r['평균예측']:5.2f}% | 실제 {r['실제승률']:5.2f}% | "
                f"오차 {r['오차']:+5.2f}%p")

    log("\n" + "=" * 66)
    log("📊 확률 기준 매매 시뮬레이션 (실현손익 기반)")
    log("=" * 66)
    log(f"  {'진입범위':>10} | {'확률≥':>7} | {'매매수':>8} | {'승률':>7} | "
        f"{'매매당손익':>10} | {'1/4켈리':>8}")
    log("  " + "-" * 66)

    rows = []
    for q, thr, m in top_pct_masks(prob, cfg['top_pct']):
        if m.sum() == 0:
            continue
        wr = float(actual[m].mean())
        avg = float(net[m].mean())
        kf = kelly_fraction(wr, tp, sl) * 0.25
        rows.append((q, int(m.sum()), wr, avg))
        log(f"  {'상위 '+str(q)+'%':>10} | {thr*100:>6.1f}% | {m.sum():>8,} | "
            f"{wr*100:>6.2f}% | {avg:>+9.4f}% | {kf*100:>7.1f}%")

    log("  " + "-" * 66)
    log(f"  {'필터없음':>10} | {'-':>7} | {len(net):>8,} | {actual_base*100:>6.2f}% | "
        f"{base_pnl:>+9.4f}% | {'-':>8}")

    known_rate = float(known.mean()) * 100
    log(f"\n  ℹ️ 검증 표본 중 학습구간에 충분한 사례가 있던 비율: {known_rate:.1f}%")
    log("     (낮으면 상태를 너무 잘게 쪼갠 것 — --state 를 줄이거나 --bins 를 낮추세요)")

    log("\n" + "=" * 66)
    log("📌 판정")
    log("=" * 66)

    # 필터가 '필터 없음'보다 유의미하게 나은가 (표본오차 감안)
    best = None
    for q, cnt, wr, avg in rows:
        if cnt < 200:
            continue
        se = float(net.std()) / np.sqrt(cnt)      # 표준오차
        if avg > 0 and avg > base_pnl + 2 * se:   # 2σ 초과여야 인정
            best = (q, cnt, wr, avg, se)
            break

    if skill <= 0:
        log("  ❌ 모델에 정보 없음. 이 특징 조합으로는 우위가 없습니다.")
        log("     → 지표를 더 넣지 마세요. 시간축(--interval)을 늘려 다시 확인하세요.")
    elif best is None:
        log("  ⚠️ 정보는 있으나 수수료를 넘는 우위는 확인되지 않았습니다.")
        log("     → 수수료를 낮추거나(메이커 주문), TP를 키워 수수료 비중을 줄이세요.")
    else:
        q, cnt, wr, avg, se = best
        log(f"  ✅ 확률 상위 {q}%만 진입: 매매당 {avg:+.4f}% ({cnt:,}건, 승률 {wr*100:.2f}%)")
        log(f"     필터없음({base_pnl:+.4f}%) 대비 {avg-base_pnl:+.4f}%p, 표준오차 {se:.4f}%")
        log("     ⚠️ 이것만으로 실매매하지 마세요. 반드시 확인할 것:")
        log("        1) 다른 심볼에서도 재현되는가")
        log("        2) 다른 기간에서도 재현되는가")
        log("        3) --synthetic 에서는 우위가 0으로 나오는가 (버그 검사)")


# ==================== 시간봉 비교 ====================
def adapt_state(n_samples, cfg):
    """표본 수에 맞춰 상태 해상도를 낮춘다.

    상태 수 = (구간수)^(축 개수). 표본이 적은데 축을 많이 쓰면
    상태당 사례가 몇 건도 안 남아 확률표가 노이즈가 된다.
    긴 시간봉일수록 표본이 급감하므로 자동으로 줄여야 한다.
    """
    cols, bins = cfg['state_cols'], cfg['n_bins']
    if n_samples < 5_000:
        return [c for c in ['ut', 'ema_up', 'adx'] if c in cols] or cols[:2], 2
    if n_samples < 20_000:
        return cols[:3], 2
    if n_samples < 50_000:
        return cols[:4], bins
    return cols, bins


def analyze_one(df, base_cfg, args, interval, log=print):
    """한 시간봉에 대해 전체 파이프라인을 돌리고 지표를 dict로 반환."""
    cfg = dict(base_cfg)
    atr_med = float((atr_rma(df, 14) / df['close'] * 100).median())

    # TP/SL을 그 시간봉의 변동성에 맞춰 산출 (고정 %는 시간봉마다 난이도가 달라짐)
    if args.auto_tpsl:
        tp = max(args.atr_tp * atr_med, args.fee * 20)
        sl = args.atr_sl * atr_med
    else:
        tp, sl = args.tp, args.sl
    cfg['tp'], cfg['sl'] = tp, sl

    res = {'interval': interval, 'bars': len(df), 'atr': atr_med,
           'tp': tp, 'sl': sl, 'fee_share': args.fee / tp * 100,
           'n': 0, 'base_ev': np.nan, 'skill': np.nan,
           'best_thr': None, 'best_ev': np.nan, 'se': np.nan,
           'coverage': np.nan, 'note': '', 'state': '-'}

    outcome, held, gross = triple_barrier(df, tp, sl, args.max_hold, args.side)
    valid = np.isfinite(gross)
    if valid.sum() < 500:
        res['note'] = '표본부족'
        return res

    labels = np.zeros(len(gross), dtype=int)
    labels[valid] = (gross[valid] - args.fee > 0).astype(int)

    # 표본 수에 맞춰 상태 해상도 자동 조정
    cfg['state_cols'], cfg['n_bins'] = adapt_state(int(valid.sum()), cfg)
    res['state'] = f"{len(cfg['state_cols'])}축×{cfg['n_bins']}"

    feat = build_features(df, cfg)
    out = walk_forward(feat, labels, valid, cfg, log=lambda *_: None)
    if out is None:
        res['note'] = '검증불가'
        return res

    prob, actual, te_idx, known = out
    net = gross[te_idx] - args.fee

    res['n'] = len(net)
    res['base_ev'] = float(net.mean())
    res['coverage'] = float(known.mean()) * 100
    res['hold'] = float(held[held > 0].mean())

    base_rate = float(actual.mean())
    bs_m = brier_score(prob, actual)
    bs_b = brier_score(np.full_like(prob, base_rate), actual)
    res['skill'] = (1 - bs_m / bs_b) * 100 if bs_b > 0 else 0.0

    # 필터를 걸었을 때 필터 없음보다 통계적으로 유의하게 나은가
    sd = float(net.std())
    for q, thr, m in top_pct_masks(prob, cfg['top_pct']):
        if m.sum() < 200:
            continue
        avg = float(net[m].mean())
        se = sd / np.sqrt(int(m.sum()))
        if np.isnan(res['best_ev']) or avg > res['best_ev']:
            res['best_ev'], res['best_thr'], res['se'] = avg, q, se

    return res


def multi_tf(args, cfg, log=print):
    """15m / 1h / 4h / 1d 를 같은 기준으로 비교한다."""
    tfs = [t.strip() for t in args.timeframes.split(',') if t.strip()]

    log("\n" + "=" * 78)
    log("📊 시간봉 비교")
    log("=" * 78)
    if args.auto_tpsl:
        log(f"  TP = {args.atr_tp}×ATR (최소 수수료×20) / SL = {args.atr_sl}×ATR")
        log("  → 시간봉마다 변동성이 다르므로 고정 %가 아닌 ATR 배수로 맞춘다")
    else:
        log(f"  TP = {args.tp}% / SL = {args.sl}% (고정)")
    log(f"  수수료 {args.fee}% | 최대보유 {args.max_hold}봉 | "
        f"{'롱' if args.side == 1 else '숏'}")

    results = []
    for tf in tfs:
        days = TF_DEFAULT_DAYS.get(tf, args.days)
        log(f"\n  ── {tf} (최근 {days}일) ──")
        try:
            if args.synthetic:
                n = min(80000, int(days * 86400 * 1000 / INTERVAL_MS.get(tf, 300000)))
                df = synthetic_klines(n=max(n, 3000), interval=tf, log=log)
            elif args.csv:
                df = load_csv(args.csv, log=log)
            else:
                df = fetch_klines(args.symbol, days, tf, log=log)
        except Exception as e:
            log(f"     ❌ 데이터 실패: {e}")
            continue

        r = analyze_one(df, cfg, args, tf, log=log)
        results.append(r)
        if r['note']:
            log(f"     ⚠️ {r['note']}")
        else:
            log(f"     표본 {r['n']:,} | ATR {r['atr']:.3f}% | "
                f"TP {r['tp']:.2f}% / SL {r['sl']:.2f}%")

    if not results:
        log("\n  ❌ 분석할 수 있는 시간봉이 없습니다.")
        return 1

    log("\n" + "=" * 78)
    log("📋 결과")
    log("=" * 78)
    log(f"  {'시간봉':>6} | {'표본':>7} | {'ATR':>6} | {'TP/SL':>12} | "
        f"{'수수료비중':>9} | {'상태':>6} | {'커버':>5} | {'기준EV':>9} | {'skill':>7} | {'최고EV':>9}")
    log("  " + "-" * 96)

    for r in results:
        if r['note']:
            log(f"  {r['interval']:>6} | {r['note']:>7} | {'-':>6} | {'-':>12} | "
                f"{'-':>9} | {'-':>6} | {'-':>5} | {'-':>9} | {'-':>7} | {'-':>9}")
            continue
        fs = r['fee_share']
        fflag = '❌' if fs > 10 else ('⚠️' if fs > 5 else '✅')
        cflag = '⚠️' if r['coverage'] < 50 else ' '
        log(f"  {r['interval']:>6} | {r['n']:>7,} | {r['atr']:>5.2f}% | "
            f"{r['tp']:>5.2f}/{r['sl']:>5.2f}% | {fflag}{fs:>7.1f}% | "
            f"{r['state']:>6} | {cflag}{r['coverage']:>3.0f}% | "
            f"{r['base_ev']:>+8.4f}% | {r['skill']:>+6.2f}% | {r['best_ev']:>+8.4f}%")

    log("\n  📖 열 설명")
    log("     · 기준EV  = 필터 없이 전부 진입했을 때 매매당 손익")
    log(f"     · skill   = 확률모델의 정보량. 0 이하면 예측력 없음")
    log("     · 최고EV  = 확률 필터를 걸었을 때 최선의 매매당 손익")
    log("     · 상태   = 확률표 해상도 (축 개수 × 구간 수). 표본에 맞춰 자동 조정됨")
    log("     · 커버   = 검증표본 중 학습구간에 사례가 충분했던 비율 (낮으면 신뢰도↓)")
    log(f"     · 우위가 0이면 기준EV는 {-args.fee:+.3f}%(= -수수료) 근처가 나온다")

    log("\n" + "=" * 78)
    log("📌 시간봉별 판정")
    log("=" * 78)
    for r in results:
        if r['note']:
            log(f"  {r['interval']:>4}: ❌ {r['note']}")
            continue
        tag = f"  {r['interval']:>4}:"
        if r['coverage'] < 50:
            log(f"{tag} ⚠️ 표본 커버리지 {r['coverage']:.0f}% — 상태를 너무 잘게 쪼갬. "
                f"--state 를 줄이세요")
        elif r['skill'] <= 0:
            log(f"{tag} ❌ 예측력 없음 (skill {r['skill']:+.2f}%)")
        elif np.isnan(r['best_ev']) or r['best_ev'] <= 0:
            log(f"{tag} ⚠️ 정보는 있으나 수수료를 못 넘음 "
                f"(최고 EV {r['best_ev']:+.4f}%)")
        elif r['best_ev'] > r['base_ev'] + 2 * r['se']:
            log(f"{tag} ✅ 확률 상위 {r['best_thr']}%에서 "
                f"매매당 {r['best_ev']:+.4f}% (2σ 초과, 표준오차 {r['se']:.4f}%)")
        else:
            log(f"{tag} ⚠️ EV는 양수지만 표본오차 범위 안 "
                f"({r['best_ev']:+.4f}% ± {2*r['se']:.4f}%) — 우연일 수 있음")

    # 결과 저장
    try:
        out_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(out_dir, f'prob_result_{args.symbol}.csv')
        pd.DataFrame(results).to_csv(path, index=False, encoding='utf-8-sig')
        log(f"\n  💾 결과 저장: {path}")
    except Exception as e:
        log(f"\n  ⚠️ 결과 저장 실패: {e}")

    small = [r for r in results if not r['note'] and r['n'] < 3000]
    if small:
        log(f"\n  ⚠️ 표본 3,000건 미만: {', '.join(r['interval'] for r in small)}")
        log("     긴 시간봉은 표본이 급격히 줄어 통계적 신뢰도가 떨어집니다.")
        log("     일봉은 수년치를 모아도 수천 건이라, 우연한 우위가 나오기 쉽습니다.")

    log("")
    return 0


# ==================== TP/SL 스윕 ====================
def sweep(df, cfg, args, log=print):
    """TP/SL 조합별로 '아무 필터 없이 진입했을 때' 결과를 비교한다.

    여기서 보려는 것은 어떤 조합이 돈을 버는가가 아니다.
    (랜덤워크에서는 모든 조합이 기대값 0이다.)
    보려는 것은 수수료가 총이익을 얼마나 갉아먹는가다.
    """
    fee = cfg['fee']
    atr_pct = float((atr_rma(df, 14) / df['close'] * 100).median())

    log("\n" + "=" * 74)
    log("📊 TP/SL 조합 비교")
    log("=" * 74)
    log(f"  이 심볼의 중앙 ATR: {atr_pct:.3f}% (봉당 평균 변동폭)")
    log(f"  왕복 수수료+슬리피지: {fee}%")
    log("")
    log(f"  {'TP':>6} {'SL':>6} | {'승률':>7} | {'수수료비중':>10} | "
        f"{'평균보유':>8} | {'매매당손익':>10} | {'랜덤기대':>8}")
    log("  " + "-" * 72)

    combos = [(0.5, 0.75), (0.5, 1.0), (1.0, 1.0), (1.0, 1.5), (1.5, 1.5),
              (2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (3.0, 4.5), (5.0, 5.0)]

    for tp, sl in combos:
        # 배리어가 멀수록 도달에 오래 걸리므로 보유 한도를 비례해 늘린다
        mh = max(24, int(args.max_hold * (tp + sl) / 2.5))
        outcome, held, gross = triple_barrier(df, tp, sl, mh, args.side)
        m = np.isfinite(gross)
        if m.sum() < 200:
            continue
        g = gross[m]
        wr = float((g - fee > 0).mean())
        net = g - fee
        fee_share = fee / tp * 100
        avg_hold = float(held[held > 0].mean())

        flag = '❌' if fee_share > 10 else ('⚠️' if fee_share > 5 else '✅')
        log(f"  {tp:>5.1f}% {sl:>5.1f}% | {wr*100:>7.2f}% | "
            f"{flag} {fee_share:>6.1f}% | {avg_hold:>7.0f}봉 | "
            f"{net.mean():>+8.4f}% | {-fee:>+7.3f}%")

    log("\n  💡 읽는 법")
    log("     · 수수료비중 = 수수료 / 익절폭. 이만큼이 매매마다 그냥 사라진다")
    log("       ✅ 5% 미만 권장 / ⚠️ 5~10% / ❌ 10% 초과는 구조적으로 불리")
    log("     · 매매당손익이 '랜덤기대'(= -수수료) 근처면 우위가 없는 것이다")
    log("     · 승률은 배리어 거리에 따라 자동으로 변하므로 단독 비교는 무의미하다")
    log("")
    log(f"  📌 이 심볼 기준 권장 최소 TP: {max(fee * 20, atr_pct * 1.5):.2f}%")
    log(f"     (수수료의 20배 = {fee*20:.2f}%, ATR의 1.5배 = {atr_pct*1.5:.2f}% 중 큰 값)")
    log("")
    return 0


# ==================== 메인 ====================
def main():
    ap = argparse.ArgumentParser(description='확률(%) 기반 매매 분석기')
    ap.add_argument('--symbol', default='BTCUSDT')
    ap.add_argument('--days', type=int, default=365)
    ap.add_argument('--interval', default='5m')
    ap.add_argument('--tp', type=float, default=1.0, help='익절 %%')
    ap.add_argument('--sl', type=float, default=1.5, help='손절 %%')
    ap.add_argument('--fee', type=float, default=0.08, help='왕복 수수료+슬리피지 %%')
    ap.add_argument('--max-hold', type=int, default=48, help='최대 보유 봉 수')
    ap.add_argument('--side', type=int, default=1, choices=[1, -1], help='1=롱 -1=숏')
    ap.add_argument('--bins', type=int, default=3, help='연속값 분위 구간 수')
    ap.add_argument('--folds', type=int, default=5)
    ap.add_argument('--min-samples', type=int, default=50, help='상태별 최소 표본')
    ap.add_argument('--state', default='ut,ema_up,adx,atr_pct,ret_n',
                    help='상태 구성 컬럼 (쉼표구분). 선택지: '
                         'ut,ema_up,adx,atr_pct,vol_ratio,ret_n,body,session')
    ap.add_argument('--csv', default=None, help='로컬 CSV 경로 (다운로드 대신 사용)')
    ap.add_argument('--synthetic', action='store_true',
                    help='합성 랜덤워크로 자체 검증 (승률≈기준선, skill≈0 이어야 정상)')
    ap.add_argument('--sweep', action='store_true',
                    help='TP/SL 조합을 훑어 수수료 대비 효율 비교')
    ap.add_argument('--multi-tf', action='store_true',
                    help='여러 시간봉을 같은 기준으로 비교')
    ap.add_argument('--timeframes', default=','.join(DEFAULT_TIMEFRAMES),
                    help='비교할 시간봉 (기본: 15m,1h,4h,1d)')
    ap.add_argument('--auto-tpsl', action='store_true', default=True,
                    help='TP/SL을 그 시간봉의 ATR 배수로 자동 산출 (기본 켜짐)')
    ap.add_argument('--fixed-tpsl', dest='auto_tpsl', action='store_false',
                    help='--tp/--sl 값을 그대로 사용')
    ap.add_argument('--atr-tp', type=float, default=3.0, help='TP = 이 값 × ATR')
    ap.add_argument('--atr-sl', type=float, default=1.5, help='SL = 이 값 × ATR')
    args = ap.parse_args()

    # 인자 없이 그냥 실행(더블클릭)하면 시간봉 비교 백테스트를 돌린다
    if len(sys.argv) == 1:
        args.multi_tf = True
        print("  ℹ️ 인자가 없어 기본 백테스트를 실행합니다 "
              "(BTCUSDT, 15m/1h/4h/1d 비교)")
        print("     다른 옵션은 --help 로 확인하세요.\n")

    cfg = {
        'ut_sens': 1.0, 'ut_atr': 10,
        'ema_fast': 34, 'ema_slow': 55,
        'adx_period': 10,
        'adx_edges': np.array([15, 21, 30]),
        'mom_lookback': 12,
        'n_bins': args.bins,
        'n_folds': args.folds,
        'min_samples': args.min_samples,
        'smoothing': 30,
        'calib_bins': 10,
        'state_cols': [c.strip() for c in args.state.split(',') if c.strip()],
        'tp': args.tp, 'sl': args.sl, 'fee': args.fee,
        'top_pct': [50, 30, 20, 10, 5],   # 예측 확률 상위 q%만 진입
    }

    print("=" * 62)
    print("  확률(%) 기반 매매 분석기")
    print("=" * 62)

    if args.multi_tf:
        print(f"  심볼 {args.symbol} | 상태 구성: {', '.join(cfg['state_cols'])}")
        return multi_tf(args, cfg)

    print(f"  심볼 {args.symbol} | {args.interval} | 최근 {args.days}일 | "
          f"{'롱' if args.side == 1 else '숏'}")
    print(f"  익절 +{args.tp}% / 손절 -{args.sl}% / 수수료 {args.fee}% / "
          f"최대보유 {args.max_hold}봉")
    print(f"  상태 구성: {', '.join(cfg['state_cols'])}")
    print("=" * 62)

    print("\n[1/4] 데이터 수집")
    if args.synthetic:
        df = synthetic_klines(interval=args.interval)
    elif args.csv:
        df = load_csv(args.csv)
    else:
        df = fetch_klines(args.symbol, args.days, args.interval)
    if len(df) < 2000:
        print(f"  ⚠️ 표본이 {len(df):,}봉뿐입니다. --days 를 늘리세요.")

    if args.sweep:
        return sweep(df, cfg, args)

    print("\n[2/4] 특징 생성")
    feat = build_features(df, cfg)
    print(f"  {len(feat):,}봉 × {len(cfg['state_cols'])}개 상태축")

    print("\n[3/4] 삼중 배리어 라벨링")
    outcome, held, gross = triple_barrier(df, args.tp, args.sl, args.max_hold, args.side)
    valid = np.isfinite(gross)
    n_tp = int((outcome == 1).sum())
    n_sl = int((outcome == 0).sum())
    n_to = int((outcome == 2).sum())
    print(f"  익절도달 {n_tp:,} | 손절도달 {n_sl:,} | 시간초과청산 {n_to:,}")
    print(f"  (시간초과분도 실현손익으로 표본에 포함 — 빼면 승률이 부풀려짐)")
    if valid.sum() < 1000:
        print("  ⚠️ 표본이 너무 적습니다. --days 를 늘리세요.")
        return 1
    print(f"  평균 보유: {held[held > 0].mean():.1f}봉")

    # 모델이 맞힐 대상 = '수수료까지 내고 남는가'
    labels = np.zeros(len(gross), dtype=int)
    labels[valid] = (gross[valid] - args.fee > 0).astype(int)

    print("\n[4/4] Walk-forward 검증")
    out = walk_forward(feat, labels, valid, cfg)
    if out is None:
        print("  ❌ 검증 구간을 만들지 못했습니다. --days 를 늘리세요.")
        return 1
    prob, actual, te_idx, known = out

    report(prob, actual, gross[te_idx], known, cfg)
    print()
    return 0


if __name__ == '__main__':
    try:
        _pause_exit(main())
    except KeyboardInterrupt:
        print("\n중단됨.")
        _pause_exit(1)
    except RuntimeError as e:
        # 데이터 실패 등 예상 가능한 오류 — 안내만 보여준다
        print("\n" + "=" * 62)
        print("❌ 실행할 수 없습니다")
        print("=" * 62)
        print(f"\n{e}\n")
        _pause_exit(1)
    except Exception as e:
        print("\n" + "=" * 62)
        print("❌ 예상치 못한 오류입니다 (아래 내용을 캡처해 문의하세요)")
        print("=" * 62)
        print(f"\n{e}\n")
        import traceback
        traceback.print_exc()
        _pause_exit(1)
