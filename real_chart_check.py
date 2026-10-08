#!/usr/bin/env python3
"""실제 바이낸스 차트로 ① '익절 후 바로 재진입' vs '다음 봉 재진입'  ② TP(익절 %) 조합 비교

  python real_chart_check.py              ← 프로그램 1·2 코인 20개, 최근 2년, ①② 둘 다
  python real_chart_check.py --what tp    ← TP 비교만 (--what reentry 는 재진입 비교만)
  python real_chart_check.py --what risk --days 1460   ← 1시간봉 최대 하락 + 3·5·7·10배 강제청산 비교 (4년)
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
    ap.add_argument('--coins', nargs='*', default=COINS, help='코인 (예: BTC ETH SOL)')
    ap.add_argument('--offline', action='store_true', help='저장된 차트만 사용 (다운로드 안 함)')
    ap.add_argument('--what', choices=['all', 'reentry', 'tp', 'risk'], default='all',
                    help='all = 재진입+TP+위험 / reentry = 재진입 비교만 / tp = TP 비교만 / '
                         'risk = 1시간봉 최대 하락·보유 중 최대 역행 → 3·5·7·10배 비교')
    a = ap.parse_args()
    coins = [c.upper().replace('USDT', '').replace('/', '') for c in a.coins]

    out(f"📊 실제 바이낸스 차트 비교 — 최근 {a.days}일, 코인 {len(coins)}개, 1시간봉 (15분 단위로 따라감)")
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
