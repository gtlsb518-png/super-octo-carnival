#!/usr/bin/env python3
"""
신호 검증기 — "이 신호, 진짜 맞나?"를 숫자로 답하는 모듈

핵심 질문: 어떤 신호가 뜬 날의 '다음날 상승 확률'이
          그냥 아무 날이나의 상승 확률보다 의미 있게 높은가?

정직하게 재기 위해 4가지를 같이 계산합니다.
  1) 기준 상승확률(base rate) 대비 우위       → 절대 적중률에 속지 않기
  2) 유효 표본수(연속 신호는 1건으로 묶음)     → 표본 부풀리기 방지
  3) 전반기/후반기 분리 + 최근 구간 제외(OOS)  → 과최적화 걸러내기
  4) 수수료·세금·슬리피지 차감 후 기대수익     → "맞아도 손해"인 신호 걸러내기

사용법
    python stock_backtest.py                 # 설정 파일의 감시 종목 전부
    python stock_backtest.py 005930 SPY      # 종목 지정
    python stock_backtest.py --csv my.csv    # 직접 받은 CSV로 검증
"""

import sys
import math
import importlib

import pandas as pd
import numpy as np

_config = importlib.import_module('stock_config')
stock_data = importlib.import_module('stock_data')
sig = importlib.import_module('stock_signals')


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _episodes(flags):
    """연속으로 켜진 신호가 몇 '덩어리'인지 (참고용 지표)"""
    arr = np.asarray(flags, dtype=bool)
    if arr.size == 0:
        return 0
    return int(np.sum(arr & ~np.r_[False, arr[:-1]]))


def _perm_pvalue(hits, mask, obs_rate, n_perm=1000, seed=20240101):
    """
    순열검정 — "이 정도 적중률은 아무 날이나 찍어도 나오는가?"

    신호 배열을 통째로 원형 이동(circular shift)시켜 가짜 신호를 1000번 만들고,
    그 가짜들이 실제 적중률 이상을 내는 비율을 p값으로 씁니다.
    신호가 뭉쳐 나오는 성질과 주가의 자기상관을 그대로 보존하기 때문에
    단순 z검정보다 정직하면서 검정력도 살아 있습니다.
    """
    n = len(mask)
    if n < 50 or mask.sum() == 0:
        return 1.0
    rng = np.random.default_rng(seed)
    shifts = rng.integers(10, n - 10, size=n_perm)
    ge = 0
    for s in shifts:
        m = np.roll(mask, s)
        v = hits[m]
        if v.size and v.mean() >= obs_rate:
            ge += 1
    return (1 + ge) / (n_perm + 1)


def round_trip_cost():
    """1일 스윙 왕복 비용 (매수 → 다음날 매도): 수수료 2회 + 거래세 + 슬리피지 2회"""
    return _config.FEE_RATE * 2 + _config.TAX_RATE + _config.SLIPPAGE * 2


def _stats(nxt, mask, direction, with_p=True):
    """
    한 신호의 통계 계산
    nxt   : 다음날 수익률 Series (NaN 제거된 구간)
    mask  : 같은 구간의 신호 boolean Series
    """
    nxt = nxt.dropna()
    mask = mask.reindex(nxt.index).fillna(False).astype(bool)

    vals = nxt.values
    m = mask.values
    # 적중 정의: 상승 신호는 다음날 플러스, 하락 신호는 다음날 마이너스
    hits = (vals > 0) if direction == 'up' else (vals < 0)

    n = int(m.sum())
    if n == 0:
        return None

    base_hit = float(hits.mean()) * 100          # 아무 날이나 맞을 확률
    hit_rate = float(hits[m].mean()) * 100       # 신호 뜬 날 맞을 확률
    edge = hit_rate - base_hit

    sel = vals[m]
    mean_ret = float(sel.mean()) * 100
    median_ret = float(np.median(sel)) * 100

    p_value = _perm_pvalue(hits, m, hits[m].mean()) if with_p else float('nan')

    # 비용 차감 후 기대수익 (상승 신호는 매수, 하락 신호는 '그날 안 사기'라 비용 없음)
    net = mean_ret - round_trip_cost() * 100 if direction == 'up' else -mean_ret

    return {
        'n': n, 'n_eff': _episodes(m),
        'hit_rate': hit_rate, 'base_hit': base_hit, 'edge': edge,
        'mean_ret': mean_ret, 'median_ret': median_ret,
        'net_ret': net, 'p_value': p_value,
    }


def validate(df, alpha=None, verbose=False):
    """
    df(일봉)를 받아 모든 신호를 검증하고 결과 dict를 반환

    반환: {신호명: {...통계..., 'passed': bool, 'reject': '탈락 사유'}}
    """
    d, flags = sig.compute_all(df)
    nxt = d['next_ret']

    valid = nxt.notna()
    n_total = int(valid.sum())
    if n_total < 200:
        raise ValueError(f"데이터가 너무 짧습니다 ({n_total}봉). 최소 200봉 필요")

    # 검증 구간(IS) / 실전 확인 구간(OOS) 분리 — 최근 데이터는 남겨둔다
    split = int(n_total * (1 - _config.OOS_RATIO))
    idx = d.index[valid]
    is_idx = idx[:split]
    oos_idx = idx[split:]

    base_rate = float((nxt.loc[is_idx] > 0).mean()) * 100

    n_tested = len(sig.SIGNALS)
    if alpha is None:
        alpha = _config.MAX_P_VALUE / (n_tested if _config.BONFERRONI else 1)

    results = {}
    for name, (desc, direction, _fn) in sig.SIGNALS.items():
        mask = flags[name]
        st = _stats(nxt.loc[is_idx], mask.loc[is_idx], direction)
        if st is None:
            results[name] = {'desc': desc, 'direction': direction, 'n': 0,
                             'passed': False, 'reject': '신호 발생 0회'}
            continue

        # 전반기 / 후반기 각각 확인 (한쪽 구간에서만 통한 신호 걸러내기)
        half = len(is_idx) // 2
        h1 = _stats(nxt.loc[is_idx[:half]], mask.loc[is_idx[:half]], direction, with_p=False)
        h2 = _stats(nxt.loc[is_idx[half:]], mask.loc[is_idx[half:]], direction, with_p=False)
        st['edge_h1'] = h1['edge'] if h1 else float('nan')
        st['edge_h2'] = h2['edge'] if h2 else float('nan')

        # 최근 구간(OOS) — 검증에 쓰지 않고 참고만
        oos = _stats(nxt.loc[oos_idx], mask.loc[oos_idx], direction, with_p=False)
        st['oos_hit'] = oos['hit_rate'] if oos else float('nan')
        st['oos_n'] = oos['n'] if oos else 0

        # 통과 판정
        reject = None
        if st['n'] < _config.MIN_SAMPLES:
            reject = f"표본 부족 ({st['n']}회 < {_config.MIN_SAMPLES}회)"
        elif st['edge'] < _config.MIN_EDGE_PCT:
            reject = f"우위 부족 ({st['edge']:+.1f}%p < {_config.MIN_EDGE_PCT}%p)"
        elif st['p_value'] > alpha:
            reject = f"우연일 가능성 (p={st['p_value']:.3f} > {alpha:.4f})"
        elif st['net_ret'] <= 0:
            reject = f"비용 차감하면 마이너스 ({st['net_ret']:+.3f}%)"
        elif _config.REQUIRE_BOTH_HALVES and not (st['edge_h1'] > 0 and st['edge_h2'] > 0):
            reject = f"한쪽 기간에서만 통함 (전반 {st['edge_h1']:+.1f}%p / 후반 {st['edge_h2']:+.1f}%p)"

        st.update({'desc': desc, 'direction': direction,
                   'passed': reject is None, 'reject': reject or ''})
        results[name] = st

    results['_meta'] = {
        'base_rate': base_rate, 'n_total': n_total,
        'is_days': len(is_idx), 'oos_days': len(oos_idx),
        'alpha': alpha, 'n_tested': n_tested,
        'cost': round_trip_cost() * 100,
        'first': str(d['date'].iloc[0].date()), 'last': str(d['date'].iloc[-1].date()),
    }
    return results


def print_report(ticker, results):
    """검증 결과를 사람이 읽는 표로 출력"""
    m = results['_meta']
    print()
    print("=" * 92)
    print(f"  {ticker}  검증 리포트   ({m['first']} ~ {m['last']}, 총 {m['n_total']}봉)")
    print("=" * 92)
    print(f"  기준 상승확률(아무 날이나 다음날 오를 확률): {m['base_rate']:.1f}%")
    print(f"  검증 구간 {m['is_days']}봉 / 실전확인(OOS) 구간 {m['oos_days']}봉 (검증에 미사용)")
    bonf = f" (신호 {m['n_tested']}개 본페로니 보정)" if _config.BONFERRONI else ""
    print(f"  유의수준 a={m['alpha']:.4f}{bonf}   1일 왕복비용 {m['cost']:.3f}%")
    print("-" * 92)
    print(f"  {'신호':<16}{'방향':<6}{'발생':>6}{'유효':>6}{'적중률':>8}{'우위':>8}"
          f"{'평균%':>8}{'비용후%':>9}{'p값':>8}  판정")
    print("-" * 92)

    rows = [(k, v) for k, v in results.items() if k != '_meta' and v.get('n', 0) > 0]
    rows.sort(key=lambda kv: kv[1].get('edge', -99), reverse=True)

    for name, st in rows:
        verdict = "✅ 통과" if st['passed'] else f"❌ {st['reject']}"
        arrow = "상승" if st['direction'] == 'up' else "하락"
        print(f"  {name:<16}{arrow:<6}{st['n']:>6}{st['n_eff']:>6}"
              f"{st['hit_rate']:>7.1f}%{st['edge']:>+7.1f}p"
              f"{st['mean_ret']:>+8.2f}{st['net_ret']:>+9.3f}{st['p_value']:>8.3f}  {verdict}")

    passed = [k for k, v in rows if v['passed']]
    print("-" * 92)
    if passed:
        print(f"  통과 신호 {len(passed)}개: {', '.join(passed)}")
        print("  ↓ 최근 구간(검증에 안 쓴 데이터)에서도 유지되는지 확인:")
        for k in passed:
            st = results[k]
            keep = "유지" if st['oos_hit'] > st['base_hit'] else "무너짐"
            print(f"      {k:<16} 검증구간 {st['hit_rate']:.1f}%  →  "
                  f"최근구간 {st['oos_hit']:.1f}% ({st['oos_n']}회) … {keep}")
    else:
        print("  통과 신호 없음 — 이 종목은 종가 신호로 다음날을 예측할 근거가 없습니다.")
        print("  (이게 정상적인 결과입니다. 억지로 기준을 낮추면 헛알람만 늘어납니다.)")
    print("=" * 92)


def main():
    args = [a for a in sys.argv[1:]]
    csv_path = None
    if '--csv' in args:
        i = args.index('--csv')
        csv_path = args[i + 1]
        args = args[:i] + args[i + 2:]

    all_rows = []
    if csv_path:
        df = stock_data.load_csv(csv_path)
        targets = [(csv_path, df)]
    else:
        tickers = args or _config.WATCH_LIST
        targets = []
        for t in tickers:
            try:
                targets.append((t, stock_data.get_daily(t)))
            except Exception as e:
                print(f"[건너뜀] {e}")

    for ticker, df in targets:
        try:
            res = validate(df)
        except Exception as e:
            print(f"[{ticker}] 검증 실패: {e}")
            continue
        print_report(ticker, res)
        for k, v in res.items():
            if k == '_meta' or v.get('n', 0) == 0:
                continue
            all_rows.append({'종목': ticker, '신호': k, '방향': v['direction'],
                             '발생': v['n'], '유효표본': v['n_eff'],
                             '적중률': round(v['hit_rate'], 2), '기준': round(v['base_hit'], 2),
                             '우위': round(v['edge'], 2), '평균수익률': round(v['mean_ret'], 3),
                             '비용후': round(v['net_ret'], 3), 'p값': round(v['p_value'], 4),
                             '통과': v['passed'], '탈락사유': v['reject']})

    if all_rows:
        out = 'stock_signal_report.csv'
        pd.DataFrame(all_rows).to_csv(out, index=False, encoding='utf-8-sig')
        print(f"\n전체 결과 저장: {out}")


if __name__ == '__main__':
    main()
