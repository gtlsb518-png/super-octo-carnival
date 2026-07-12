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

## 매매 로직 요약

- **진입**: UT Bot 상태 + EMA(34/55) 크로스 AND 조건, 코인당 LONG/SHORT 봇 각 1개 (기본 5분봉)
- **익절**: ADX(기간 10) 기반 동적 TP — 추세장(ADX≥21) 1.2% / 횡보장 1.0%
- **손절/스위칭**: 고정 손절 없음 — 보유 중 역신호(UT+EMA) 발생 시 청산 후 즉시 반대 방향 진입 (AUTO 스위칭이 유일한 손절 수단)
- 차트 데이터는 항상 메인넷 시세, 주문은 테스트넷/메인넷 선택
- 거래 기록은 `trade_history.xlsx`(코인별 시트), 통계는 `bot_stats.json`에 저장

---

# 도매꾹 제품 자동 크롤러 (별도 프로그램)

`domeggook_crawler.py` — 도매꾹(domeggook.com) 상품 검색 결과를 자동 수집하는 독립 실행형 프로그램.

## 실행 방법

```bash
python domeggook_crawler.py              # GUI 실행
python domeggook_crawler.py 양말 -p 3    # CLI: '양말' 1~3페이지 수집 후 엑셀 저장
python domeggook_crawler.py 양말 -i 30   # 30분마다 자동 반복 수집
```

## 기능

- 키워드별 상품 수집: 상품번호 / 상품명 / 가격 / 최소구매수량 / URL / 이미지
- 자동(주기) 크롤링: N분마다 재수집하여 **신규 상품 · 가격 변동** 자동 감지
- 결과는 `도매꾹_수집결과_날짜.xlsx`로 저장, 수집 이력은 `dmg_history.json`에 누적
- 수집 방식: 웹 크롤링(기본) 또는 [도매꾹 오픈API](https://openapi.domeggook.com) 키 입력 시 API 모드(권장, 더 안정적)
- 요청 간 딜레이 기본 2초 — 서버에 부담을 주지 않도록 과도한 페이지 수/짧은 주기 사용은 피하세요
