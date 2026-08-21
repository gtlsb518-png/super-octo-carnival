#!/usr/bin/env python3
"""
🐋 고래 추종 자동매매 봇 — 단독 실행형

🔥 이 파일 하나만 있으면 실행됩니다.
   실행:  python whale_bot.py     (더블클릭 가능)

아이디어:
  바이낸스 선물 '실시간 체결 스트림(aggTrade)'을 구독해서
  초대형 시장가 체결(= 고래)이 어느 방향으로 터지는지 실시간 감지하고,
  그 방향으로 자동 진입한다. ('시장의 고래 흐름'을 따라감)

진입:
  · 최근 N초(window_sec) 동안의 '고래 체결'만 모아서
    순매수금액 - 순매도금액 = 순흐름(net)을 계산
  · 순매수 net ≥ 임계금액(net_usd)  → 롱 진입
  · 순매도 net ≤ -임계금액          → 숏 진입 (allow_short 시)
  · 여기서 '고래 체결'이란 단일 체결금액 ≥ whale_usd 인 시장가 체결

청산 (2중):
  1) 진입 즉시 거래소에 TP(익절) + SL(손절) 주문을 걸어둠
     → 프로그램이 꺼져도 거래소가 알아서 청산
  2) 보유 중 고래 흐름이 '반대'로 강하게 뒤집히면 즉시 시장가 청산
     (reverse_exit)

안전장치:
  · 기본 테스트넷(모의) 모드. 실거래는 체크 해제 필요
  · 체결 스트림은 항상 메인넷 실시세(테스트넷엔 고래가 없음)
  · 주문은 테스트넷/메인넷 선택
  · 코인당 동시 1포지션, 청산 후 재진입 쿨다운
  · 수량/가격은 거래소 규격(stepSize/tickSize)에 맞춤
"""

import hashlib
import hmac
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from urllib.parse import urlencode

# ==================== 라이브러리 자동 설치 ====================
for _mod, _pip in [('requests', 'requests'), ('websocket', 'websocket-client')]:
    try:
        __import__(_mod)
    except ImportError:
        print(f"📦 {_pip} 설치 중...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', _pip],
                              stdout=subprocess.DEVNULL)

import requests  # noqa: E402
import websocket  # noqa: E402  (websocket-client)

SETTINGS_FILE = 'whale_settings.json'
TRADE_LOG_CSV = 'whale_trades.csv'

# ==================== 기본 설정값 ====================
DEFAULTS = {
    'api_key': '',
    'api_secret': '',
    'testnet': True,          # True=테스트넷(모의), False=실거래
    'symbols': 'BTCUSDT,ETHUSDT,SOLUSDT',
    'whale_usd': 500000,      # 단일 대형체결로 인정할 최소 금액 (USD)
    'window_sec': 10,         # 고래 흐름 집계 창 (초)
    'net_usd': 1000000,       # 순흐름 진입 임계 (USD)
    'amount': 50.0,           # 코인당 진입금 (USDT)
    'leverage': 3,            # 레버리지 (배)
    'tp_pct': 1.5,            # 익절 % (가격 기준)
    'sl_pct': 1.0,            # 손절 % (가격 기준)
    'allow_short': True,      # 숏 진입 허용
    'reverse_exit': True,     # 고래 반대신호 시 즉시 청산
    'cooldown_sec': 60,       # 청산 후 재진입 쿨다운 (초)
    'max_positions': 3,       # 동시 보유 최대 포지션 수
}


def load_settings():
    s = dict(DEFAULTS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                s.update(json.load(f))
        except Exception:
            pass
    else:
        # 기존 봇의 1_config.py가 같은 폴더에 있으면 API 키 가져오기
        try:
            import importlib
            cfg = importlib.import_module('1_config')
            s['api_key'] = getattr(cfg, 'API_KEY', '')
            s['api_secret'] = getattr(cfg, 'API_SECRET', '')
            s['testnet'] = bool(getattr(cfg, 'TESTNET', True))
        except Exception:
            pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"설정 저장 실패: {e}")


# ==================== 바이낸스 선물 API (롱/숏 겸용) ====================
class BinanceFutures:
    def __init__(self, key, secret, testnet=True, log=print):
        self.key, self.secret, self.log = key, secret, log
        self.base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        self.chart = "https://fapi.binance.com"      # 시세/거래소규격은 항상 메인넷
        self.s = requests.Session()
        self.s.headers.update({'X-MBX-APIKEY': key})
        self.offset = 0
        self.filters = {}
        self.sync_time()

    def sync_time(self):
        try:
            r = self.s.get(f"{self.base}/fapi/v1/time", timeout=10)
            self.offset = r.json()['serverTime'] - int(time.time() * 1000)
        except Exception as e:
            self.log(f"⚠️ 시간 동기화 실패: {e}")
            self.offset = 0

    def _req(self, method, path, params=None, signed=False, base=None):
        url = (base or self.base) + path
        params = dict(params or {})
        if signed:
            params['timestamp'] = int(time.time() * 1000) + self.offset
            params['recvWindow'] = 10000
            q = urlencode(params)
            params['signature'] = hmac.new(self.secret.encode(), q.encode(),
                                           hashlib.sha256).hexdigest()
        try:
            r = self.s.request(method, url, params=params, timeout=15)
            if r.status_code >= 400:
                try:
                    err = r.json()
                    code = err.get('code')
                    if code == -1021:            # 시간 오차
                        self.sync_time()
                    # -2022(ReduceOnly 거부), -4046(마진타입 동일) 등은 소음이라 무시 가능
                    if code not in (-4046,):
                        self.log(f"❌ API 오류 {code}: {err.get('msg')}")
                except Exception:
                    self.log(f"❌ API 오류: {r.text[:200]}")
                return None
            return r.json()
        except Exception as e:
            self.log(f"❌ 통신 오류: {e}")
            return None

    # ---------- 조회 ----------
    def balance(self):
        a = self._req('GET', '/fapi/v2/account', signed=True)
        return float(a.get('totalWalletBalance', 0)) if a else 0.0

    def position(self, symbol):
        d = self._req('GET', '/fapi/v2/positionRisk', {'symbol': symbol}, signed=True)
        if not d:
            return None
        for p in d:
            amt = float(p['positionAmt'])
            if amt != 0:
                return {'amt': amt,
                        'side': 'long' if amt > 0 else 'short',
                        'entry': float(p['entryPrice']),
                        'pnl': float(p['unRealizedProfit']),
                        'mark': float(p.get('markPrice', 0))}
        return False   # False = 조회성공·포지션없음 / None = 조회실패

    def load_filters(self, symbol):
        if symbol in self.filters:
            return self.filters[symbol]
        d = self._req('GET', '/fapi/v1/exchangeInfo', base=self.chart)
        if d:
            for s in d.get('symbols', []):
                f = {x['filterType']: x for x in s.get('filters', [])}
                self.filters[s['symbol']] = {
                    'step': f.get('LOT_SIZE', {}).get('stepSize', '0.001'),
                    'minQty': f.get('LOT_SIZE', {}).get('minQty', '0.001'),
                    'tick': f.get('PRICE_FILTER', {}).get('tickSize', '0.01'),
                    'minNotional': f.get('MIN_NOTIONAL', {}).get('notional', '5'),
                }
        return self.filters.get(symbol, {'step': '0.001', 'minQty': '0.001',
                                         'tick': '0.01', 'minNotional': '5'})

    def fmt_qty(self, symbol, qty):
        f = self.load_filters(symbol)
        step = Decimal(str(f['step']))
        q = (Decimal(str(qty)) / step).quantize(Decimal('1'), rounding=ROUND_DOWN) * step
        dec = max(0, -step.normalize().as_tuple().exponent)
        return f"{q:.{dec}f}", float(q), float(f['minQty']), float(f['minNotional'])

    def fmt_price(self, symbol, price):
        f = self.load_filters(symbol)
        tick = Decimal(str(f['tick']))
        p = (Decimal(str(price)) / tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick
        dec = max(0, -tick.normalize().as_tuple().exponent)
        return f"{p:.{dec}f}"

    # ---------- 주문 ----------
    def set_leverage(self, symbol, lev):
        self._req('POST', '/fapi/v1/leverage', {'symbol': symbol, 'leverage': lev}, signed=True)

    def set_isolated(self, symbol):
        self._req('POST', '/fapi/v1/marginType',
                  {'symbol': symbol, 'marginType': 'ISOLATED'}, signed=True)

    def market_order(self, symbol, side, qty_str):
        """side: BUY(롱 진입 / 숏 청산) or SELL(숏 진입 / 롱 청산)"""
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': side, 'type': 'MARKET',
                          'quantity': qty_str}, signed=True)

    def close_market(self, symbol, close_side, qty_str):
        """close_side: 롱 청산=SELL, 숏 청산=BUY"""
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': close_side, 'type': 'MARKET',
                          'quantity': qty_str, 'reduceOnly': 'true'}, signed=True)

    def place_tp(self, symbol, close_side, stop_price):
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': close_side, 'type': 'TAKE_PROFIT_MARKET',
                          'stopPrice': stop_price, 'closePosition': 'true',
                          'workingType': 'CONTRACT_PRICE'}, signed=True)

    def place_sl(self, symbol, close_side, stop_price):
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': close_side, 'type': 'STOP_MARKET',
                          'stopPrice': stop_price, 'closePosition': 'true',
                          'workingType': 'CONTRACT_PRICE'}, signed=True)

    def cancel_all(self, symbol):
        return self._req('DELETE', '/fapi/v1/allOpenOrders', {'symbol': symbol}, signed=True)


# ==================== 실시간 고래 체결 스트림 ====================
class WhaleStream:
    """
    바이낸스 선물 aggTrade 스트림(항상 메인넷)을 구독해서,
    심볼별로 '최근 window_sec 초 동안의 고래 체결'만 보관한다.
      · 고래 체결 = 단일 체결금액 ≥ whale_usd
      · net_flow() → (순흐름, 매수금액, 매도금액, 최근고래건수)
    """

    def __init__(self, symbols, whale_usd, window_sec, log=print):
        self.symbols = [s.upper() for s in symbols]
        self.whale_usd = float(whale_usd)
        self.window_sec = float(window_sec)
        self.log = log
        self.lock = threading.Lock()
        self.trades = {s: deque() for s in self.symbols}   # (ts, side, notional)
        self.last_price = {s: 0.0 for s in self.symbols}
        self.connected = False
        self.running = False
        self.ws = None

    def update_params(self, whale_usd, window_sec):
        self.whale_usd = float(whale_usd)
        self.window_sec = float(window_sec)

    def _url(self):
        streams = '/'.join(f"{s.lower()}@aggTrade" for s in self.symbols)
        return f"wss://fstream.binance.com/stream?streams={streams}"

    def _on_message(self, _ws, message):
        try:
            payload = json.loads(message)
            d = payload.get('data', payload)
            sym = d.get('s', '').upper()
            if sym not in self.trades:
                return
            price = float(d['p'])
            qty = float(d['q'])
            notional = price * qty
            # m=True → 매수자가 메이커 = 공격자는 매도(시장가 매도)
            side = 'sell' if d.get('m') else 'buy'
            now = time.time()
            with self.lock:
                self.last_price[sym] = price
                if notional >= self.whale_usd:
                    self.trades[sym].append((now, side, notional))
        except Exception:
            pass

    def _on_error(self, _ws, err):
        self.connected = False
        self.log(f"⚠️ 스트림 오류: {err}")

    def _on_close(self, _ws, *_a):
        self.connected = False

    def _on_open(self, _ws):
        self.connected = True
        self.log(f"🔌 실시간 체결 스트림 연결됨 ({', '.join(self.symbols)})")

    def start(self):
        self.running = True
        threading.Thread(target=self._run_forever, daemon=True).start()

    def _run_forever(self):
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self._url(),
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever(ping_interval=180, ping_timeout=10)
            except Exception as e:
                self.log(f"⚠️ 스트림 재연결 대기: {e}")
            if self.running:
                self.connected = False
                time.sleep(3)   # 재연결

    def stop(self):
        self.running = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def net_flow(self, sym):
        """최근 window_sec 초의 고래 순흐름 반환"""
        cutoff = time.time() - self.window_sec
        buy = sell = 0.0
        cnt = 0
        with self.lock:
            dq = self.trades.get(sym)
            if dq is None:
                return 0.0, 0.0, 0.0, 0
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            for _ts, side, notional in dq:
                if side == 'buy':
                    buy += notional
                else:
                    sell += notional
                cnt += 1
        return buy - sell, buy, sell, cnt

    def price(self, sym):
        with self.lock:
            return self.last_price.get(sym, 0.0)


# ==================== 트레이딩 봇 ====================
class WhaleBot:
    def __init__(self, cfg, log, status_cb):
        self.cfg, self.log, self.status_cb = cfg, log, status_cb
        self.api = BinanceFutures(cfg['api_key'], cfg['api_secret'],
                                  cfg['testnet'], log)
        self.stream = WhaleStream(self._symbols(), cfg['whale_usd'],
                                  cfg['window_sec'], log)
        self.running = False
        self.state = {}         # 심볼별 상태 표시용
        self.cooldown = {}      # 심볼별 재진입 가능 시각

    def start(self):
        self.running = True
        self.stream.start()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.stream.stop()

    def _symbols(self):
        return [s.strip().upper() for s in self.cfg['symbols'].split(',') if s.strip()]

    def _open_count(self):
        return sum(1 for s in self._symbols() if self.state.get(s, {}).get('pos'))

    def _loop(self):
        mode = "테스트넷(모의)" if self.cfg['testnet'] else "🔴 실거래"
        self.log("=" * 48)
        self.log(f"🐋 고래 추종 봇 시작 | {mode}")
        self.log(f"   코인: {', '.join(self._symbols())}")
        self.log(f"   고래기준 ${self.cfg['whale_usd']:,.0f} | 창 {self.cfg['window_sec']}초 | "
                 f"진입임계 ${self.cfg['net_usd']:,.0f}")
        self.log(f"   진입금 {self.cfg['amount']} USDT × {self.cfg['leverage']}배 | "
                 f"익절 {self.cfg['tp_pct']}% | 손절 {self.cfg['sl_pct']}% | "
                 f"숏 {'허용' if self.cfg['allow_short'] else '금지'}")
        bal = self.api.balance()
        self.log(f"   잔고: {bal:,.2f} USDT")
        self.log("=" * 48)

        while self.running:
            try:
                for sym in self._symbols():
                    if not self.running:
                        break
                    self._check(sym)
                self.status_cb()
            except Exception as e:
                self.log(f"⚠️ 루프 오류: {e}")
            time.sleep(1)
        self.log("⏹️ 봇 정지됨")

    def _check(self, sym):
        net, buy, sell, cnt = self.stream.net_flow(sym)
        price = self.stream.price(sym)
        st = self.state.setdefault(sym, {})
        st['price'] = price
        st['net'] = net
        st['buy'] = buy
        st['sell'] = sell
        st['cnt'] = cnt

        pos = self.api.position(sym)
        net_usd = float(self.cfg['net_usd'])

        # ---------- 포지션 보유 중 ----------
        if pos:
            st['pos'] = True
            st['pside'] = pos['side']
            st['entry'] = pos['entry']
            st['pnl'] = pos['pnl']
            st['roi'] = (pos['pnl'] / (self.cfg['amount'] or 1)) * 100
            st['sig'] = f"📈 {pos['side'].upper()} 보유"

            # 고래 반대신호 청산
            if self.cfg['reverse_exit']:
                if pos['side'] == 'long' and net <= -net_usd:
                    self.log(f"🔄 [{sym}] 롱 보유 중 고래 순매도 ${-net:,.0f} 감지 → 청산")
                    self._close(sym, pos)
                elif pos['side'] == 'short' and net >= net_usd:
                    self.log(f"🔄 [{sym}] 숏 보유 중 고래 순매수 ${net:,.0f} 감지 → 청산")
                    self._close(sym, pos)
            return

        # ---------- 방금 청산됨 감지 ----------
        if pos is False and st.get('pos'):
            self.log(f"✅ [{sym}] 포지션 청산 완료 (거래소 TP/SL 체결)")
            self.api.cancel_all(sym)     # 남은 반대편 주문 정리
            st.pop('entry', None)
            st.pop('roi', None)
            st.pop('pside', None)
            self.cooldown[sym] = time.time() + float(self.cfg['cooldown_sec'])
        st['pos'] = False

        # ---------- 진입 판단 ----------
        if not self.stream.connected:
            st['sig'] = '스트림 연결중…'
            return
        if time.time() < self.cooldown.get(sym, 0):
            remain = int(self.cooldown[sym] - time.time())
            st['sig'] = f'쿨다운 {remain}s'
            return
        if price <= 0:
            st['sig'] = '체결 대기중'
            return

        if net >= net_usd:
            if self._open_count() >= int(self.cfg['max_positions']):
                st['sig'] = f"🟢 롱신호(만석 {self.cfg['max_positions']})"
                return
            st['sig'] = '🟢 롱 진입'
            self._enter(sym, 'long', price)
        elif net <= -net_usd and self.cfg['allow_short']:
            if self._open_count() >= int(self.cfg['max_positions']):
                st['sig'] = f"🔴 숏신호(만석 {self.cfg['max_positions']})"
                return
            st['sig'] = '🔴 숏 진입'
            self._enter(sym, 'short', price)
        else:
            st['sig'] = '대기중'

    def _enter(self, sym, direction, price):
        arrow = '🟢 롱' if direction == 'long' else '🔴 숏'
        self.log(f"{arrow} [{sym}] 고래 순흐름 감지! 진입 시도 (기준가 {price:,.4f})")
        self.api.cancel_all(sym)
        self.api.set_isolated(sym)
        self.api.set_leverage(sym, int(self.cfg['leverage']))

        raw_qty = self.cfg['amount'] * self.cfg['leverage'] / price
        qty_str, qty, min_qty, min_notional = self.api.fmt_qty(sym, raw_qty)
        if qty < min_qty or qty * price < min_notional:
            self.log(f"❌ [{sym}] 주문금액 부족 (수량 {qty}, 최소 {min_qty} / "
                     f"최소금액 {min_notional} USDT) → 진입금이나 레버리지를 올리세요")
            return

        entry_side = 'BUY' if direction == 'long' else 'SELL'
        order = self.api.market_order(sym, entry_side, qty_str)
        if not order:
            self.log(f"❌ [{sym}] 진입 주문 실패")
            return

        fill = float(order.get('avgPrice') or 0) or price
        self.log(f"✅ [{sym}] {direction.upper()} 진입 완료! 체결가 {fill:,.4f} | 수량 {qty_str}")

        # 거래소 TP/SL (프로그램 꺼져도 유지)
        close_side = 'SELL' if direction == 'long' else 'BUY'
        if direction == 'long':
            tp = self.api.fmt_price(sym, fill * (1 + self.cfg['tp_pct'] / 100))
            sl = self.api.fmt_price(sym, fill * (1 - self.cfg['sl_pct'] / 100))
        else:
            tp = self.api.fmt_price(sym, fill * (1 - self.cfg['tp_pct'] / 100))
            sl = self.api.fmt_price(sym, fill * (1 + self.cfg['sl_pct'] / 100))
        ok_tp = self.api.place_tp(sym, close_side, tp)
        ok_sl = self.api.place_sl(sym, close_side, sl)
        self.log(f"   🎯 익절 {tp} ({'등록' if ok_tp else '실패'})"
                 f" | 🛡️ 손절 {sl} ({'등록' if ok_sl else '실패'})")
        if not ok_sl:
            self.log(f"   ⚠️ [{sym}] 손절 주문 실패! 수동으로 확인하세요")

        st = self.state.setdefault(sym, {})
        st['pos'] = True
        st['pside'] = direction
        self._record(sym, direction, fill, qty, tp, sl)

    def _close(self, sym, pos):
        close_side = 'SELL' if pos['side'] == 'long' else 'BUY'
        qty_str, _, _, _ = self.api.fmt_qty(sym, abs(pos['amt']))
        self.api.cancel_all(sym)     # 거래소 TP/SL 취소
        r = self.api.close_market(sym, close_side, qty_str)
        self.log(f"{'✅' if r else '❌'} [{sym}] 반대신호 청산 "
                 f"(손익 {pos['pnl']:+.2f} USDT)")
        st = self.state.setdefault(sym, {})
        st['pos'] = False
        st.pop('pside', None)
        self.cooldown[sym] = time.time() + float(self.cfg['cooldown_sec'])

    def _record(self, sym, direction, price, qty, tp, sl):
        try:
            new = not os.path.exists(TRADE_LOG_CSV)
            with open(TRADE_LOG_CSV, 'a', encoding='utf-8-sig') as f:
                if new:
                    f.write('시각,코인,방향,진입가,수량,익절가,손절가,진입금,레버리지\n')
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{sym},{direction},{price},"
                        f"{qty},{tp},{sl},{self.cfg['amount']},{self.cfg['leverage']}\n")
        except Exception:
            pass

    def close_all(self):
        """모든 포지션 즉시 시장가 청산"""
        for sym in self._symbols():
            pos = self.api.position(sym)
            if pos:
                self._close(sym, pos)
        self.status_cb()


# ==================== GUI ====================
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    BG, PANEL, FG, ACC = '#1e1e1e', '#2d2d2d', '#ffffff', '#00ccff'
    cfg = load_settings()
    log_q = queue.Queue()
    bot = [None]

    root = tk.Tk()
    root.title("🐋 고래 추종 자동매매 봇 (실시간 대형체결 추종)")
    root.geometry("1120x780")
    root.configure(bg=BG)

    banner = tk.Label(root, text="", font=('Arial', 10, 'bold'))
    banner.pack(fill='x')

    def paint_banner():
        if cfg['testnet']:
            banner.config(text="🧪 테스트넷(모의거래) 모드 — 실제 돈이 아닙니다",
                          bg='#0a3d2e', fg='#00ff88')
        else:
            banner.config(text="🔴 실거래 모드 — 실제 자금이 사용됩니다!",
                          bg='#5a0000', fg='#ffdddd')

    left = tk.Frame(root, bg=PANEL)
    left.pack(side='left', fill='y', padx=8, pady=8)
    tk.Label(left, text="⚙️ 설정", bg=PANEL, fg=ACC,
             font=('Arial', 12, 'bold')).pack(pady=(8, 4))

    vars_ = {}
    form = tk.Frame(left, bg=PANEL)
    form.pack(padx=10)
    rows = [
        ('API Key', 'api_key', 26), ('API Secret', 'api_secret', 26),
        ('코인 (쉼표로 구분)', 'symbols', 26),
        ('고래 기준금액 ($)', 'whale_usd', 14),
        ('집계 창 (초)', 'window_sec', 14),
        ('진입 임계금액 ($)', 'net_usd', 14),
        ('진입금 (USDT)', 'amount', 14),
        ('레버리지 (배)', 'leverage', 14),
        ('익절 TP %', 'tp_pct', 14),
        ('손절 SL %', 'sl_pct', 14),
        ('재진입 쿨다운 (초)', 'cooldown_sec', 14),
        ('최대 포지션 수', 'max_positions', 14),
    ]
    for r, (label, key, w) in enumerate(rows):
        tk.Label(form, text=label, bg=PANEL, fg=FG, font=('Arial', 9),
                 anchor='w').grid(row=r, column=0, sticky='w', pady=2)
        v = tk.StringVar(value=str(cfg.get(key, '')))
        show = '*' if 'secret' in key or 'key' in key else None
        tk.Entry(form, textvariable=v, width=w, font=('Arial', 9),
                 show=show).grid(row=r, column=1, padx=6, pady=2)
        vars_[key] = v

    allow_short_var = tk.BooleanVar(value=cfg['allow_short'])
    tk.Checkbutton(left, text="숏 진입 허용", variable=allow_short_var,
                   bg=PANEL, fg='#ffaa00', selectcolor=BG, activebackground=PANEL,
                   font=('Arial', 10)).pack(anchor='w', padx=10, pady=(6, 0))

    reverse_exit_var = tk.BooleanVar(value=cfg['reverse_exit'])
    tk.Checkbutton(left, text="고래 반대신호 시 청산", variable=reverse_exit_var,
                   bg=PANEL, fg='#ffaa00', selectcolor=BG, activebackground=PANEL,
                   font=('Arial', 10)).pack(anchor='w', padx=10)

    testnet_var = tk.BooleanVar(value=cfg['testnet'])
    tk.Checkbutton(left, text="테스트넷(모의거래) 사용", variable=testnet_var,
                   bg=PANEL, fg='#00ffff', selectcolor=BG, activebackground=PANEL,
                   font=('Arial', 10, 'bold'),
                   command=lambda: (cfg.update(testnet=testnet_var.get()),
                                    paint_banner())).pack(anchor='w', padx=10, pady=6)

    tk.Label(left, text="※ 진입 로직 (자동)", bg=PANEL, fg='#ffaa00',
             font=('Arial', 9, 'bold')).pack(anchor='w', padx=10)
    for t in ["  · 실시간 체결(aggTrade) 구독",
              "  · 단일 체결 ≥ 고래기준 = '고래'",
              "  · 창(초) 내 순매수-순매도 = 순흐름",
              "  · 순흐름 ≥ 임계 → 롱 / ≤ -임계 → 숏"]:
        tk.Label(left, text=t, bg=PANEL, fg='#888888',
                 font=('Arial', 8)).pack(anchor='w', padx=10)

    def collect():
        c = dict(cfg)
        int_keys = ('leverage', 'max_positions', 'cooldown_sec', 'window_sec')
        for k, v in vars_.items():
            raw = v.get().strip()
            if k in ('api_key', 'api_secret', 'symbols'):
                c[k] = raw
            else:
                try:
                    c[k] = int(float(raw)) if k in int_keys else float(raw)
                except ValueError:
                    c[k] = DEFAULTS[k]
                    v.set(str(DEFAULTS[k]))
        c['testnet'] = testnet_var.get()
        c['allow_short'] = allow_short_var.get()
        c['reverse_exit'] = reverse_exit_var.get()
        return c

    def gui_log(m):
        log_q.put(str(m))

    def start():
        c = collect()
        if not c['api_key'] or not c['api_secret']:
            messagebox.showwarning("API 키 필요", "API Key와 Secret을 입력하세요.")
            return
        if not c['testnet']:
            if not messagebox.askyesno("⚠️ 실거래 확인",
                                       "실거래 모드입니다. 실제 자금이 사용됩니다.\n\n"
                                       "정말 시작하시겠습니까?"):
                return
        cfg.update(c)
        save_settings(cfg)
        bot[0] = WhaleBot(cfg, gui_log, refresh_status)
        bot[0].start()
        start_btn.config(state='disabled')
        stop_btn.config(state='normal')

    def stop():
        if bot[0]:
            bot[0].stop()
        start_btn.config(state='normal')
        stop_btn.config(state='disabled')

    def close_all():
        if not bot[0]:
            return
        if messagebox.askyesno("전체 청산", "보유 중인 모든 포지션을 즉시 청산할까요?"):
            threading.Thread(target=bot[0].close_all, daemon=True).start()

    btns = tk.Frame(left, bg=PANEL)
    btns.pack(pady=12)
    start_btn = tk.Button(btns, text="▶️ 시작", command=start, bg='#00aa00', fg='#fff',
                          font=('Arial', 12, 'bold'), padx=14, pady=6)
    start_btn.pack(side='left', padx=3)
    stop_btn = tk.Button(btns, text="⏹️ 정지", command=stop, bg='#666', fg='#fff',
                         font=('Arial', 12, 'bold'), padx=14, pady=6, state='disabled')
    stop_btn.pack(side='left', padx=3)
    tk.Button(left, text="🛑 전체 청산", command=close_all, bg='#cc4400', fg='#fff',
              font=('Arial', 10, 'bold'), padx=10, pady=4).pack()

    # ---------- 우측: 상태 + 로그 ----------
    right = tk.Frame(root, bg=BG)
    right.pack(side='left', fill='both', expand=True, padx=(0, 8), pady=8)
    tk.Label(right, text="📊 코인별 고래 흐름", bg=BG, fg=ACC,
             font=('Arial', 12, 'bold')).pack(anchor='w')

    cols = ['코인', '상태', '현재가', '순흐름($)', '매수($)', '매도($)',
            '고래수', '진입가', '손익', 'ROI%']
    style = ttk.Style()
    try:
        style.theme_use('clam')
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=24)
        style.configure('Treeview.Heading', background='#3d3d3d', foreground=FG)
    except Exception:
        pass
    tree = ttk.Treeview(right, columns=cols, show='headings', height=9)
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=95 if c in ('순흐름($)', '매수($)', '매도($)') else 72,
                    anchor='center')
    tree.column('상태', width=120)
    tree.pack(fill='x')

    tk.Label(right, text="📋 로그", bg=BG, fg=ACC,
             font=('Arial', 11, 'bold')).pack(anchor='w', pady=(8, 0))
    lf = tk.Frame(right, bg=BG)
    lf.pack(fill='both', expand=True)
    sb = tk.Scrollbar(lf)
    sb.pack(side='right', fill='y')
    log_text = tk.Text(lf, bg='#141414', fg='#ccc', font=('Consolas', 9),
                       yscrollcommand=sb.set, wrap='word')
    log_text.pack(side='left', fill='both', expand=True)
    sb.config(command=log_text.yview)

    def refresh_status():
        pass  # 실제 갱신은 poll에서 (스레드 안전)

    def _fmt_usd(v):
        return f"{v:+,.0f}" if v else '0'

    def poll():
        try:
            while True:
                msg = log_q.get_nowait()
                log_text.insert('end', f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                log_text.see('end')
                if int(log_text.index('end-1c').split('.')[0]) > 500:
                    log_text.delete('1.0', '100.0')
        except queue.Empty:
            pass
        if bot[0]:
            for i in tree.get_children():
                tree.delete(i)
            for sym in bot[0]._symbols():
                st = bot[0].state.get(sym, {})
                tree.insert('', 'end', values=[
                    sym,
                    st.get('sig', '-'),
                    f"{st.get('price', 0):,.4f}" if st.get('price') else '-',
                    _fmt_usd(st.get('net', 0)),
                    f"{st.get('buy', 0):,.0f}",
                    f"{st.get('sell', 0):,.0f}",
                    st.get('cnt', 0),
                    f"{st.get('entry', 0):,.4f}" if st.get('entry') else '-',
                    f"{st.get('pnl', 0):+.2f}" if st.get('pos') else '-',
                    f"{st.get('roi', 0):+.2f}%" if st.get('pos') else '-',
                ])
        root.after(1000, poll)

    paint_banner()
    gui_log("✅ 준비 완료. API 키 확인 후 [▶️ 시작]을 누르세요.")
    gui_log(f"   고래기준 ${cfg['whale_usd']:,.0f} | 창 {cfg['window_sec']}초 | "
            f"진입임계 ${cfg['net_usd']:,.0f}")
    gui_log("   ⚠️ 임계금액이 낮으면 잦은 진입, 높으면 신호가 드물어집니다. 코인별로 조절하세요.")
    poll()
    root.protocol("WM_DELETE_WINDOW", lambda: (stop(), save_settings(collect()),
                                               root.destroy()))
    root.mainloop()


# ==================== CLI (GUI 없을 때) ====================
def run_cli():
    cfg = load_settings()
    if not cfg['api_key'] or not cfg['api_secret']:
        print("❌ whale_settings.json 또는 1_config.py에 API 키를 넣어주세요.")
        return
    bot = WhaleBot(cfg, print, lambda: None)
    bot.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n정지 중…")
        bot.stop()


if __name__ == '__main__':
    try:
        launch_gui()
    except ImportError:
        print("⚠️ tkinter(GUI)가 없어 CLI 모드로 실행합니다. (Ctrl+C 로 종료)")
        run_cli()
    except Exception as e:
        import traceback
        print("\n❌ 오류 발생:")
        traceback.print_exc()
        print(f"\n요약: {e}")
        input("\n[엔터]로 종료...")
