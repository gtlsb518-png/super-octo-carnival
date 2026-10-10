#!/usr/bin/env python3
"""실제 코인 시장 특징을 흉내 낸 가상 시장 (코인 20개, 15분봉, 4년 사이클) — 백테스트·점검용

실제 바이낸스 차트를 못 받는 환경에서 전략을 비교할 때 쓴다. 넣은 특징:
  · 변동성이 몰려다님 (GARCH) + 가끔 큰 움직임 (꼬리가 두꺼운 분포, t 분포 자유도 4)
  · 코인끼리 같이 움직임 (시장 공통 움직임 + 코인별 움직임, 상관 약 0.6~0.7)
  · 알트는 BTC 보다 더 크게 움직임 (변동성·베타가 큼)
  · 4년 사이클: 1년차 상승 → 2년차 하락 → 3년차 횡보 → 4년차 상승 후 마지막 석 달 급락
  · 순간 꼬리 (봉 저가/고가만 크게 찍고 돌아옴) — 코인 하나만 / 시장 전체가 같이 (1년에 두 번쯤)

  import realistic_market as rm
  m = rm.make(11)          # {'BTC': DataFrame(open/high/low/close/volume, 15분 시간 인덱스), ...}
  rm.describe(m)           # 특징 요약 출력
"""
import numpy as np
import pandas as pd

YEARS = 4
BARS_PER_YEAR = 365 * 96
COINS = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'TRX', 'SUI', 'LINK',
         'AVAX', 'LTC', 'BCH', 'DOT', 'XLM', 'HBAR', 'ETC', 'FIL', 'AAVE', 'ATOM']
START_PX = {'BTC': 60000, 'ETH': 3000, 'BNB': 500, 'SOL': 150, 'XRP': 0.6, 'ADA': 0.5, 'DOGE': 0.15, 'TRX': 0.12,
            'SUI': 1.5, 'LINK': 15, 'AVAX': 35, 'LTC': 80, 'BCH': 400, 'DOT': 7, 'XLM': 0.12, 'HBAR': 0.09,
            'ETC': 25, 'FIL': 5, 'AAVE': 150, 'ATOM': 9}
# 국면별 시장 1년 로그수익 (마지막 해는 9달 상승 + 3달 급락)
REGIMES = [('상승', 0.85), ('하락', -1.0), ('횡보', 0.0), ('상승후급락', None)]


def _garch(rng, n, ann_vol, df=4):
    """GARCH(1,1) + t 분포 충격 → 봉마다 수익률 (평균 연변동성 ann_vol)"""
    target = ann_vol / np.sqrt(BARS_PER_YEAR)
    a, b = 0.07, 0.91
    omega = target ** 2 * (1 - a - b)
    z = rng.standard_t(df, n) / np.sqrt(df / (df - 2))
    r = np.empty(n)
    var = target ** 2
    for i in range(n):
        r[i] = np.sqrt(var) * z[i]
        var = omega + a * r[i] ** 2 + b * var
    return r


def _drift(n_year):
    out = []
    for name, mu in REGIMES:
        if mu is None:
            k = int(n_year * 0.75)
            out.append(np.full(k, 0.6 / k))
            out.append(np.full(n_year - k, -0.75 / (n_year - k)))
        else:
            out.append(np.full(n_year, mu / n_year))
    return np.concatenate(out)


def make(seed, years=YEARS):
    """{코인: 15분봉 DataFrame} years 년치 (국면 순서대로)"""
    rng = np.random.default_rng(seed)
    n = BARS_PER_YEAR * years
    idx = pd.date_range('2021-01-01', periods=n, freq='15min')
    mkt = _garch(rng, n, 0.55)
    drift = np.resize(_drift(BARS_PER_YEAR), n)
    # 시장 전체 순간 꼬리: 1년에 두 번쯤, 봉 하나에서 저가(또는 고가)만 크게
    n_crash = rng.poisson(2 * years)
    crash_at = rng.integers(500, n, n_crash)
    crash_dir = rng.choice([-1, 1], n_crash, p=[0.8, 0.2])
    crash_sz = rng.uniform(0.06, 0.22, n_crash)
    out = {}
    for i, c in enumerate(COINS):
        beta = 1.0 if c == 'BTC' else rng.uniform(1.1, 1.6)
        idio_vol = 0.25 if c in ('BTC', 'ETH') else rng.uniform(0.45, 0.85)
        r = beta * (mkt + drift) + _garch(rng, n, idio_vol) - 0.5 * (beta * 0.55) ** 2 / BARS_PER_YEAR
        close = START_PX[c] * np.exp(np.cumsum(r))
        opn = np.r_[START_PX[c], close[:-1]]
        bar_sig = np.abs(r).mean() * 1.2 + 1e-5
        hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, bar_sig, n)))
        lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, bar_sig, n)))
        # 코인 하나만 순간 꼬리: 1년에 3번쯤
        k = rng.poisson(3 * years)
        at = rng.integers(500, n, k)
        sz = rng.uniform(0.03, 0.12, k)
        down = rng.random(k) < 0.6
        lo[at[down]] *= (1 - sz[down])
        hi[at[~down]] *= (1 + sz[~down])
        for j, d, s in zip(crash_at, crash_dir, crash_sz):
            s = min(0.6, s * beta)
            if d < 0:
                lo[j] = min(lo[j], opn[j] * (1 - s))
            else:
                hi[j] = max(hi[j], opn[j] * (1 + s))
        out[c] = pd.DataFrame({'open': opn, 'high': hi, 'low': lo, 'close': close, 'volume': 1.0}, index=idx)
    return out


def describe(m):
    """실제 코인과 비교용 특징"""
    for c, df in m.items():
        r = np.log(df['close']).diff().dropna()
        yr = df['close'].resample('YE').last().pct_change().dropna() * 100
        wick = ((df['open'] - df['low']) / df['open']).max() * 100
        print(f"{c:5s} 연변동성 {r.std() * np.sqrt(BARS_PER_YEAR) * 100:5.0f}% · 첨도 {r.kurt():5.1f} · "
              f"최대 하락 꼬리 {wick:4.1f}% · 연도별 " + ' / '.join(f"{v:+.0f}%" for v in yr))


if __name__ == '__main__':
    describe(make(11))
