#!/usr/bin/env python3
"""
BTC 1년 백테스트 — 현재 봇 설정 그대로, 한 번에 실행

실행:  python btc_1year.py     (더블클릭도 가능)
   ※ backtest.py가 같은 폴더에 있어야 합니다.

무엇을 하나:
  · 바이낸스 선물 메인넷에서 BTCUSDT 1시간봉 1년치 자동 다운로드
  · 지금 봇에 설정된 값 그대로 시뮬레이션
      진입금 50 USDT × 3배 | UT(10,2) + EMA(34/55)
      익절: ADX(1h) ≥21 → 3.0% / <21 → 2.0% | 손절: 스위칭
  · 승률·순손익·수수료·최대낙폭 + 월별 표 출력
  · 결과를 CSV / 엑셀로 저장

옵션:
  python btc_1year.py --tp-compare    ← TP 값들(1~5%)을 비교해서 최적값 추천
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import backtest as bt
except ImportError:
    print("=" * 60)
    print("❌ backtest.py를 찾을 수 없습니다.")
    print("   btc_1year.py와 backtest.py를 같은 폴더에 두고 실행하세요.")
    print("=" * 60)
    input("\n[엔터]로 종료...")
    sys.exit(1)

# ==================== 지금 봇에 설정된 값 ====================
SETTINGS = dict(
    bt.DEFAULTS,
    strategy='base',        # UT Bot + EMA + 스위칭
    interval='1h',          # 매매 시간봉
    adx_interval='1h',      # ADX 시간봉
    days=365,               # 1년
    amount=50.0,            # 진입금 (USDT)
    leverage=3,             # 레버리지
    fee_pct=0.04,           # 수수료 % (편도)
    ut_sens=10.0, ut_atr=2,
    ema_fast=34, ema_slow=55,
    adx_period=10, adx_th=21,
    tp_mode='adx',
    tp_trend=3.0,           # 추세장 익절 %
    tp_sideways=2.0,        # 횡보장 익절 %
    sl_pct=0.0,             # 고정 손절 없음 (스위칭이 손절 역할)
    hybrid=False,
    vol_filter=False,
)

SYMBOL = 'BTCUSDT'


def main():
    tp_compare = '--tp-compare' in sys.argv

    print("=" * 62)
    print("📊 BTC 1년 백테스트 — 현재 봇 설정")
    print("=" * 62)
    print(f"  코인      : {SYMBOL}")
    print(f"  기간      : 최근 {SETTINGS['days']}일")
    print(f"  매매 시간봉: {SETTINGS['interval']}   |  ADX 시간봉: {SETTINGS['adx_interval']}")
    print(f"  진입금    : {SETTINGS['amount']:.0f} USDT × {SETTINGS['leverage']}배"
          f"  (포지션 {SETTINGS['amount'] * SETTINGS['leverage']:.0f} USDT)")
    print(f"  진입 신호 : UT({SETTINGS['ut_sens']:g},{SETTINGS['ut_atr']}) "
          f"+ EMA({SETTINGS['ema_fast']}/{SETTINGS['ema_slow']}) 동시 충족")
    print(f"  익절      : 추세장 {SETTINGS['tp_trend']}% / 횡보장 {SETTINGS['tp_sideways']}%"
          f"  (ADX {SETTINGS['adx_th']} 기준)")
    print("  손절      : 고정 손절 없음 — 반대신호 시 스위칭")
    print(f"  수수료    : {SETTINGS['fee_pct']}% × 2 (왕복 {SETTINGS['fee_pct'] * 2}%)")
    print("=" * 62)
    print("\n⏳ 바이낸스에서 1년치 데이터를 받는 중입니다 (처음 1회, 1~3분 소요)...\n")

    mode = 'tp' if tp_compare else 'off'
    try:
        summaries = bt.run_all([SYMBOL], dict(SETTINGS), mode, log=print)
    except Exception as e:
        print("\n" + "=" * 62)
        print(f"❌ 실행 중 오류: {e}")
        print("   인터넷 연결 또는 바이낸스 접속을 확인하세요.")
        print("=" * 62)
        import traceback
        traceback.print_exc()
        input("\n[엔터]로 종료...")
        return

    if not summaries:
        print("\n⚠️ 결과가 없습니다. 위 메시지를 확인하세요.")
        input("\n[엔터]로 종료...")
        return

    # ---------- 상세 리포트 ----------
    for s in summaries:
        amount = SETTINGS['amount']
        net = s['순손익']
        print("\n" + "=" * 62)
        print(f"📈 [{s['설정']}] 1년 성적")
        print("=" * 62)
        print(f"  총 거래      : {s['거래수']}회")
        print(f"    · TP 익절  : {s['TP익절']}회")
        print(f"    · 스위칭   : {s['스위칭']}회")
        print(f"    · 강제청산 : {s['강제청산']}회")
        wins = round(s['거래수'] * s['승률%'] / 100)
        print(f"  승 / 패      : {wins} / {s['거래수'] - wins}   → 승률 {s['승률%']}%")
        print(f"  총 수익      : {s['총수익']:+,.2f} USDT (수수료 차감 전)")
        print(f"  총 수수료    : -{s['수수료']:,.2f} USDT")
        print("  ─────────────────────────────────────")
        print(f"  최종 순손익  : {net:+,.2f} USDT")
        print(f"  진입금 대비  : {net / amount * 100:+.1f}%  (원금 {amount:.0f} USDT 기준)")
        print(f"  최대 낙폭    : {s['최대낙폭']:+,.2f} USDT")
        print(f"  최대 단일손실: {s['최대단일손실']:+,.2f} USDT")
        print(f"  보유 중 최저 ROI: {s['최저ROI%']:+.1f}%")

    if tp_compare and len(summaries) > 1:
        best = max(summaries, key=lambda x: x['순손익'])
        print("\n" + "=" * 62)
        print(f"🏆 TP 비교 결과 1위: [{best['설정']}] 순손익 {best['순손익']:+,.2f} USDT")
        print("=" * 62)

    print("\n💾 저장된 파일:")
    wd = bt.get_workdir(log=lambda m: None)
    if wd:
        print(f"   {os.path.join(wd, 'backtest_result.csv')}      (요약)")
        print(f"   {os.path.join(wd, 'backtest_월별집계.xlsx')}   (월별 수익/수수료 표)")
        print(f"   {os.path.join(wd, f'backtest_trades_{SYMBOL}_*.csv')} (전체 거래내역)")

    print("\n💡 참고")
    print("   · 실전은 슬리피지·체결지연 때문에 위 결과보다 나쁘게 나옵니다.")
    print("   · TP 값을 바꿔 비교하려면:  python btc_1year.py --tp-compare")
    input("\n[엔터]로 종료...")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n중단됨.")
    except Exception as e:
        import traceback
        print("\n❌ 오류 발생:")
        traceback.print_exc()
        print(f"\n요약: {e}")
        input("\n[엔터]로 종료...")
