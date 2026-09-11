#!/usr/bin/env python3
"""
주식 종가 신호 알림

장 마감 후 종가를 받아, '과거 검증을 통과한 신호'가 오늘 떴는지 확인하고 알립니다.
검증을 통과하지 못한 신호는 오늘 떠도 알리지 않습니다 (--all 로 보기만 가능).

사용법
    python stock_alert.py                # 오늘 1회 검사
    python stock_alert.py --watch        # 매일 정해진 시각에 자동 검사
    python stock_alert.py --all          # 탈락 신호까지 전부 보기 (참고용)
    python stock_alert.py 005930 SPY     # 종목 지정
    python stock_alert.py --report       # 알림 없이 검증 리포트만
"""

import os
import sys
import time
import importlib
from datetime import datetime, timedelta

import requests

_config = importlib.import_module('stock_config')
stock_data = importlib.import_module('stock_data')
sig = importlib.import_module('stock_signals')
bt = importlib.import_module('stock_backtest')

DISCLAIMER = ("이건 예측이 아니라 '과거에 이랬다'는 확률 편향입니다. "
              "적중률이 55%여도 10번 중 4~5번은 틀립니다. 투자 판단·책임은 본인에게 있습니다.")


# ==================== 알림 전송 ====================

def send_telegram(text):
    token, chat_id = _config.TELEGRAM_TOKEN, _config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True},
            timeout=15)
        if r.status_code != 200:
            print(f"  [텔레그램 실패] HTTP {r.status_code} {r.text[:120]}")
            return False
        return True
    except Exception as e:
        print(f"  [텔레그램 실패] {e}")
        return False


def notify(text):
    """설정된 모든 채널로 알림 전송"""
    if _config.ALERT_CONSOLE:
        print(text)
    if _config.ALERT_FILE:
        try:
            with open(_config.ALERT_FILE, 'a', encoding='utf-8') as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
        except Exception as e:
            print(f"  [파일 기록 실패] {e}")
    send_telegram(text)


# ==================== 검사 ====================

def check_ticker(ticker, show_all=False):
    """
    한 종목 검사 → (알림 텍스트 리스트, 참고 정보 리스트)
    """
    # 종목코드 대신 CSV 경로를 줘도 됩니다 (야후가 막힌 망에서 유용)
    if ticker.lower().endswith('.csv'):
        df = stock_data.load_csv(ticker)
        ticker = os.path.basename(ticker)[:-4]
    else:
        df = stock_data.get_daily(ticker, quiet=True)
    results = bt.validate(df)
    d, flags = sig.compute_all(df)

    last = d.iloc[-1]
    bar_date = last['date'].date()
    price = last['close']
    day_ret = (last['ret'] or 0) * 100

    # 진행 중인 봉으로 신호를 잡으면 종가가 바뀌어 신호가 사라질 수 있습니다
    warn = ""
    if bar_date == datetime.now().date() and datetime.now().strftime('%H:%M') < _config.CHECK_TIME:
        warn = "  ⚠️ 아직 장 중일 수 있습니다 — 종가가 확정되면 신호가 바뀔 수 있습니다\n"
    elif bar_date < (datetime.now().date() - timedelta(days=5)):
        warn = f"  ⚠️ 데이터가 오래됐습니다 (마지막 봉 {bar_date}) — 휴장이거나 수집 실패일 수 있습니다\n"

    alerts, info = [], []
    for name, (desc, direction, _fn) in sig.SIGNALS.items():
        if not bool(flags[name].iloc[-1]):
            continue
        st = results.get(name, {})
        if not st or st.get('n', 0) == 0:
            continue

        head = (f"{'🔔' if st.get('passed') else '·'} [{ticker}] {bar_date} "
                f"종가 {price:,.0f} ({day_ret:+.2f}%)")
        arrow = "다음날 상승 기대" if direction == 'up' else "다음날 하락 주의"
        body = (f"{head}\n"
                f"{warn}"
                f"  신호: {name} — {desc} ({arrow})\n"
                f"  과거 성적: 적중률 {st['hit_rate']:.1f}% "
                f"(아무 날이나 {st['base_hit']:.1f}%, 우위 {st['edge']:+.1f}%p, "
                f"{st['n']}회, p={st['p_value']:.3f})\n"
                f"  비용 차감 기대수익: {st['net_ret']:+.3f}%/일\n"
                f"  검증 외 최근 구간: {st['oos_hit']:.1f}% ({st['oos_n']}회)")

        if st.get('passed'):
            alerts.append(body + f"\n  ※ {DISCLAIMER}")
        elif show_all:
            info.append(body + f"\n  ❌ 알림 제외: {st['reject']}")

    return alerts, info, results, ticker


def run_once(tickers, show_all=False, report=False):
    print(f"\n{'='*70}\n  종가 신호 검사  {datetime.now():%Y-%m-%d %H:%M}\n{'='*70}")
    total_alerts = 0

    for ticker in tickers:
        try:
            alerts, info, results, ticker = check_ticker(ticker, show_all=show_all)
        except Exception as e:
            print(f"\n[{ticker}] 검사 실패: {e}")
            continue

        if report:
            bt.print_report(ticker, results)
            continue

        passed = [k for k, v in results.items() if k != '_meta' and v.get('passed')]
        if not passed:
            print(f"\n[{ticker}] 검증 통과 신호 자체가 없음 → 알릴 게 없습니다 "
                  f"(이게 대부분의 경우입니다)")
            continue

        print(f"\n[{ticker}] 검증 통과 신호: {', '.join(passed)}")
        if alerts:
            for a in alerts:
                notify("\n" + a)
                total_alerts += 1
        else:
            print(f"  오늘은 해당 신호 없음")

        for i in info:
            print("\n" + i)

    if not report:
        print(f"\n{'='*70}\n  알림 {total_alerts}건\n{'='*70}")
    return total_alerts


def run_watch(tickers, show_all=False):
    """매일 CHECK_TIME에 1회 검사하는 상주 모드"""
    print(f"감시 시작 — 매일 {_config.CHECK_TIME}에 검사합니다. (Ctrl+C 종료)")
    last_run = None
    while True:
        try:
            now = datetime.now()
            if now.strftime('%H:%M') >= _config.CHECK_TIME and last_run != now.date():
                if now.weekday() < 5:          # 주말 건너뛰기
                    run_once(tickers, show_all=show_all)
                last_run = now.date()
            time.sleep(30)
        except KeyboardInterrupt:
            print("\n감시 종료")
            return
        except Exception as e:
            print(f"[감시 오류] {e}")
            time.sleep(60)


def main():
    args = sys.argv[1:]
    watch = '--watch' in args
    show_all = '--all' in args
    report = '--report' in args
    tickers = [a for a in args if not a.startswith('--')] or _config.WATCH_LIST

    print(f"대상 종목: {', '.join(tickers)}")
    if watch:
        run_watch(tickers, show_all=show_all)
    else:
        run_once(tickers, show_all=show_all, report=report)


if __name__ == '__main__':
    main()
