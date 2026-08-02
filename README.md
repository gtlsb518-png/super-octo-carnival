# 바이낸스 선물 자동매매 봇

UT Bot + EMA 크로스 신호 기반 바이낸스 USDT-M 선물 자동매매 프로그램 (tkinter GUI).

## 실행 방법

```bash
python 9_main.py
```

필요한 라이브러리(pandas, numpy, requests, openpyxl)는 실행 시 자동 설치됩니다.
tkinter가 없는 리눅스에서는 `sudo apt-get install python3-tk`로 설치하세요.

## 설정

`1_config.py`에서 수정:

- `API_KEY` / `API_SECRET`: 바이낸스 API 키 (현재 테스트넷 키)
- `TESTNET`: `True`=테스트넷(데모), `False`=메인넷(실거래)

첫 실행 후에는 `settings.json`이 생성되어 그 값이 우선 적용됩니다.

⚠️ 메인넷 키를 사용할 경우 절대 저장소에 커밋하지 마세요.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `1_config.py` | API 키, 수수료율, 트레이딩 파라미터 |
| `2_api.py` | 바이낸스 선물 API 래퍼 (참조용 — 실제는 5번에 개선판 내장) |
| `3_indicators.py` | UT Bot, EMA, ADX, 거래량 지표 |
| `4_bot.py` | 봇 클래스 스텁 (호환용) |
| `5_gui.py` | **핵심**: GUI + API + 트레이딩 봇 로직 통합 |
| `6~8_gui_*.py` | 호환용 스텁 (미사용) |
| `9_main.py` | 실행 진입점 (라이브러리 자동 설치) |

## 확률(%) 기반 분석기 — `prob_model.py`

봇과 별개로 동작하는 독립 백테스트 도구. 이진 신호 대신 **상태별 승률(%)** 을
집계하고, 그 숫자가 믿을 만한지 검증한 뒤 기댓값이 양수일 때만 진입한다.

```bash
python prob_model.py                    # 기본: 15m/1h/4h/1d 비교 (더블클릭 가능)
python prob_model.py --synthetic        # 버그 검사 — 반드시 먼저 실행
python prob_model.py --sweep            # TP/SL 조합 비교
python prob_model.py --symbol ETHUSDT   # 다른 심볼로 재현 확인
python prob_model.py --csv data.csv     # 바이낸스가 막힌 환경
```

**결과 읽는 순서: `skill` → `커버` → `최고EV`**

| 항목 | 의미 |
|---|---|
| `skill` | 확률모델의 정보량. **0 이하면 예측력 없음** — 아래 숫자는 볼 필요 없음 |
| `커버` | 검증표본 중 학습구간에 사례가 충분했던 비율. 50% 미만이면 신뢰도 낮음 |
| `기준EV` | 필터 없이 전부 진입했을 때 매매당 손익. 우위가 0이면 `-수수료`가 나옴 |
| `최고EV` | 확률 필터를 걸었을 때 최선의 매매당 손익 |

⚠️ **`--synthetic`을 먼저 돌리세요.** 랜덤 데이터인데 우위가 나오면 코드 버그다.
정상이면 skill이 0 이하로 나온다.

⚠️ 판단 기준은 승률이 아니라 **매매당 기대손익**이다. 익절 +a% / 손절 -b%면
랜덤워크에서도 승률 `b/(a+b)`가 나오므로, 승률 60%는 예측력 0을 뜻할 수 있다.

## 매매 로직 요약

- **진입**: UT Bot 상태 + EMA(34/55) 크로스 AND 조건, 코인당 LONG/SHORT 봇 각 1개 (기본 5분봉)
- **익절**: ADX(기간 10) 기반 동적 TP — 추세장(ADX≥21) 1.2% / 횡보장 1.0%
- **손절/스위칭**: 고정 손절 없음 — 보유 중 역신호(UT+EMA) 발생 시 청산 후 즉시 반대 방향 진입 (AUTO 스위칭이 유일한 손절 수단)
- 차트 데이터는 항상 메인넷 시세, 주문은 테스트넷/메인넷 선택
- 거래 기록은 `trade_history.xlsx`(코인별 시트), 통계는 `bot_stats.json`에 저장
