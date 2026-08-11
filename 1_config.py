#!/usr/bin/env python3
"""
설정 파일
API 키, 수수료, 트레이딩 파라미터 등 설정값
"""

# ==================== API 설정 (여기서 수정!) ====================
# ⚠️ 중요: API 키와 TESTNET 값을 반드시 '짝'으로 맞춰야 합니다!
#
#   [테스트넷(모의)로 돌리기]
#     · https://testnet.binancefuture.com 에서 발급받은 키 입력
#     · TESTNET = True
#
#   [메인넷(실거래)로 돌리기]
#     · https://www.binance.com 에서 발급받은 키 입력 (선물 거래 권한 필요)
#     · TESTNET = False        ← 이걸 안 바꾸면 실거래가 안 됩니다!
#
#   ※ 키 종류로 자동 판별하지 않습니다. TESTNET 값이 접속 서버를 결정합니다.
#   ※ 같은 폴더에 settings.json이 있으면 이 파일이 무시되니 삭제하세요.

API_KEY = "GIfUkScBdtzxS9esHcaRe3ybEtyLn2BTslcwR4xhpggaTvhqILpivPefL7fn4Q1v"
API_SECRET = "iRwvo3i9flRN0QaulpMS7CjkX5ginRsRtKmdL9GeEtLIEmRJUtPbu4TPdLHwXqMt"
TESTNET = True  # True=테스트넷(모의), False=메인넷(실거래)
# ==================================================================

# ==================== 수수료율 ====================
FEE_RATE = 0.0004  # 0.04% (바이낸스 선물 테이커 - 추천인 코드 할인 적용)
# 진입 0.04% + 청산 0.04% = 1거래당 0.08%

# ==================== 트레이딩 설정 ====================
STOP_LOSS_PCT = -8.0      # 손절 ROI % (기본: -8%)
SWITCHING_COOLDOWN = 60   # 스위칭 쿨다운 (초)
CHECK_INTERVAL = 3        # 신호 체크 간격 (초) - 포지션 없을 때
CHECK_INTERVAL_FAST = 0.5  # 신호 체크 간격 (초) - 포지션 있을 때 (빠른 익절!)
CLOSE_WAIT_TIME = 5       # 청산 후 대기 시간 (초)
ENTRY_WAIT_TIME = 3       # 진입 후 대기 시간 (초)

# ==================== 시스템 설정 ====================
MAX_LOG_LINES = 100       # 로그 최대 라인 수 (메모리 관리)
MAX_ORDER_RETRY = 3       # 주문 최대 재시도 횟수
CONSECUTIVE_SL_LIMIT = 2  # 연속 손절 제한
