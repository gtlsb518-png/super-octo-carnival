#!/usr/bin/env python3
"""
바이낸스 선물 자동매매 봇 v2 — 국면 라우터 전략 (완전 독립 실행형)

🔥 이 파일 하나로 실행됩니다. API 키는 1_config.py에서 읽습니다 (없으면 GUI에서 입력).
   필요 라이브러리(pandas/numpy/requests)는 실행 시 자동 설치됩니다.

실행:
  python bot_v2.py

전략 (backtest_v2.py의 combo와 동일 로직):
- ADX(14) ≥ 25 (추세장) → 돈치안 20봉 돌파 + EMA200 필터로 진입,
  ATR 2.5배 손절 + 샹들리에 트레일링 (수익은 추세 끝까지)
- ADX(14) < 20 (횡보장) → 볼린저(20,2σ) 이탈 + RSI(14) 과매도/과매수로 역추세 진입,
  ATR 2.0배 손절 + 중심선 복귀 익절 + 최대 보유봉 초과 시 청산
- ADX 20~25 → 판단 유보, 신규 진입 없음

기존 봇(5_gui.py)과의 핵심 차이:
1. 손절이 거래소 주문(STOP_MARKET)으로 실제로 걸림 — 봇/PC가 죽어도 손절은 작동
2. 포지션 크기 = 자본 × 리스크% ÷ 손절거리 — 1회 손실이 자본의 리스크%로 고정
3. 일일 손실 한도 서킷브레이커 — 하루 손실이 한도 초과 시 당일 신규 진입 중단
4. 1시간봉 기본 — 거래 빈도를 낮춰 수수료 부담 최소화
5. 상태를 bot_v2_state.json에 저장 — 재시작해도 보유 포지션 이어서 관리

⚠️ 반드시 테스트넷(1_config.py의 TESTNET=True)에서 충분히 검증 후 실거래 하세요.
⚠️ 메인넷 키는 출금 권한 없는 키 + IP 화이트리스트로 만드세요.
"""

import hashlib
import hmac
import importlib
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

# ==================== 라이브러리 자동 설치 ====================
for _mod in ['pandas', 'numpy', 'requests']:
    try:
        __import__(_mod)
    except ImportError:
        print(f"📦 {_mod} 설치 중...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', _mod],
                              stdout=subprocess.DEVNULL)

import numpy as np
import pandas as pd
import requests

# ==================== 설정 ====================
try:
    _config = importlib.import_module('1_config')
    API_KEY = _config.API_KEY
    API_SECRET = _config.API_SECRET
    TESTNET = _config.TESTNET
except Exception:
    API_KEY, API_SECRET, TESTNET = '', '', True

SETTINGS_FILE = 'bot_v2_settings.json'
STATE_FILE = 'bot_v2_state.json'
TRADES_FILE = 'bot_v2_trades.csv'

DEFAULTS = {
    'symbols': 'BTCUSDT,ETHUSDT,SOLUSDT',
    'interval': '1h',
    'risk_pct': 0.7,        # 1회 거래 리스크 (자본의 %)
    'max_lev': 5,           # 레버리지 상한
    'max_positions': 3,     # 동시 보유 포지션 수 상한
    'daily_loss_limit': 3.0,  # 일일 손실 한도 % (초과 시 당일 신규진입 중단)
    # 국면 판정
    'adx_period': 14,
    'adx_trend': 25,        # 이 이상 = 추세장
    'adx_side': 20,         # 이 미만 = 횡보장
    # 추세추종
    'dc_entry': 20,
    'atr_period': 14,
    'atr_mult': 2.5,
    'ema_filter': 200,
    # 역추세
    'bb_len': 20,
    'bb_std': 2.0,
    'rsi_len': 14,
    'rsi_os': 30,
    'rsi_ob': 70,
    'mr_atr_mult': 2.0,
    'mr_max_bars': 48,
    # 루프
    'check_sec': 20,        # 체크 주기 (초) — 1h봉이라 빠를 필요 없음
}

INTERVALS = ['15m', '30m', '1h', '2h', '4h']
INTERVAL_MS = {'15m': 900_000, '30m': 1_800_000, '1h': 3_600_000,
               '2h': 7_200_000, '4h': 14_400_000}


# ==================== 바이낸스 API ====================
class BinanceAPI:
    """2_api.py 기반 + 정밀도(exchangeInfo)/스탑주문/주문취소 추가"""

    def __init__(self, api_key, api_secret, testnet=True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = ("https://testnet.binancefuture.com" if testnet
                         else "https://fapi.binance.com")
        self.chart_url = "https://fapi.binance.com"  # 차트는 항상 메인넷
        self.session = requests.Session()
        self.session.headers.update({'X-MBX-APIKEY': self.api_key})
        self.time_offset = 0
        self._filters = {}
        self._sync_time()

    def _sync_time(self):
        try:
            r = self.session.get(f"{self.base_url}/fapi/v1/time", timeout=10)
            self.time_offset = r.json()['serverTime'] - int(time.time() * 1000)
        except Exception:
            self.time_offset = 0

    def _sign(self, params):
        qs = urlencode(params)
        params['signature'] = hmac.new(self.api_secret.encode(), qs.encode(),
                                       hashlib.sha256).hexdigest()
        return params

    def _request(self, method, endpoint, params=None, signed=False):
        url = f"{self.base_url}{endpoint}"
        params = dict(params or {})
        if signed:
            params['timestamp'] = int(time.time() * 1000) + self.time_offset
            params = self._sign(params)
        try:
            if method == 'GET':
                r = self.session.get(url, params=params, timeout=10)
            elif method == 'DELETE':
                r = self.session.delete(url, params=params, timeout=10)
            else:
                r = self.session.post(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            try:
                err = e.response.json()
                if err.get('code') == -1021:
                    self._sync_time()
                return {'_error': err}
            except Exception:
                return {'_error': {'msg': str(e)}}
        except Exception as e:
            return {'_error': {'msg': str(e)}}

    @staticmethod
    def _ok(res):
        return res is not None and '_error' not in res

    # ---- 시세 ----
    def get_klines(self, symbol, interval, limit=400):
        try:
            r = self.session.get(f"{self.chart_url}/fapi/v1/klines",
                                 params={'symbol': symbol, 'interval': interval,
                                         'limit': limit}, timeout=10)
            r.raise_for_status()
            df = pd.DataFrame(r.json(), columns=[
                'ts', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'qv', 'n', 'tb', 'tq', 'ig'])
            df = df[['ts', 'open', 'high', 'low', 'close', 'volume', 'close_time']]
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            df['ts'] = pd.to_datetime(df['ts'].astype(np.int64), unit='ms')
            return df.set_index('ts')
        except Exception:
            return None

    def get_filters(self, symbol):
        """수량 stepSize / 가격 tickSize (exchangeInfo, 캐시)"""
        if symbol in self._filters:
            return self._filters[symbol]
        try:
            r = self.session.get(f"{self.chart_url}/fapi/v1/exchangeInfo", timeout=15)
            r.raise_for_status()
            for s in r.json()['symbols']:
                step = tick = min_qty = None
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        min_qty = float(f['minQty'])
                    elif f['filterType'] == 'PRICE_FILTER':
                        tick = float(f['tickSize'])
                self._filters[s['symbol']] = {'step': step or 0.001,
                                              'tick': tick or 0.01,
                                              'min_qty': min_qty or 0.001}
        except Exception:
            pass
        return self._filters.get(symbol, {'step': 0.001, 'tick': 0.01,
                                          'min_qty': 0.001})

    def round_qty(self, symbol, qty):
        step = self.get_filters(symbol)['step']
        return math.floor(qty / step) * step if step > 0 else qty

    def round_price(self, symbol, price):
        tick = self.get_filters(symbol)['tick']
        return round(round(price / tick) * tick, 10) if tick > 0 else price

    # ---- 계좌/포지션 ----
    def get_balance(self):
        res = self._request('GET', '/fapi/v2/account', signed=True)
        return float(res.get('totalWalletBalance', 0)) if self._ok(res) else None

    def get_position(self, symbol):
        res = self._request('GET', '/fapi/v2/positionRisk',
                            params={'symbol': symbol}, signed=True)
        if not self._ok(res):
            return None
        for pos in res:
            amt = float(pos['positionAmt'])
            if pos['symbol'] == symbol and amt != 0:
                return {'side': 'LONG' if amt > 0 else 'SHORT',
                        'qty': abs(amt),
                        'entry': float(pos['entryPrice']),
                        'pnl': float(pos['unRealizedProfit'])}
        return {}  # 포지션 없음 (조회 성공)

    def set_leverage(self, symbol, lev):
        self._request('POST', '/fapi/v1/leverage',
                      params={'symbol': symbol, 'leverage': lev}, signed=True)

    def set_isolated(self, symbol):
        self._request('POST', '/fapi/v1/marginType',
                      params={'symbol': symbol, 'marginType': 'ISOLATED'},
                      signed=True)

    # ---- 주문 ----
    def market_order(self, symbol, side, qty, reduce_only=False):
        params = {'symbol': symbol, 'side': side, 'type': 'MARKET',
                  'quantity': qty}
        if reduce_only:
            params['reduceOnly'] = 'true'
        for attempt in range(3):
            res = self._request('POST', '/fapi/v1/order', params=params, signed=True)
            if self._ok(res):
                return res
            time.sleep(2 * (attempt + 1))
        return res

    def stop_market_close(self, symbol, side, stop_price):
        """포지션 전체 청산 STOP_MARKET (closePosition) — 거래소에 실제로 걸리는 손절"""
        params = {'symbol': symbol, 'side': side, 'type': 'STOP_MARKET',
                  'stopPrice': self.round_price(symbol, stop_price),
                  'closePosition': 'true', 'workingType': 'MARK_PRICE'}
        res = self._request('POST', '/fapi/v1/order', params=params, signed=True)
        return res if self._ok(res) else None

    def cancel_order(self, symbol, order_id):
        self._request('DELETE', '/fapi/v1/order',
                      params={'symbol': symbol, 'orderId': order_id}, signed=True)

    def cancel_all(self, symbol):
        self._request('DELETE', '/fapi/v1/allOpenOrders',
                      params={'symbol': symbol}, signed=True)

    def last_price(self, symbol):
        try:
            r = self.session.get(f"{self.chart_url}/fapi/v1/ticker/price",
                                 params={'symbol': symbol}, timeout=10)
            return float(r.json()['price'])
        except Exception:
            return None


# ==================== 지표 (backtest_v2.py와 동일 공식) ====================
def atr_rma(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx_series(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    up = high - high.shift(1)
    down = low.shift(1) - low
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df.index)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(20.0)


def rsi_series(df, period):
    delta = df['close'].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def compute_signals(df, p):
    """완성봉 기준 신호 계산. 마지막 행(진행 중인 봉)은 제외하고 호출할 것.

    반환: dict — 마지막 완성봉의 신호/지표 스냅샷
    """
    c = df['close']
    atr = atr_rma(df, p['atr_period'])
    adx = adx_series(df, p['adx_period'])

    # 추세추종 신호
    dc_hi = df['high'].rolling(p['dc_entry']).max().shift(1)
    dc_lo = df['low'].rolling(p['dc_entry']).min().shift(1)
    ema_f = c.ewm(span=int(p['ema_filter']), adjust=False).mean() \
        if p['ema_filter'] > 0 else None
    t_long = (c.iloc[-1] > dc_hi.iloc[-1]) and \
        (ema_f is None or c.iloc[-1] > ema_f.iloc[-1])
    t_short = (c.iloc[-1] < dc_lo.iloc[-1]) and \
        (ema_f is None or c.iloc[-1] < ema_f.iloc[-1])

    # 역추세 신호
    mid = c.rolling(p['bb_len']).mean()
    sd = c.rolling(p['bb_len']).std()
    rsi = rsi_series(df, p['rsi_len'])
    adx_now = float(adx.iloc[-1])
    m_long = (c.iloc[-1] < (mid - p['bb_std'] * sd).iloc[-1]) and \
        (rsi.iloc[-1] < p['rsi_os']) and (adx_now < p['adx_side'])
    m_short = (c.iloc[-1] > (mid + p['bb_std'] * sd).iloc[-1]) and \
        (rsi.iloc[-1] > p['rsi_ob']) and (adx_now < p['adx_side'])

    # 국면 라우팅
    if adx_now >= p['adx_trend']:
        regime, long_sig, short_sig, kind = '추세', t_long, t_short, 'T'
    elif adx_now < p['adx_side']:
        regime, long_sig, short_sig, kind = '횡보', m_long, m_short, 'M'
    else:
        regime, long_sig, short_sig, kind = '유보', False, False, '-'

    return {
        'long': bool(long_sig), 'short': bool(short_sig), 'kind': kind,
        'regime': regime, 'adx': round(adx_now, 1),
        'atr': float(atr.iloc[-1]), 'mid': float(mid.iloc[-1]),
        'close': float(c.iloc[-1]), 'bar_time': str(df.index[-1]),
        # 반대 돌파 (추세 포지션의 스위칭 판정용)
        't_long': bool(t_long), 't_short': bool(t_short),
    }


# ==================== 봇 엔진 ====================
class TradingBot:
    def __init__(self, api, params, log=print, on_status=None):
        self.api = api
        self.p = params
        self.log = log
        self.on_status = on_status or (lambda *_: None)
        self.running = False
        self.state = self._load_state()   # symbol → 포지션 관리 상태
        self.day_key = None
        self.day_start_equity = None
        self.halted = False               # 서킷브레이커 발동 여부

    # ---- 상태 저장/복원 ----
    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _record_trade(self, row):
        header = not os.path.exists(TRADES_FILE)
        pd.DataFrame([row]).to_csv(TRADES_FILE, mode='a', header=header,
                                   index=False, encoding='utf-8-sig')

    # ---- 서킷브레이커 ----
    def _check_circuit_breaker(self):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        equity = self.api.get_balance()
        if equity is None:
            return
        if self.day_key != today:
            self.day_key = today
            self.day_start_equity = equity
            if self.halted:
                self.log("🔓 날짜 변경 — 서킷브레이커 해제, 거래 재개")
            self.halted = False
            return
        if self.day_start_equity and not self.halted:
            dd = (equity - self.day_start_equity) / self.day_start_equity * 100
            if dd <= -self.p['daily_loss_limit']:
                self.halted = True
                self.log(f"🚨 서킷브레이커 발동! 오늘 손실 {dd:.1f}% "
                         f"(한도 -{self.p['daily_loss_limit']}%) — 당일 신규 진입 중단")

    # ---- 진입 ----
    def _try_enter(self, symbol, sig):
        if self.halted:
            return
        open_count = sum(1 for s in self.state.values() if s.get('active'))
        if open_count >= self.p['max_positions']:
            return
        side = 'LONG' if sig['long'] else ('SHORT' if sig['short'] else None)
        if not side:
            return

        equity = self.api.get_balance()
        price = self.api.last_price(symbol)
        if not equity or not price:
            return

        am = self.p['atr_mult'] if sig['kind'] == 'T' else self.p['mr_atr_mult']
        dist = am * sig['atr']
        if dist <= 0:
            return
        stop_price = price - dist if side == 'LONG' else price + dist

        qty = equity * self.p['risk_pct'] / 100.0 / dist
        cap = equity * self.p['max_lev'] / self.p['max_positions'] / price
        qty = self.api.round_qty(symbol, min(qty, cap))
        if qty < self.api.get_filters(symbol)['min_qty'] or qty * price < 5:
            self.log(f"⚠️ {symbol} 수량 미달로 진입 생략 (qty={qty})")
            return

        self.api.set_isolated(symbol)
        self.api.set_leverage(symbol, self.p['max_lev'])
        res = self.api.market_order(symbol, 'BUY' if side == 'LONG' else 'SELL', qty)
        if not BinanceAPI._ok(res):
            self.log(f"❌ {symbol} 진입 주문 실패: {res.get('_error')}")
            return

        # 실제 체결가 기준으로 손절가 재계산
        pos = self.api.get_position(symbol)
        entry = pos['entry'] if pos else price
        stop_price = entry - dist if side == 'LONG' else entry + dist
        stop_side = 'SELL' if side == 'LONG' else 'BUY'
        stop_order = self.api.stop_market_close(symbol, stop_side, stop_price)
        if stop_order is None:
            # 손절 주문 실패 → 즉시 포지션 철회 (손절 없는 포지션은 절대 보유 금지)
            self.log(f"❌ {symbol} 손절 주문 실패 → 포지션 즉시 청산!")
            self.api.market_order(symbol, stop_side, qty, reduce_only=True)
            return

        self.state[symbol] = {
            'active': True, 'side': side, 'kind': sig['kind'],
            'entry': entry, 'qty': qty, 'stop': stop_price,
            'stop_order_id': stop_order.get('orderId'),
            'extreme': entry, 'bars_held': 0,
            'entry_bar': sig['bar_time'], 'entry_time': str(datetime.now()),
        }
        self._save_state()
        label = '추세돌파' if sig['kind'] == 'T' else '역추세'
        self.log(f"🚀 {symbol} {side} 진입 [{label}] 수량 {qty} @ {entry:.6g} | "
                 f"손절 {stop_price:.6g} (리스크 {self.p['risk_pct']}%) | "
                 f"ADX {sig['adx']} ({sig['regime']})")

    # ---- 보유 관리 (새 완성봉마다 1회) ----
    def _manage(self, symbol, sig, df):
        st = self.state.get(symbol)
        if not st or not st.get('active'):
            return

        pos = self.api.get_position(symbol)
        if pos is None:
            return  # 조회 실패 — 다음 루프에서 재시도
        if pos == {}:
            # 포지션 없음 → 거래소 손절이 체결됐거나 수동 청산됨
            self.api.cancel_all(symbol)
            st['active'] = False
            self._save_state()
            self._record_trade({'시각': str(datetime.now()), '심볼': symbol,
                                '방향': st['side'], '유형': '손절/외부청산',
                                '진입가': st['entry'], '신호': st['kind']})
            self.log(f"🛑 {symbol} 포지션 종료 감지 (손절 체결 또는 수동 청산)")
            return

        st['bars_held'] += 1
        side = st['side']
        last = df.iloc[-1]  # 마지막 완성봉

        exit_reason = None
        if st['kind'] == 'T':
            # 반대 돌파 = 추세 종료 → 청산
            if (side == 'LONG' and sig['t_short']) or \
               (side == 'SHORT' and sig['t_long']):
                exit_reason = '반대돌파'
            else:
                # 샹들리에 트레일링: 극값 갱신 → 스탑 상향/하향
                if side == 'LONG':
                    st['extreme'] = max(st['extreme'], float(last['high']))
                    new_stop = st['extreme'] - self.p['atr_mult'] * sig['atr']
                    improve = new_stop > st['stop']
                else:
                    st['extreme'] = min(st['extreme'], float(last['low']))
                    new_stop = st['extreme'] + self.p['atr_mult'] * sig['atr']
                    improve = new_stop < st['stop']
                if improve:
                    self._replace_stop(symbol, st, new_stop)
        else:  # 역추세 (M)
            if side == 'LONG' and sig['close'] >= sig['mid']:
                exit_reason = '중심선익절'
            elif side == 'SHORT' and sig['close'] <= sig['mid']:
                exit_reason = '중심선익절'
            elif st['bars_held'] >= self.p['mr_max_bars']:
                exit_reason = '시간청산'

        if exit_reason:
            self._close(symbol, st, exit_reason)
        else:
            self._save_state()

    def _replace_stop(self, symbol, st, new_stop):
        """새 스탑 주문 먼저 걸고 옛 주문 취소 (스탑 공백 방지)"""
        stop_side = 'SELL' if st['side'] == 'LONG' else 'BUY'
        new_order = self.api.stop_market_close(symbol, stop_side, new_stop)
        if new_order is None:
            self.log(f"⚠️ {symbol} 트레일링 스탑 갱신 실패 (기존 스탑 유지)")
            return
        if st.get('stop_order_id'):
            self.api.cancel_order(symbol, st['stop_order_id'])
        st['stop'] = self.api.round_price(symbol, new_stop)
        st['stop_order_id'] = new_order.get('orderId')
        self._save_state()
        self.log(f"📈 {symbol} 트레일링 스탑 → {st['stop']:.6g}")

    def _close(self, symbol, st, reason):
        stop_side = 'SELL' if st['side'] == 'LONG' else 'BUY'
        res = self.api.market_order(symbol, stop_side, st['qty'], reduce_only=True)
        if not BinanceAPI._ok(res):
            self.log(f"❌ {symbol} 청산 주문 실패 — 다음 봉에서 재시도: {res.get('_error')}")
            return
        self.api.cancel_all(symbol)
        exit_price = self.api.last_price(symbol) or 0
        st['active'] = False
        self._save_state()
        self._record_trade({'시각': str(datetime.now()), '심볼': symbol,
                            '방향': st['side'], '유형': reason,
                            '진입가': st['entry'], '청산가': exit_price,
                            '보유봉': st['bars_held'], '신호': st['kind']})
        self.log(f"✅ {symbol} {st['side']} 청산 [{reason}] @ {exit_price:.6g} "
                 f"(진입 {st['entry']:.6g}, {st['bars_held']}봉 보유)")

    # ---- 메인 루프 ----
    def run(self):
        self.running = True
        symbols = [s.strip().upper() for s in self.p['symbols'].split(',') if s.strip()]
        interval = self.p['interval']
        last_bar = {}  # symbol → 마지막으로 처리한 완성봉 시각

        mode = "테스트넷 (데모)" if self.api.testnet else "⚠️ 메인넷 (실거래!)"
        self.log(f"▶️ 봇 시작 — {mode} | {interval}봉 | {', '.join(symbols)}")
        self.log(f"   리스크 {self.p['risk_pct']}%/회 | 레버리지캡 {self.p['max_lev']}x | "
                 f"일일한도 -{self.p['daily_loss_limit']}%")

        while self.running:
            try:
                self._check_circuit_breaker()
                for symbol in symbols:
                    if not self.running:
                        break
                    df = self.api.get_klines(symbol, interval, limit=400)
                    if df is None or len(df) < 250:
                        continue
                    # 진행 중인 봉 제거 → 완성봉만 사용 (선견편향 방지)
                    now_ms = int(time.time() * 1000)
                    if int(df['close_time'].iloc[-1]) > now_ms:
                        df = df.iloc[:-1]
                    df = df.drop(columns=['close_time'])
                    bar_time = str(df.index[-1])
                    if last_bar.get(symbol) == bar_time:
                        continue  # 새 완성봉 없음

                    sig = compute_signals(df, self.p)
                    st = self.state.get(symbol)
                    if st and st.get('active'):
                        self._manage(symbol, sig, df)
                    else:
                        self._try_enter(symbol, sig)
                    last_bar[symbol] = bar_time
                    self.on_status(symbol, sig, self.state.get(symbol))
            except Exception as e:
                self.log(f"❌ 루프 오류: {e}")
            for _ in range(int(self.p['check_sec'])):
                if not self.running:
                    break
                time.sleep(1)
        self.log("⏹️ 봇 정지")

    def stop(self):
        self.running = False


# ==================== GUI ====================
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    BG, PANEL, FG, ACCENT = '#1e1e1e', '#2d2d2d', '#ffffff', '#00ff88'

    saved = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding='utf-8') as f:
                saved = json.load(f)
        except Exception:
            pass

    root = tk.Tk()
    root.title("🤖 자동매매 봇 v2 — 국면 라우터 (돈치안 추세 + BB/RSI 역추세)")
    root.geometry("1150x720")
    root.configure(bg=BG)

    log_q = queue.Queue()
    bot_holder = [None]

    # ---------- 좌측: 설정 ----------
    left = tk.Frame(root, bg=PANEL)
    left.pack(side='left', fill='y', padx=8, pady=8)

    mode_txt = "🟢 테스트넷 (데모)" if TESTNET else "🔴 메인넷 (실거래!)"
    tk.Label(left, text=mode_txt, bg=PANEL,
             fg='#00ff88' if TESTNET else '#ff4444',
             font=('Arial', 12, 'bold')).pack(pady=(10, 2))
    tk.Label(left, text="(1_config.py의 TESTNET에서 변경)", bg=PANEL, fg='#888888',
             font=('Arial', 8)).pack()

    tk.Label(left, text="⚙️ 설정", bg=PANEL, fg=ACCENT,
             font=('Arial', 12, 'bold')).pack(pady=(8, 4))

    form = tk.Frame(left, bg=PANEL)
    form.pack(padx=10)
    vars_ = {}
    fields = [
        ('코인 (쉼표 구분)', 'symbols', 18),
        ('리스크 %/회', 'risk_pct', 8), ('레버리지 상한', 'max_lev', 8),
        ('동시 포지션 수', 'max_positions', 8), ('일일 손실한도 %', 'daily_loss_limit', 8),
        ('ADX 추세 기준', 'adx_trend', 8), ('ADX 횡보 기준', 'adx_side', 8),
        ('돈치안 채널', 'dc_entry', 8), ('추세 ATR 배수', 'atr_mult', 8),
        ('EMA 필터', 'ema_filter', 8), ('역추세 ATR 배수', 'mr_atr_mult', 8),
        ('최대 보유봉', 'mr_max_bars', 8), ('체크 주기 (초)', 'check_sec', 8),
    ]
    for r, (label, key, w) in enumerate(fields):
        tk.Label(form, text=label, bg=PANEL, fg=FG, font=('Arial', 10),
                 anchor='w').grid(row=r, column=0, sticky='w', pady=2)
        v = tk.StringVar(value=str(saved.get(key, DEFAULTS[key])))
        tk.Entry(form, textvariable=v, width=w, font=('Arial', 10),
                 justify='center').grid(row=r, column=1, padx=6, pady=2)
        vars_[key] = v

    tk.Label(form, text='시간봉', bg=PANEL, fg=FG, font=('Arial', 10),
             anchor='w').grid(row=len(fields), column=0, sticky='w', pady=2)
    interval_var = tk.StringVar(value=saved.get('interval', DEFAULTS['interval']))
    ttk.Combobox(form, textvariable=interval_var, values=INTERVALS, width=6,
                 state='readonly').grid(row=len(fields), column=1, padx=6, pady=2)

    start_btn = tk.Button(left, text="▶️ 봇 시작", bg='#0066cc', fg='#ffffff',
                          font=('Arial', 13, 'bold'), padx=24, pady=8)
    start_btn.pack(pady=(14, 4))
    stop_btn = tk.Button(left, text="⏹️ 정지", bg='#663333', fg='#ffffff',
                         font=('Arial', 11, 'bold'), padx=24, pady=4,
                         state='disabled')
    stop_btn.pack()
    tk.Label(left, text="정지 시 보유 포지션과 거래소 손절\n주문은 그대로 유지됩니다.",
             bg=PANEL, fg='#888888', font=('Arial', 8), justify='left'
             ).pack(pady=(6, 0))

    # ---------- 우측: 상태 + 로그 ----------
    right = tk.Frame(root, bg=BG)
    right.pack(side='left', fill='both', expand=True, pady=8, padx=(0, 8))

    tk.Label(right, text="📡 심볼 상태 (완성봉마다 갱신)", bg=BG, fg=ACCENT,
             font=('Arial', 11, 'bold')).pack(anchor='w')
    cols = ['심볼', '국면', 'ADX', '종가', '포지션', '신호종류', '진입가', '손절가', '보유봉']
    style = ttk.Style()
    try:
        style.theme_use('clam')
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=22)
        style.configure('Treeview.Heading', background='#3d3d3d', foreground=FG)
    except Exception:
        pass
    tree = ttk.Treeview(right, columns=cols, show='headings', height=8)
    for col in cols:
        tree.heading(col, text=col)
        tree.column(col, width=88, anchor='center')
    tree.pack(fill='x')

    tk.Label(right, text="📋 로그 (bot_v2_trades.csv에 거래 기록 저장)", bg=BG,
             fg=ACCENT, font=('Arial', 11, 'bold')).pack(anchor='w', pady=(8, 0))
    log_frame = tk.Frame(right, bg=BG)
    log_frame.pack(fill='both', expand=True)
    lsb = tk.Scrollbar(log_frame)
    lsb.pack(side='right', fill='y')
    log_text = tk.Text(log_frame, bg='#141414', fg='#cccccc',
                       font=('Consolas', 9), yscrollcommand=lsb.set, wrap='word')
    log_text.pack(side='left', fill='both', expand=True)
    lsb.config(command=log_text.yview)

    def gui_log(msg):
        log_q.put(('log', f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"))

    row_ids = {}

    def on_status(symbol, sig, st):
        log_q.put(('status', (symbol, sig, st)))

    def poll_queue():
        try:
            while True:
                kind, payload = log_q.get_nowait()
                if kind == 'log':
                    log_text.insert('end', payload + '\n')
                    log_text.see('end')
                elif kind == 'status':
                    symbol, sig, st = payload
                    active = st and st.get('active')
                    vals = [symbol, sig['regime'], sig['adx'],
                            f"{sig['close']:.6g}",
                            (st['side'] if active else '-'),
                            ('추세' if active and st['kind'] == 'T'
                             else '역추세' if active else '-'),
                            (f"{st['entry']:.6g}" if active else '-'),
                            (f"{st['stop']:.6g}" if active else '-'),
                            (st['bars_held'] if active else '-')]
                    if symbol in row_ids:
                        tree.item(row_ids[symbol], values=vals)
                    else:
                        row_ids[symbol] = tree.insert('', 'end', values=vals)
        except queue.Empty:
            pass
        root.after(200, poll_queue)

    def read_params():
        p = dict(DEFAULTS)
        int_keys = {'max_lev', 'max_positions', 'adx_trend', 'adx_side',
                    'dc_entry', 'ema_filter', 'mr_max_bars', 'check_sec'}
        for key, v in vars_.items():
            raw = v.get().strip()
            if key == 'symbols':
                p[key] = raw
            else:
                p[key] = int(float(raw)) if key in int_keys else float(raw)
        p['interval'] = interval_var.get()
        return p

    def start():
        if bot_holder[0] and bot_holder[0].running:
            return
        if not API_KEY or not API_SECRET:
            messagebox.showerror("API 키 없음",
                                 "1_config.py에 API_KEY / API_SECRET을 설정해주세요.")
            return
        try:
            p = read_params()
        except ValueError as e:
            messagebox.showwarning("입력 오류", f"숫자를 확인해주세요!\n\n{e}")
            return
        if not TESTNET:
            if not messagebox.askyesno(
                    "⚠️ 실거래 확인",
                    "메인넷(실제 돈)으로 거래합니다!\n\n"
                    "테스트넷에서 충분히 검증하셨나요?\n계속할까요?"):
                return
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(p, f, ensure_ascii=False, indent=2)

        api = BinanceAPI(API_KEY, API_SECRET, TESTNET)
        bot = TradingBot(api, p, log=gui_log, on_status=on_status)
        bot_holder[0] = bot
        threading.Thread(target=bot.run, daemon=True).start()
        start_btn.config(state='disabled')
        stop_btn.config(state='normal')

    def stop():
        if bot_holder[0]:
            bot_holder[0].stop()
        start_btn.config(state='normal')
        stop_btn.config(state='disabled')

    start_btn.config(command=start)
    stop_btn.config(command=stop)

    def on_close():
        if bot_holder[0] and bot_holder[0].running:
            if not messagebox.askyesno("종료", "봇이 실행 중입니다. 종료할까요?\n"
                                       "(보유 포지션과 거래소 손절 주문은 유지됩니다)"):
                return
            bot_holder[0].stop()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', on_close)

    gui_log("✅ 준비 완료. 설정 확인 후 [▶️ 봇 시작]을 누르세요.")
    gui_log("   전략: ADX≥25 추세장→돈치안 돌파 / ADX<20 횡보장→BB+RSI 역추세")
    gui_log("   손절은 거래소 STOP_MARKET 주문으로 실제로 걸립니다.")
    if TESTNET:
        gui_log("   현재 테스트넷 모드 — 가상 자금으로 안전하게 검증하세요.")
    poll_queue()
    root.mainloop()


if __name__ == '__main__':
    launch_gui()
