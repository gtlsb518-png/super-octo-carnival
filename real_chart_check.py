#!/usr/bin/env python3
"""실제 바이낸스 차트로 ① '익절 후 바로 재진입' vs '다음 봉 재진입'  ② TP(익절 %) 조합 비교

  python real_chart_check.py              ← 프로그램 1·2 코인 20개, 최근 2년, ①② 둘 다
  python real_chart_check.py --what tp    ← TP 비교만 (--what reentry 는 재진입 비교만)
  python real_chart_check.py --what risk --days 1460   ← 1시간봉 최대 하락 + 3·5·7·10배 강제청산 비교 (4년)
  python real_chart_check.py --what now --days 1460   ← ★ 지금 봇 설정(15분봉·스위칭만·5배) 그대로 4년 — 수익 나는지, 몇 달 봐야 아는지
  python real_chart_check.py --what now --since 2020-01-01   ← 2020년부터 지금까지 (연도별 표 포함)
  python real_chart_check.py --what grid --since 2020-01-01  ← 봉(15분·1시간·4시간) · 배율 · UT 값 · 횡보 필터 한꺼번에 비교
  python real_chart_check.py --what ut --since 2020-01-01    ← ★ UT 키·ATR 조합 찾기 (앞 기간에서 고르고 뒤 기간으로 확인)
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
         'AVAX', 'LTC', 'BCH', 'DOT', 'XLM', 'HBAR', 'ETC', 'FIL', 'AAVE', 'ATOM']
AMOUNT = {'BTC': 60}            # 나머지 50 (1_config.py 기본값과 같게)
DATA_DIR = os.path.join(BASE, 'real_data')
OUT_TXT = os.path.join(BASE, '실제차트_비교결과.txt')

# 비교할 설정: (이름, 레버리지, 신호, 재진입, 스위칭)
CONFIGS = [
    ('예전: 3배·즉시신호·바로재진입', 3, 'live', 'immediate', 'live'),
    ('지금: 5배·UT마감·바로재진입', 5, 'ut_confirmed', 'immediate', 'close'),
    ('5배·UT마감·다음봉', 5, 'ut_confirmed', 'next_bar', 'close'),
    ('5배·바로재진입·스위칭즉시', 5, 'ut_confirmed', 'immediate', 'live'),
    ('3배·UT마감·바로재진입', 3, 'ut_confirmed', 'immediate', 'close'),
]

# TP 비교 (지금 방식: 5배 · UT 봉마감 + EMA 실시간 · 다음 봉 재진입): (횡보 TP, 추세 TP, 이름)
TP_CONFIGS = [
    (1.2, 1.5, '1.2 / 1.5 (지금)'),
    (1.2, 1.2, '1.2 / 1.2'),
    (1.0, 1.2, '1.0 / 1.2'),
    (1.5, 1.5, '1.5 / 1.5'),
    (1.2, 2.0, '1.2 / 2.0'),
    (1e6, 1e6, '스위칭만 (TP 없음)'),   # 익절 없이 반대 신호 2개(봉 마감)에만 스위칭
]
SLIP_FEE = 0.06      # 슬리피지까지 감안한 1회 비용 % (기본 수수료 0.04%)

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
def params(lev, mode, reentry, amount, tp_s=1.2, tp_t=1.5, fee=0.04, switch='close'):
    p = dict(bt.DEFAULTS)
    p.update(amount=float(amount), leverage=lev, fee_pct=fee, ut_sens=10.0, ut_atr=5,
             ema_fast=34, ema_slow=55, adx_period=10, adx_th=21, tp_trend=tp_t, tp_sideways=tp_s,
             funding_on=True, funding_pct=0.01, funding_hours=8,
             signal_mode=mode, reentry=reentry, switch_mode=switch)
    return p


def combine(by_coin):
    """코인별 거래 → 계좌 전체 요약 (순손익·최대낙폭·월별·반기별)"""
    ts = [t.assign(코인=c) for c, t in by_coin.items() if len(t)]
    if not ts:
        return None
    all_t = pd.concat(ts, ignore_index=True).sort_values('시각')
    eq = np.r_[0.0, all_t['순손익'].cumsum().values]
    monthly = all_t.groupby(all_t['시각'].dt.to_period('M'))['순손익'].sum()
    half = all_t.groupby(all_t['시각'].dt.year.astype(str) + '-' +
                         np.where(all_t['시각'].dt.month <= 6, '상', '하'))['순손익'].sum()
    return dict(all=all_t, net=all_t['순손익'].sum(), mdd=(eq - np.maximum.accumulate(eq)).min(),
                monthly=monthly, half=half)


def tp_compare(data):
    """② TP 조합 비교 — 지금 방식 그대로, 익절 % 만 바꿔서"""
    out('━' * 92)
    out('② TP(익절 %) 비교 — 5배 · UT 봉마감 + EMA 실시간 · 익절 후 바로 재진입 · 스위칭 봉마감 · 횡보(ADX<21)/추세(ADX≥21)')
    res = {n: {} for *_, n in TP_CONFIGS}
    slip = {n: {} for *_, n in TP_CONFIGS}
    t0 = time.time()
    for i, (c, df) in enumerate(data.items(), 1):
        print(f"  TP 계산 {i}/{len(data)}: {c}", flush=True)
        for ts, tt, n in TP_CONFIGS:
            amt = AMOUNT.get(c, 50)
            res[n][c] = bt.run_backtest_live(df, params(5, 'ut_confirmed', 'immediate', amt, ts, tt), bar='1h')[0]
            slip[n][c] = bt.run_backtest_live(df, params(5, 'ut_confirmed', 'immediate', amt, ts, tt, SLIP_FEE),
                                              bar='1h')[0]
    print(f"  (계산 {time.time() - t0:.0f}초)")
    names = [n for *_, n in TP_CONFIGS]
    cur = names[0]
    S = {n: combine(res[n]) for n in names}
    SS = {n: combine(slip[n]) for n in names}
    if any(S[n] is None for n in names):
        out('  거래가 없어 비교할 수 없습니다'); return
    out(f"{'TP 횡보/추세':18s} {'순손익':>9s} {'슬리피지감안':>11s} {'최대낙폭':>9s} {'최악의달':>9s} {'손실달':>6s} "
        f"{'거래':>7s} {'익절비율':>7s} {'청산':>5s} {'지금보다 나은 코인':>14s}")
    for n in names:
        a = S[n]['all']
        better = sum(1 for c in data if res[n][c]['순손익'].sum() > res[cur][c]['순손익'].sum()) if n != cur else None
        out(f"{n:18s} {S[n]['net']:+9,.0f} {SS[n]['net']:+11,.0f} {S[n]['mdd']:+9,.0f} {S[n]['monthly'].min():+9,.0f} "
            f"{(S[n]['monthly'] < 0).sum():>3d}/{len(S[n]['monthly']):<2d} {len(a):7,d} "
            f"{(a['유형'] == 'TP익절').mean() * 100:6.0f}% {(a['유형'] == '강제청산').sum():5d} "
            f"{'-' if better is None else f'{better}/{len(data)}':>14s}")
    out('')
    out('  반기별 순손익 (기간마다 1등이 바뀌는지 — 늘 이기는 값이 진짜 좋은 값)')
    halves = S[cur]['half'].index
    out(f"  {'기간':8s} " + ' '.join(f"{n.split(' (')[0]:>10s}" for n in names))
    wins = {n: 0 for n in names}
    for h in halves:
        vals = [S[n]['half'].get(h, 0.0) for n in names]
        wins[names[int(np.argmax(vals))]] += 1
        out(f"  {h:8s} " + ' '.join(f"{v:+10,.0f}" for v in vals))
    out(f"  {'1등 횟수':8s} " + ' '.join(f"{wins[n]:>10d}" for n in names))
    # 자동 판단: 지금보다 '두 비용 조건 모두 5% 이상' + '반기 대부분' 이겨야 바꿀 만하다고 본다
    out('')
    best = max(names[1:], key=lambda n: S[n]['net'])
    gain = (S[best]['net'] - S[cur]['net']) / max(1.0, abs(S[cur]['net'])) * 100
    gain_s = (SS[best]['net'] - SS[cur]['net']) / max(1.0, abs(SS[cur]['net'])) * 100
    beat = sum(1 for h in halves if S[best]['half'].get(h, 0) > S[cur]['half'].get(h, 0))
    if gain > 5 and gain_s > 5 and beat >= max(1, round(len(halves) * 0.75)):
        out(f"  👉 '{best}' 가 지금보다 {gain:+.0f}% (슬리피지 감안 {gain_s:+.0f}%), 반기 {beat}/{len(halves)}번 앞섬 → 바꿀 만함")
    else:
        out(f"  👉 가장 많이 번 다른 값 '{best}': 지금보다 {gain:+.0f}% (슬리피지 감안 {gain_s:+.0f}%), "
            f"반기 {beat}/{len(halves)}번 앞섬 → 차이가 꾸준하지 않아 지금 값 유지 추천")


RISK_LEVS = [3, 5, 7, 10]
MMR = 1.0        # 유지증거금 % (알트 소액 기준 대략) — 강제청산은 '100/배율 − 이 값' % 반대로 가면


def _liq_line(lev):
    return 100.0 / lev - MMR


def risk_compare(data):
    """③ 1시간봉 최대 하락·상승 + 봇이 들고 있는 동안 가장 크게 반대로 간 폭 → 3·5·7·10배 비교"""
    out('━' * 92)
    out('③ 위험 점검 — 1시간봉 최대 하락/상승과, 봇 포지션이 버텨야 했던 최대 역행 → 배율별 강제청산')
    out(f"   강제청산선(격리, 유지증거금 {MMR:.0f}% 가정): " +
        ' · '.join(f"{L}배 −{_liq_line(L):.1f}%" for L in RISK_LEVS))
    out('')
    out(f"{'코인':6s} {'1시간 최대하락':>16s} {'1시간 최대상승':>16s} {'24시간 최대하락':>18s} "
        f"{'보유 중 최대역행':>14s} {'상위1%':>7s}  " + ' '.join(f"{L}배청산".rjust(6) for L in RISK_LEVS))
    worst = {'h1': (0, '', ''), 'mae': (0, '', '')}
    mae_all = []
    t0 = time.time()
    for i, (c, df) in enumerate(data.items(), 1):
        print(f"  위험 계산 {i}/{len(data)}: {c}", flush=True)
        h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        dn = (h['low'] / h['open'] - 1) * 100
        up = (h['high'] / h['open'] - 1) * 100
        low24 = h['low'][::-1].rolling(24, min_periods=1).min()[::-1]
        dn24 = (low24 / h['open'] - 1) * 100
        # 1배로 돌리면 강제청산이 없어서, 거래마다 '가격이 반대로 가장 많이 간 폭(%)' 이 그대로 나온다
        amt = AMOUNT.get(c, 50)
        p1 = params(1, 'ut_confirmed', 'immediate', amt)
        tr = bt.run_backtest_live(df, p1, bar='1h')[0]
        mae = (-tr['최저ROI%']).clip(lower=0) if len(tr) else pd.Series(dtype=float)
        mae_all.extend(mae.tolist())
        cnt = [int((mae >= _liq_line(L)).sum()) for L in RISK_LEVS]
        mx = float(mae.max()) if len(mae) else 0.0
        p99 = float(mae.quantile(0.99)) if len(mae) else 0.0
        if dn.min() < worst['h1'][0]:
            worst['h1'] = (dn.min(), c, dn.idxmin().strftime('%Y-%m-%d %H시'))
        if len(mae) and mx > worst['mae'][0]:
            worst['mae'] = (mx, c, tr.loc[mae.idxmax(), '시각'].strftime('%Y-%m-%d'))
        out(f"{c:6s} {dn.min():+7.1f}% ({dn.idxmin():%y-%m-%d}) {up.max():+7.1f}% ({up.idxmax():%y-%m-%d}) "
            f"{dn24.min():+7.1f}% ({dn24.idxmin():%y-%m-%d}) {-mx:+13.1f}% {-p99:+6.1f}%  "
            + ' '.join(f"{n:>6d}" for n in cnt))
    out('')
    m = pd.Series(mae_all) if mae_all else pd.Series([0.0])
    out(f"  가장 큰 1시간 하락: {worst['h1'][1]} {worst['h1'][0]:+.1f}% ({worst['h1'][2]})")
    out(f"  봇이 들고 있던 포지션이 가장 크게 반대로 간 폭: {worst['mae'][1]} −{worst['mae'][0]:.1f}% ({worst['mae'][2]} 진입)")
    out(f"  전체 {len(m):,}거래 중 반대로 간 폭: 절반은 −{m.median():.1f}% 이내, 99% 는 −{m.quantile(0.99):.1f}% 이내")
    out('')

    out(f"  배율별 결과 (진입금 BTC 60 / 나머지 50 그대로, 강제청산되면 그 거래는 증거금 전부 손실)")
    out(f"  {'배율':6s} {'순손익':>10s} {'최대낙폭':>10s} {'수익÷낙폭':>9s} {'강제청산':>8s} {'손실달':>7s} {'최악의달':>10s}")
    S = {}
    for L in RISK_LEVS:
        by = {}
        for c, df in data.items():
            p = params(L, 'ut_confirmed', 'immediate', AMOUNT.get(c, 50))
            p['mmr_pct'] = MMR
            by[c] = bt.run_backtest_live(df, p, bar='1h')[0]
        S[L] = combine(by)
        if S[L] is None:
            out('  거래가 없어 비교할 수 없습니다'); return
        a, r = S[L]['all'], S[L]['net'] / max(1.0, abs(S[L]['mdd']))
        out(f"  {str(L) + '배':6s} {S[L]['net']:+10,.0f} {S[L]['mdd']:+10,.0f} {r:9.2f} "
            f"{int((a['유형'] == '강제청산').sum()):8d} {(S[L]['monthly'] < 0).sum():>3d}/{len(S[L]['monthly']):<3d} "
            f"{S[L]['monthly'].min():+10,.0f}")
    print(f"  (계산 {time.time() - t0:.0f}초)")
    out('')
    # 판단: 3배 vs 5배
    a3, a5 = S[3], S[5]
    liq3 = int((a3['all']['유형'] == '강제청산').sum())
    liq5 = int((a5['all']['유형'] == '강제청산').sum())
    r3 = a3['net'] / max(1.0, abs(a3['mdd']))
    r5 = a5['net'] / max(1.0, abs(a5['mdd']))
    out(f"  👉 3배 vs 5배: 순손익 {a3['net']:+,.0f} vs {a5['net']:+,.0f} · 최대낙폭 {a3['mdd']:+,.0f} vs {a5['mdd']:+,.0f}"
        f" · 강제청산 {liq3} vs {liq5}번")
    if a5['net'] <= 0 and a3['net'] <= 0:
        out("     둘 다 손해 → 배율이 낮을수록 덜 잃습니다. 배율보다 전략(TP·신호)부터 손봐야 합니다.")
    elif r3 >= r5:
        out(f"     3배가 낙폭 대비 수익이 같거나 낫고({r3:.2f} vs {r5:.2f}) 강제청산도 {'적습니다' if liq3 < liq5 else '같습니다'} → 3배 추천")
    else:
        msg = f"     5배가 낙폭 대비 수익이 더 좋습니다({r5:.2f} vs {r3:.2f})"
        if liq5 > liq3:
            msg += (f". 대신 강제청산이 {liq5 - liq3}번 더 났습니다 — 실제 봇은 청산되면 그 코인이 멈추고 "
                    f"그날 손실이 한꺼번에 몰립니다")
            out(msg)
            out("     → 큰 폭락 한 번에 크게 잃는 게 싫으면 3배, 감수할 수 있으면 5배")
        else:
            out(msg + " → 5배 유지 추천")
    out("     ※ 실제 봇은 강제청산되면 그 코인이 멈춥니다 (여기서는 계속 매매한다고 계산)")


def main():
    ap = argparse.ArgumentParser(description='실제 차트로 재진입 방식 비교')
    ap.add_argument('--days', type=int, default=730, help='기간 (일, 기본 730 = 2년)')
    ap.add_argument('--since', default=None, help='이 날짜부터 (예: 2020-01-01) — 주면 --days 대신 씀')
    ap.add_argument('--coins', nargs='*', default=COINS, help='코인 (예: BTC ETH SOL)')
    ap.add_argument('--offline', action='store_true', help='저장된 차트만 사용 (다운로드 안 함)')
    ap.add_argument('--what', choices=['all', 'reentry', 'tp', 'risk', 'now', 'grid', 'ut'], default='all',
                    help='all = 재진입+TP+위험 / reentry = 재진입 비교만 / tp = TP 비교만 / '
                         'risk = 1시간봉 최대 하락·보유 중 최대 역행 → 3·5·7·10배 비교 / '
                         'now = 지금 봇 설정(15분봉·스위칭만) 그대로 수익·기간별 수익 확률 / '
                         'grid = 봉·배율·UT 값·횡보 필터 비교 (스위칭만) / '
                         'ut = UT 키·ATR 조합 찾기 (앞 기간 고르기 + 뒤 기간 확인)')
    a = ap.parse_args()
    if a.since:
        a.days = max(60, (pd.Timestamp.now() - pd.Timestamp(a.since)).days + 1)
    coins = [c.upper().replace('USDT', '').replace('/', '') for c in a.coins]

    out(f"📊 실제 바이낸스 차트 비교 — 최근 {a.days}일, 코인 {len(coins)}개, "
        + ("15분봉 (지금 봇 설정)" if a.what == 'now' else "1시간봉 (15분 단위로 따라감)"))
    out(f"   진입금 BTC 60 / 나머지 50 USDT · 손절 없음 · 펀딩 0.01%/8h 가정")
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

    if a.what == 'now':
        now_check(data)
    if a.what == 'grid':
        grid_check(data)
    if a.what == 'ut':
        ut_search(data)
    if a.what in ('all', 'reentry'):
        reentry_compare(data)
    if a.what in ('all', 'tp'):
        tp_compare(data)
    if a.what in ('all', 'risk'):
        risk_compare(data)

    out('')
    out('※ 과거 결과이며 앞으로도 같다는 보장은 없습니다. 펀딩비는 0.01% 고정 가정입니다.')
    out(f'※ 슬리피지감안 = 주문 1회 비용을 {SLIP_FEE}% 로 계산 (실제 체결이 조금씩 밀리는 것까지 반영)')
    try:
        with open(OUT_TXT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(_lines) + '\n')
        print(f"\n💾 결과 저장: {OUT_TXT}")
    except Exception as e:
        print(f"⚠️ 결과 파일 저장 실패: {e}")
    return 0


def now_check(data):
    """★ 지금 봇 설정 그대로: 15분봉 · 스위칭만(TP 없음) · UT 봉마감 + EMA 실시간 (진입·스위칭 같음) · 5배"""
    out('━' * 92)
    out('★ 지금 봇 설정 그대로 — 15분봉 · 스위칭만 · UT 봉마감 + EMA 실시간 · 5배 (꼬리로 강제청산 판정, 유지증거금 1%)')

    def run(c, df, lev, tp_s=1e6, tp_t=1e6):
        p = params(lev, 'ut_confirmed', 'immediate', AMOUNT.get(c, 50), tp_s=tp_s, tp_t=tp_t, switch='live')
        p['mmr_pct'] = 1.0
        return bt.run_backtest_live(df, p, bar='15min')[0]

    t0 = time.time()
    res, levs, tpres = {}, {L: {} for L in (3, 7, 10)}, {}
    for i, (c, df) in enumerate(data.items(), 1):
        print(f"  계산 {i}/{len(data)}: {c}", flush=True)
        res[c] = run(c, df, 5)
        for L in levs:
            levs[L][c] = run(c, df, L)
        tpres[c] = run(c, df, 5, 1.2, 1.5)
    print(f"  (계산 {time.time() - t0:.0f}초)")

    def total(by):
        a = pd.concat([t.assign(코인=c) for c, t in by.items() if len(t)], ignore_index=True)
        if a.empty:
            return None
        a = a.sort_values('시각')
        eq = np.r_[0.0, a['순손익'].cumsum().values]
        mon = a.groupby(a['시각'].dt.to_period('M'))['순손익'].sum()
        return dict(all=a, net=a['순손익'].sum(), mdd=(eq - np.maximum.accumulate(eq)).min(), mon=mon,
                    liq=int((a['유형'] == '강제청산').sum()), fee=a['수수료'].sum(), fund=a['펀딩비'].sum())

    out(f"{'코인':6s} {'기간':>6s} {'순손익':>9s} {'거래':>6s} {'강제청산':>6s} {'최대역행':>8s}")
    for c, df in data.items():
        t = res[c]
        net = t['순손익'].sum() if len(t) else 0.0
        mae = (-t['최저ROI%'] / 5).max() if len(t) else 0.0
        out(f"{c:6s} {(df.index[-1] - df.index[0]).days:>5d}일 {net:+9,.0f} {len(t):6,d} "
            f"{int((t['유형'] == '강제청산').sum()) if len(t) else 0:6d} {mae:7.1f}%")
    T = total(res)
    if T is None:
        out('거래 없음')
        return
    out('━' * 92)
    out(f"전체: 순손익 {T['net']:+,.0f} · 최대 낙폭 {T['mdd']:+,.0f} · 최악의 달 {T['mon'].min():+,.0f} · "
        f"손실 난 달 {(T['mon'] < 0).sum()}/{len(T['mon'])} · 강제청산 {T['liq']}번 · 수수료 {T['fee']:,.0f} · 펀딩 {T['fund']:,.0f}")
    for L in sorted(list(levs) + [5]):
        X = T if L == 5 else total(levs[L])
        if X:
            out(f"  {L:>2}배: 순손익 {X['net']:+9,.0f} · 최대 낙폭 {X['mdd']:+8,.0f} · 최악의 달 {X['mon'].min():+7,.0f} · 강제청산 {X['liq']:4d}번")
    P = total(tpres)
    if P:
        out(f"  (참고) TP+스위칭 5배: 순손익 {P['net']:+,.0f} · 최대 낙폭 {P['mdd']:+,.0f} · 수수료 {P['fee']:,.0f}")

    out('━' * 92)
    out('월별 순손익 (5배, 코인 전부)')
    for m, v in T['mon'].items():
        bar = '█' * min(40, int(abs(v) / max(1.0, T['mon'].abs().max()) * 40))
        out(f"  {m}  {v:+8,.0f}  {'+' if v >= 0 else '-'}{bar}")

    out('━' * 92)
    out('연도별 (5배, 코인 전부) — BTC 는 그해 가격 변화')
    btc = data.get('BTC')
    yr = T['all'].groupby(T['all']['시각'].dt.year)
    for y, g in yr:
        chg = ''
        if btc is not None:
            b = btc[btc.index.year == y]['close']
            if len(b) > 1:
                chg = f"BTC {(b.iloc[-1] / b.iloc[0] - 1) * 100:+5.0f}%  "
        out(f"  {y}  {chg}순손익 {g['순손익'].sum():+8,.0f} · 거래 {len(g):5,d} · 이긴 비율 {(g['순손익'] > 0).mean() * 100:3.0f}% · "
            f"강제청산 {int((g['유형'] == '강제청산').sum()):3d} · 수수료 {g['수수료'].sum():6,.0f}")

    out('━' * 92)
    out('아무 날에나 시작해서 그 기간만 봤을 때 — 수익이 나 있을 확률 (최악)')
    coins = list(data)
    groups = [('프로그램1 코인', coins[:10]), ('프로그램2 코인', coins[10:]), ('20개 전부', coins)]
    idx = pd.date_range(min(df.index[0] for df in data.values()).floor('D'),
                        max(df.index[-1] for df in data.values()).floor('D'), freq='D')
    daily = pd.DataFrame({c: (res[c].groupby(res[c]['시각'].dt.floor('D'))['순손익'].sum() if len(res[c]) else pd.Series(dtype=float))
                          .reindex(idx, fill_value=0.0) for c in coins})
    wins = [('1주', 7), ('2주', 14), ('1달', 30), ('3달', 91), ('6달', 182), ('1년', 365)]
    out(f"  {'':14s} " + ' '.join(f"{n:>13s}" for n, _ in wins))
    for name, cs in groups:
        if not cs:
            continue
        d = daily[cs].sum(axis=1)
        cells = []
        for _, w in wins:
            r = d.rolling(w).sum().dropna()
            cells.append(f"{(r > 0).mean() * 100:3.0f}% ({r.min():+6,.0f})" if len(r) else '      -      ')
        out(f"  {name:14s} " + ' '.join(f"{x:>13s}" for x in cells))
    out('  → 짧은 기간 결과는 운이 크게 좌우합니다. 이 표에서 확률이 80~90% 넘는 기간부터가 "봐야 아는" 기간입니다.')


# grid 비교: (이름, 봉, 배율, UT 키, UT ATR, 횡보 필터 ADX 최소값(0=끔)[, 익절 ROI%(0=없음, 익절 후 다음 신호까지 대기)[, 거래량 배수(0=끔)]])
GRID = [
    ('지금 15분 5배 UT10/5', '15min', 5, 10, 5, 0),
    ('15분 5배 ROI10% 익절+대기', '15min', 5, 10, 5, 0, 10),
    ('15분 5배 ROI15% 익절+대기', '15min', 5, 10, 5, 0, 15),
    ('15분 5배 ROI20% 익절+대기', '15min', 5, 10, 5, 0, 20),
    ('15분 5배 ROI30% 익절+대기', '15min', 5, 10, 5, 0, 30),
    ('15분 5배 ROI40% 익절+대기', '15min', 5, 10, 5, 0, 40),
    ('15분 5배 ROI50% 익절+대기', '15min', 5, 10, 5, 0, 50),
    ('15분 5배 ROI60% 익절+대기', '15min', 5, 10, 5, 0, 60),
    ('15분 5배 거래량1.5배', '15min', 5, 10, 5, 0, 0, 1.5),
    ('15분 5배 거래량2배', '15min', 5, 10, 5, 0, 0, 2.0),
    ('15분 5배 UT5/10', '15min', 5, 5, 10, 0),
    ('15분 5배 UT10/5 +ADX20', '15min', 5, 10, 5, 20),
    ('15분 3배 UT10/5', '15min', 3, 10, 5, 0),
    ('1시간 3배 UT10/5', '1h', 3, 10, 5, 0),
    ('1시간 5배 UT10/5', '1h', 5, 10, 5, 0),
    ('1시간 3배 UT5/10', '1h', 3, 5, 10, 0),
    ('1시간 3배 UT10/5 +ADX20', '1h', 3, 10, 5, 20),
    ('4시간 2배 UT10/5', '4h', 2, 10, 5, 0),
    ('4시간 3배 UT10/5', '4h', 3, 10, 5, 0),
]


def grid_check(data):
    """봉·배율·UT 값·횡보 필터 비교 — 전부 스위칭만 (TP 없음), UT 봉마감 + EMA 실시간, 꼬리로 강제청산 판정"""
    out('━' * 112)
    out('봉 · 배율 · UT 값 · 횡보 필터 비교 — 스위칭만, UT 봉마감 + EMA34/55 실시간, 진입금 BTC 60 / 나머지 50')
    t0 = time.time()
    rows = []
    for gi, (name, bar, lev, key, atr, adx_min, *rest) in enumerate(GRID, 1):
        tp_roi = rest[0] if rest else 0
        vol_mult = rest[1] if len(rest) > 1 else 0
        ts = []
        for c, df in data.items():
            tpp = tp_roi / lev if tp_roi else 1e6                 # ROI% → 가격 %
            p = params(lev, 'ut_confirmed', 'immediate', AMOUNT.get(c, 50), tp_s=tpp, tp_t=tpp, switch='live')
            p.update(ut_sens=float(key), ut_atr=int(atr), mmr_pct=1.0, adx_entry_min=float(adx_min), tp_wait=bool(tp_roi),
                     vol_filter=bool(vol_mult), vol_mult=float(vol_mult or 1.5), vol_ma=20)
            t = bt.run_backtest_live(df, p, bar=bar)[0]
            if len(t):
                ts.append(t.assign(코인=c, 증거금기준=AMOUNT.get(c, 50)))
        print(f"  계산 {gi}/{len(GRID)}: {name} ({time.time() - t0:.0f}초)", flush=True)
        if not ts:
            rows.append((name, None))
            continue
        a = pd.concat(ts, ignore_index=True).sort_values('시각')
        eq = np.r_[0.0, a['순손익'].cumsum().values]
        mon = a.groupby(a['시각'].dt.to_period('M'))['순손익'].sum()
        r = a['순손익'] / a['증거금기준'] * 100
        rows.append((name, dict(net=a['순손익'].sum(), n=len(a), win=(r > 0).mean() * 100, avg_loss=r[r <= 0].mean(),
                                p10=np.percentile(r, 10), mdd=(eq - np.maximum.accumulate(eq)).min(), worst=mon.min(),
                                negm=int((mon < 0).sum()), months=len(mon), liq=int((a['유형'] == '강제청산').sum()),
                                fee=a['수수료'].sum())))
    out(f"{'설정':24s} {'순손익':>9s} {'거래':>6s} {'이긴%':>5s} {'평균손실':>7s} {'큰손실':>7s} {'최대낙폭':>9s} "
        f"{'최악의달':>8s} {'손실달':>7s} {'청산':>5s} {'수수료':>7s}")
    for name, x in rows:
        if x is None:
            out(f"{name:24s} 거래 없음")
            continue
        out(f"{name:24s} {x['net']:+9,.0f} {x['n']:6,d} {x['win']:4.0f}% {x['avg_loss']:+6.1f}% {x['p10']:+6.1f}% "
            f"{x['mdd']:+9,.0f} {x['worst']:+8,.0f} {x['negm']:>3d}/{x['months']:<3d} {x['liq']:5d} {x['fee']:7,.0f}")
    out('  평균손실·큰손실 = 진입금 대비 % (큰손실 = 나쁜 쪽 10% 거래). ADX20 = 매매 봉 ADX 가 20 미만이면 새 진입 쉼 (스위칭 청산은 함)')
    out('  거래량N배 = 직전 마감봉 거래량이 그 앞 20봉 평균의 N배 이상일 때만 새 진입 (스위칭 청산은 함, 거래량 실린 봉에서 진입)')


UT_KEYS = (5, 7, 10, 15, 20)
UT_ATRS = (5, 10, 14)


def ut_search(data):
    """UT 키·ATR 조합 찾기 — 지금 봇 설정(15분봉·5배·스위칭만·UT 봉마감 + EMA 실시간)에서 UT 값만 바꿔 본다.

    과거에 끼워 맞추기 방지: 기간을 반으로 나눠 '앞 기간'에서 좋았던 조합이 '뒤 기간'(고를 때 안 본 기간)에서도
    지금(10/5)보다 나은지 확인한다. 둘 다 나은 조합만 추천.
    """
    out('━' * 112)
    out('★ UT 키·ATR 조합 찾기 — 15분봉 · 5배 · 스위칭만 · UT 봉마감 + EMA34/55 실시간 (UT 값만 바꿈)')
    start = min(df.index[0] for df in data.values())
    end = max(df.index[-1] for df in data.values())
    mid = start + (end - start) / 2
    out(f"   앞 기간 {start:%Y-%m-%d} ~ {mid:%Y-%m-%d} (고르기) · 뒤 기간 {mid:%Y-%m-%d} ~ {end:%Y-%m-%d} (확인)")
    t0 = time.time()
    res = {}
    combos = [(k, a) for k in UT_KEYS for a in UT_ATRS]
    for i, (k, a) in enumerate(combos, 1):
        ts = []
        for c, df in data.items():
            p = params(5, 'ut_confirmed', 'immediate', AMOUNT.get(c, 50), tp_s=1e6, tp_t=1e6, switch='live')
            p.update(ut_sens=float(k), ut_atr=int(a), mmr_pct=1.0)
            t = bt.run_backtest_live(df, p, bar='15min')[0]
            if len(t):
                ts.append(t.assign(증거금기준=AMOUNT.get(c, 50)))
        print(f"  계산 {i}/{len(combos)}: UT {k}/{a} ({time.time() - t0:.0f}초)", flush=True)
        if not ts:
            continue
        al = pd.concat(ts, ignore_index=True).sort_values('시각')
        first, second = al[al['시각'] < mid], al[al['시각'] >= mid]
        eq = np.r_[0.0, al['순손익'].cumsum().values]
        r = al['순손익'] / al['증거금기준'] * 100
        res[(k, a)] = dict(first=first['순손익'].sum(), second=second['순손익'].sum(), net=al['순손익'].sum(), n=len(al),
                           avg_loss=r[r <= 0].mean(), mdd=(eq - np.maximum.accumulate(eq)).min(),
                           fee=al['수수료'].sum(), liq=int((al['유형'] == '강제청산').sum()))
    if not res:
        out('거래 없음')
        return
    base = res.get((10, 5))
    out(f"{'UT 키/ATR':>9s} {'앞 기간':>9s} {'뒤 기간':>9s} {'전체':>9s} {'거래':>6s} {'평균손실':>7s} {'최대낙폭':>9s} "
        f"{'수수료':>7s} {'청산':>4s}   판정")
    good = []
    for (k, a), x in sorted(res.items(), key=lambda kv: -kv[1]['first']):
        tag = ''
        if (k, a) == (10, 5):
            tag = '← 지금'
        elif base and x['first'] > base['first'] and x['second'] > base['second'] and x['second'] > 0:
            tag = '✅ 앞·뒤 모두 지금보다 나음'
            good.append(((k, a), x))
        elif base and x['first'] > base['first']:
            tag = '⚠️ 앞에서만 나음 (뒤에서 못 지킴)'
        out(f"{f'{k}/{a}':>9s} {x['first']:+9,.0f} {x['second']:+9,.0f} {x['net']:+9,.0f} {x['n']:6,d} "
            f"{x['avg_loss']:+6.1f}% {x['mdd']:+9,.0f} {x['fee']:7,.0f} {x['liq']:4d}   {tag}")
    out('━' * 112)
    if good:
        (k, a), x = max(good, key=lambda g: g[1]['second'])
        out(f"추천: UT {k}/{a} — 고를 때 안 본 뒤 기간에서도 지금(10/5)보다 나았음 "
            f"(뒤 기간 {x['second']:+,.0f} vs 지금 {base['second']:+,.0f})")
        out(f"   바꾸려면: 5_gui.py 코인 기본값 'ut_sens': {k}, 'ut_atr': {a} (또는 코인 ⚙️ 설정) — 바꾸기 전에 알려주세요")
    else:
        out('추천 없음: 앞·뒤 기간 모두에서 지금(10/5)보다 나은 조합이 없음 → 지금 값 유지가 맞음')
    out('  ※ 앞 기간 1등이 뒤 기간에서 무너지면 그건 과거에 끼워 맞춘 것 (실전에서도 무너질 가능성 큼)')


def reentry_compare(data):
    """① 재진입 방식 비교"""
    out('━' * 92)
    out('① 재진입·스위칭 방식 비교 (TP 1.2% 횡보 / 1.5% 추세, 지금 = 익절 후 바로 재진입 + 스위칭은 봉 마감 확정 신호로)')
    res = {name: {} for name, *_ in CONFIGS}       # name → coin → trades
    t0 = time.time()
    for i, (c, df) in enumerate(data.items(), 1):
        print(f"  계산 {i}/{len(data)}: {c}", flush=True)
        for name, lev, mode, reentry, switch in CONFIGS:
            t, _ = bt.run_backtest_live(df, params(lev, mode, reentry, AMOUNT.get(c, 50), switch=switch), bar='1h')
            res[name][c] = t
    print(f"  (계산 {time.time() - t0:.0f}초)")

    # ---------- 코인별 ----------
    names = [n for n, *_ in CONFIGS]
    short = ['예전3배', '지금', '다음봉', '스위칭즉시', '3배']
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
        summary[n] = dict(net=all_t['순손익'].sum(), mdd=mdd, monthly=monthly, all=all_t)
        out(f"{n:26s} {all_t['순손익'].sum():+9,.0f} {mdd:+9,.0f} {monthly.min():+9,.0f} "
            f"{(monthly < 0).sum():>3d}/{len(monthly):<2d} {len(all_t):7,d} {(all_t['순손익'] > 0).mean() * 100:4.0f}% "
            f"{all_t['수수료'].sum():7,.0f} {all_t['펀딩비'].sum():6,.0f} {(all_t['유형'] == '강제청산').sum():5d}")

    # ---------- 핵심 비교: 같은 5배·UT마감에서 재진입만 다르게 ----------
    a_n, b_n = '지금: 5배·UT마감·바로재진입', '5배·UT마감·다음봉'
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
    c_n = '5배·바로재진입·스위칭즉시'
    if a_n in summary and c_n in summary:
        out('━' * 92)
        out('스위칭 비교 — 봉 중간에 바로 스위칭 vs 봉 마감 확정 후 스위칭 (나머지는 같음)')
        win = sum(1 for c in data if res[a_n][c]['순손익'].sum() > res[c_n][c]['순손익'].sum())
        n_sw = lambda n: int((summary[n]['all']['유형'] == '스위칭').sum())
        out(f"  봉 마감 스위칭이 더 번 코인: {win}/{len(data)}개")
        out(f"  전체 순손익: 즉시 {summary[c_n]['net']:+,.0f} → 봉 마감 {summary[a_n]['net']:+,.0f}")
        out(f"  스위칭 횟수: 즉시 {n_sw(c_n):,}번 → 봉 마감 {n_sw(a_n):,}번 | 최대 낙폭: {summary[c_n]['mdd']:+,.0f} → {summary[a_n]['mdd']:+,.0f}")


if __name__ == '__main__':
    try:
        code = main()
    except KeyboardInterrupt:
        code = 1
    if os.name == 'nt' and sys.stdin and sys.stdin.isatty():
        input('\n엔터를 누르면 닫힙니다...')
    sys.exit(code)
