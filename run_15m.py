#!/usr/bin/env python3
"""
15분봉 백테스트 실행기 — 더블클릭용

prob_model.py 를 15분봉 설정으로 돌린다. 같은 폴더에 두고 실행하면 된다.

  python run_15m.py
  python run_15m.py ETHUSDT          # 심볼 지정
  python run_15m.py BTCUSDT 600      # 심볼 + 조회일수

두 단계로 실행된다:

  [1단계] 합성 랜덤워크 자체검증
     예측 가능한 구조가 전혀 없는 가짜 데이터를 넣는다.
     여기서 우위가 나오면 코드에 버그가 있다는 뜻이므로 2단계로 넘어가지 않는다.

  [2단계] 실제 시세 분석
     1단계를 통과했을 때만 실행한다.

1단계를 건너뛸 수 없게 만든 이유: 백테스트가 사람을 속이는 가장 흔한 방식이
'검증 없이 좋아 보이는 숫자'이기 때문이다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import prob_model
except ImportError:
    print("❌ prob_model.py 를 찾을 수 없습니다.")
    print("   이 파일과 prob_model.py 가 같은 폴더에 있어야 합니다.")
    input("\n엔터를 누르면 종료합니다...")
    sys.exit(1)


INTERVAL = '15m'
DEFAULT_SYMBOL = 'BTCUSDT'
DEFAULT_DAYS = 900          # 15분봉 900일 ≈ 86,000봉


def run(argv):
    """prob_model 을 주어진 인자로 실행하고 종료코드를 돌려준다."""
    saved = sys.argv
    try:
        sys.argv = ['prob_model.py'] + argv
        return prob_model.main()
    finally:
        sys.argv = saved


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    symbol = args[0].upper() if len(args) >= 1 else DEFAULT_SYMBOL
    days = int(args[1]) if len(args) >= 2 and args[1].isdigit() else DEFAULT_DAYS

    print("=" * 62)
    print(f"  15분봉 백테스트 — {symbol}")
    print("=" * 62)
    print(f"  조회 기간 : 최근 {days}일")
    print(f"  TP/SL     : ATR 자동 (TP=3×ATR, SL=1.5×ATR, 최소 TP 1.6%)")
    print(f"  방향      : 롱")
    print()

    # ---------- 1단계: 자체검증 ----------
    print("=" * 62)
    print("  [1단계] 자체검증 — 랜덤 데이터로 버그 확인")
    print("=" * 62)
    print("  예측 불가능한 가짜 데이터를 넣습니다.")
    print("  여기서 우위가 나오면 코드 버그입니다.\n")

    run(['--synthetic', '--interval', INTERVAL,
         '--multi-tf', '--timeframes', INTERVAL])

    print("\n" + "=" * 62)
    print("  ⚠️ 위 결과를 먼저 확인하세요")
    print("=" * 62)
    print("  · skill 이 0 이하  → 정상. 계속 진행하세요")
    print("  · skill 이 양수    → 코드 버그. 실데이터 결과를 믿으면 안 됩니다")
    print()

    try:
        if sys.stdin is not None and sys.stdin.isatty():
            ans = input("  실제 시세로 계속할까요? (y/n): ").strip().lower()
            if ans not in ('y', 'yes', ''):
                print("\n  중단했습니다.")
                return 0
    except Exception:
        pass

    # ---------- 2단계: 실데이터 ----------
    print("\n" + "=" * 62)
    print(f"  [2단계] 실제 시세 분석 — {symbol} 15분봉")
    print("=" * 62 + "\n")

    code = run(['--symbol', symbol, '--days', str(days),
                '--multi-tf', '--timeframes', INTERVAL])

    print("\n" + "=" * 62)
    print("  결과 읽는 순서: skill → 커버 → 최고EV")
    print("=" * 62)
    print("  1) skill 이 0 이하면 거기서 끝입니다. 아래 숫자는 의미 없습니다")
    print("  2) 커버가 50% 미만이면 표본 대비 상태를 너무 잘게 쪼갠 것입니다")
    print("  3) 둘 다 통과했을 때만 최고EV 를 보세요")
    print()
    print("  ⚠️ 승률은 보지 마세요. 익절 +a% / 손절 -b% 면 랜덤에서도")
    print("     승률 b/(a+b) 가 나옵니다. 판단 기준은 매매당 기대손익입니다.")
    print()
    print("  ⚠️ 한 번의 결과는 근거가 아닙니다. 다른 심볼로도 확인하세요:")
    print(f"       python run_15m.py ETHUSDT")
    print()
    return code


if __name__ == '__main__':
    try:
        prob_model._pause_exit(main())
    except KeyboardInterrupt:
        print("\n중단됨.")
        prob_model._pause_exit(1)
    except RuntimeError as e:
        print("\n" + "=" * 62)
        print("❌ 실행할 수 없습니다")
        print("=" * 62)
        print(f"\n{e}\n")
        prob_model._pause_exit(1)
