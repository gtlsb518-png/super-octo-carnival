#!/usr/bin/env python3
"""
🐋 고래 추종 자동매매 봇 + 🏆 리더보드 트레이더 추적 — 단독 실행형

🔥 이 파일 하나만 있으면 실행됩니다.
   실행:  python whale_bot.py     (더블클릭 가능)

[1] 고래 추종 자동매매 (실시간 대형체결)
    바이낸스 선물 '실시간 체결 스트림(aggTrade)'을 구독해서
    초대형 시장가 체결(= 고래)이 어느 방향으로 터지는지 감지하고,
    그 방향으로 자동 진입한다. (코인별로 기준을 따로 설정 가능)

    진입:
      · 최근 N초(집계 창) 동안의 '고래 체결'만 모아 순매수-순매도 = 순흐름 계산
      · 순매수 net ≥ 진입임계  → 롱
      · 순매도 net ≤ -진입임계 → 숏 (allow_short 시)
      · '고래 체결' = 단일 체결금액 ≥ 고래기준금액(코인별)
    청산 (2중):
      1) 진입 즉시 거래소에 TP(익절) + SL(손절) 주문 등록 → 꺼져도 청산됨
      2) 보유 중 고래 흐름이 반대로 강하게 뒤집히면 즉시 시장가 청산

[2] 리더보드 트레이더 추적 (실명 고래)
    바이낸스 선물 리더보드 상위 트레이더를 조회해
    · 지금 어떤 코인에 어떻게 진입했는지 (방향/진입가/레버리지/평가손익)
    · ROI 성과
    · 승률(Binance 미제공 → 이 프로그램이 포지션 청산을 관찰해 직접 집계)
    을 실시간으로 보여준다. (정보 표시용 — 자동매매는 [1]이 담당)

안전장치:
  · 기본 테스트넷(모의) 모드. 실거래는 체크 해제 필요
  · 체결/리더보드 데이터는 항상 메인넷
  · 주문은 테스트넷/메인넷 선택
  · 코인당 동시 1포지션, 청산 후 재진입 쿨다운
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
LB_STATS_FILE = 'whale_leaderboard_stats.json'   # 트레이더별 관찰 승률 누적

# 코인별로 따로 설정할 수 있는 항목
PER_COIN_KEYS = ('whale_usd', 'net_usd', 'amount', 'leverage', 'tp_pct', 'sl_pct')

# ==================== 기본 설정값 (코인별 미지정 시 공통 기본값) ====================
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
    # ----- 코인별 개별 설정 (없으면 위 공통값 사용) -----
    # 예: {"BTCUSDT": {"whale_usd": 800000, "net_usd": 2000000, "leverage": 2}}
    'per_coin': {},
    # ----- 리더보드 추적 -----
    'lb_enable': True,
    'lb_period': 'WEEKLY',    # DAILY / WEEKLY / MONTHLY / ALL
    'lb_stat': 'ROI',         # ROI / PNL
    'lb_top': 10,             # 추적할 상위 트레이더 수
    'lb_poll_sec': 30,        # 리더보드 갱신 주기 (초)
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
        try:
            import importlib
            cfg = importlib.import_module('1_config')
            s['api_key'] = getattr(cfg, 'API_KEY', '')
            s['api_secret'] = getattr(cfg, 'API_SECRET', '')
            s['testnet'] = bool(getattr(cfg, 'TESTNET', True))
        except Exception:
            pass
    if not isinstance(s.get('per_coin'), dict):
        s['per_coin'] = {}
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
                    if code not in (-4046,):     # -4046=마진타입 동일(소음)
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
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': side, 'type': 'MARKET',
                          'quantity': qty_str}, signed=True)

    def close_market(self, symbol, close_side, qty_str):
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


# ==================== 실시간 고래 체결 스트림 (코인별 기준) ====================
class WhaleStream:
    """
    aggTrade 스트림(항상 메인넷) 구독. 심볼별 '고래 기준금액' 이상 체결만 보관.
      whale_usd_map: {심볼: 고래기준$}   window_map: {심볼: 집계창(초)}
    """

    def __init__(self, symbols, whale_usd_map, window_map, log=print):
        self.symbols = [s.upper() for s in symbols]
        self.whale_usd_map = {k.upper(): float(v) for k, v in whale_usd_map.items()}
        self.window_map = {k.upper(): float(v) for k, v in window_map.items()}
        self.log = log
        self.lock = threading.Lock()
        self.trades = {s: deque() for s in self.symbols}   # (ts, side, notional)
        self.last_price = {s: 0.0 for s in self.symbols}
        self.connected = False
        self.running = False
        self.ws = None

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
            notional = price * float(d['q'])
            side = 'sell' if d.get('m') else 'buy'   # m=True → 공격자=매도
            now = time.time()
            thr = self.whale_usd_map.get(sym, 500000.0)
            with self.lock:
                self.last_price[sym] = price
                if notional >= thr:
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
                    self._url(), on_open=self._on_open, on_message=self._on_message,
                    on_error=self._on_error, on_close=self._on_close)
                self.ws.run_forever(ping_interval=180, ping_timeout=10)
            except Exception as e:
                self.log(f"⚠️ 스트림 재연결 대기: {e}")
            if self.running:
                self.connected = False
                time.sleep(3)

    def stop(self):
        self.running = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def net_flow(self, sym):
        """최근 window 초의 고래 순흐름 → (순흐름, 매수$, 매도$, 건수)"""
        win = self.window_map.get(sym, 10.0)
        cutoff = time.time() - win
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


# ==================== 리더보드 트레이더 추적 ====================
class LeaderboardTracker:
    """
    바이낸스 선물 리더보드(공개 API) 상위 트레이더를 조회.
      · 현재 공개 포지션(코인/방향/진입가/레버리지/ROE/평가손익)
      · ROI 성과 (Binance 제공)
      · 관찰 승률: Binance가 승률을 주지 않으므로, 각 트레이더의 포지션이
        '청산'되는 순간을 관찰해 마지막 평가손익 부호로 승/패를 직접 누적.
        (whale_leaderboard_stats.json 에 저장 — 프로그램이 지켜본 만큼만 집계)
    ⚠️ 비공식 공개 API라 지역/정책에 따라 차단(403/451)될 수 있음.
    """
    BAPI = "https://www.binance.com/bapi/futures"

    def __init__(self, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.s = requests.Session()
        self.s.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/json',
            'clienttype': 'web',
        })
        self.lock = threading.Lock()
        self.traders = []          # 표시용 리스트(dict)
        self.prev_pos = {}         # uid -> {symbol: {'side','pnl','roe'}}
        self.stats = self._load_stats()   # uid -> {'nick','wins','losses'}
        self.running = False
        self.last_error = ''

    # ---------- 저장/불러오기 ----------
    def _load_stats(self):
        if os.path.exists(LB_STATS_FILE):
            try:
                with open(LB_STATS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_stats(self):
        try:
            with open(LB_STATS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- API ----------
    def _post(self, path, body):
        try:
            r = self.s.post(self.BAPI + path, json=body, timeout=15)
            if r.status_code >= 400:
                self.last_error = f"HTTP {r.status_code}"
                return None
            j = r.json()
            return j.get('data')
        except Exception as e:
            self.last_error = type(e).__name__
            return None

    def fetch_rank(self):
        data = self._post('/v3/public/future/leaderboard/getLeaderboardRank', {
            'isShared': True, 'isTrader': False,
            'periodType': self.cfg.get('lb_period', 'WEEKLY'),
            'statisticsType': self.cfg.get('lb_stat', 'ROI'),
            'tradeType': 'PERPETUAL',
        })
        if not data:
            return []
        # 응답이 리스트이거나 {'coinRankList'|'accountRankList':[...]}
        if isinstance(data, dict):
            data = (data.get('accountRankList') or data.get('coinRankList')
                    or data.get('rankList') or [])
        out = []
        for t in data:
            uid = t.get('encryptedUid')
            if not uid:
                continue
            out.append({
                'uid': uid,
                'nick': t.get('nickName') or uid[:8],
                'roi': _f(t.get('roi')) * (100 if abs(_f(t.get('roi'))) < 5 else 1),
                'pnl': _f(t.get('pnl')),
                'rank': t.get('rank') or (len(out) + 1),
            })
        return out

    def fetch_positions(self, uid):
        data = self._post('/v1/public/future/leaderboard/getOtherPosition',
                          {'encryptedUid': uid, 'tradeType': 'PERPETUAL'})
        if not data:
            return None      # None = 조회실패/비공개
        lst = data.get('otherPositionRetList') if isinstance(data, dict) else None
        if lst is None:
            return []
        out = []
        for p in lst:
            amt = _f(p.get('amount'))
            if amt == 0:
                continue
            out.append({
                'symbol': p.get('symbol', ''),
                'side': 'long' if amt > 0 else 'short',
                'entry': _f(p.get('entryPrice')),
                'mark': _f(p.get('markPrice')),
                'pnl': _f(p.get('pnl')),
                'roe': _f(p.get('roe')) * 100,   # roe는 소수(0.05=5%)로 옴
                'lev': _f(p.get('leverage')),
            })
        return out

    # ---------- 승률 관찰 ----------
    def _observe_closes(self, uid, nick, cur_positions):
        """이전에 있던 포지션이 사라지거나 방향이 바뀌면 '청산'으로 보고 승/패 집계"""
        prev = self.prev_pos.get(uid, {})
        cur = {p['symbol']: p for p in cur_positions}
        st = self.stats.setdefault(uid, {'nick': nick, 'wins': 0, 'losses': 0})
        st['nick'] = nick
        changed = False
        for sym, pp in prev.items():
            cp = cur.get(sym)
            if cp is None or cp['side'] != pp['side']:
                # 청산됨(또는 반대전환) → 마지막 평가손익 부호로 승패
                if pp.get('pnl', 0) >= 0:
                    st['wins'] += 1
                else:
                    st['losses'] += 1
                changed = True
        self.prev_pos[uid] = {p['symbol']: {'side': p['side'], 'pnl': p['pnl'],
                                            'roe': p['roe']} for p in cur_positions}
        if changed:
            self._save_stats()

    def winrate(self, uid):
        st = self.stats.get(uid)
        if not st:
            return None, 0
        n = st['wins'] + st['losses']
        if n == 0:
            return None, 0
        return st['wins'] / n * 100, n

    # ---------- 루프 ----------
    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        self.log("🏆 리더보드 트레이더 추적 시작 "
                 f"({self.cfg.get('lb_period')} / {self.cfg.get('lb_stat')} / "
                 f"상위 {self.cfg.get('lb_top')}명)")
        first = True
        while self.running:
            try:
                rank = self.fetch_rank()
                if not rank:
                    if first:
                        self.log(f"⚠️ 리더보드 조회 실패({self.last_error or '응답없음'}). "
                                 "네트워크/지역 제한일 수 있습니다.")
                    time.sleep(max(10, int(self.cfg.get('lb_poll_sec', 30))))
                    first = False
                    continue
                top = rank[:int(self.cfg.get('lb_top', 10))]
                built = []
                for t in top:
                    if not self.running:
                        break
                    positions = self.fetch_positions(t['uid'])
                    if positions is not None:
                        self._observe_closes(t['uid'], t['nick'], positions)
                    wr, n = self.winrate(t['uid'])
                    built.append({**t, 'positions': positions or [],
                                  'winrate': wr, 'obs': n,
                                  'shared': positions is not None})
                    time.sleep(0.3)   # 요청 간 간격(레이트리밋 완화)
                with self.lock:
                    self.traders = built
                if first:
                    self.log(f"🏆 리더보드 {len(built)}명 추적 중 "
                             "(포지션·ROI·관찰승률 표시)")
                first = False
            except Exception as e:
                self.log(f"⚠️ 리더보드 루프 오류: {e}")
            for _ in range(max(5, int(self.cfg.get('lb_poll_sec', 30)))):
                if not self.running:
                    break
                time.sleep(1)

    def snapshot(self):
        with self.lock:
            return list(self.traders)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ==================== 트레이딩 봇 ====================
class WhaleBot:
    def __init__(self, cfg, log, status_cb):
        self.cfg, self.log, self.status_cb = cfg, log, status_cb
        self.api = BinanceFutures(cfg['api_key'], cfg['api_secret'],
                                  cfg['testnet'], log)
        syms = self._symbols()
        self.stream = WhaleStream(
            syms,
            {s: self.pcfg(s, 'whale_usd') for s in syms},
            {s: self.cfg['window_sec'] for s in syms},   # 창은 공통(원하면 코인별 확장 가능)
            log)
        self.lb = LeaderboardTracker(cfg, log) if cfg.get('lb_enable') else None
        self.running = False
        self.state = {}
        self.cooldown = {}

    # 코인별 설정 조회 (per_coin 우선, 없으면 공통 기본값)
    def pcfg(self, sym, key):
        ov = self.cfg.get('per_coin', {}).get(sym, {})
        v = ov.get(key)
        if v not in (None, ''):
            return v
        return self.cfg[key]

    def start(self):
        self.running = True
        self.stream.start()
        if self.lb:
            self.lb.start()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.stream.stop()
        if self.lb:
            self.lb.stop()

    def _symbols(self):
        return [s.strip().upper() for s in self.cfg['symbols'].split(',') if s.strip()]

    def _open_count(self):
        return sum(1 for s in self._symbols() if self.state.get(s, {}).get('pos'))

    def _loop(self):
        mode = "테스트넷(모의)" if self.cfg['testnet'] else "🔴 실거래"
        self.log("=" * 48)
        self.log(f"🐋 고래 추종 봇 시작 | {mode}")
        for sym in self._symbols():
            self.log(f"   · {sym}: 고래≥${self.pcfg(sym,'whale_usd'):,.0f} "
                     f"진입임계 ${self.pcfg(sym,'net_usd'):,.0f} "
                     f"진입금 {self.pcfg(sym,'amount')}×{int(self.pcfg(sym,'leverage'))} "
                     f"TP {self.pcfg(sym,'tp_pct')}% SL {self.pcfg(sym,'sl_pct')}%")
        self.log(f"   창 {self.cfg['window_sec']}초 | 숏 {'허용' if self.cfg['allow_short'] else '금지'}"
                 f" | 반대신호청산 {'ON' if self.cfg['reverse_exit'] else 'OFF'}")
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
        st.update(price=price, net=net, buy=buy, sell=sell, cnt=cnt)

        pos = self.api.position(sym)
        net_usd = float(self.pcfg(sym, 'net_usd'))

        # ---------- 보유 중 ----------
        if pos:
            st['pos'] = True
            st['pside'] = pos['side']
            st['entry'] = pos['entry']
            st['pnl'] = pos['pnl']
            st['roi'] = (pos['pnl'] / (float(self.pcfg(sym, 'amount')) or 1)) * 100
            st['sig'] = f"📈 {pos['side'].upper()} 보유"
            if self.cfg['reverse_exit']:
                if pos['side'] == 'long' and net <= -net_usd:
                    self.log(f"🔄 [{sym}] 롱 보유 중 고래 순매도 ${-net:,.0f} → 청산")
                    self._close(sym, pos)
                elif pos['side'] == 'short' and net >= net_usd:
                    self.log(f"🔄 [{sym}] 숏 보유 중 고래 순매수 ${net:,.0f} → 청산")
                    self._close(sym, pos)
            return

        # ---------- 방금 청산됨 ----------
        if pos is False and st.get('pos'):
            self.log(f"✅ [{sym}] 포지션 청산 완료 (거래소 TP/SL 체결)")
            self.api.cancel_all(sym)
            for k in ('entry', 'roi', 'pside'):
                st.pop(k, None)
            self.cooldown[sym] = time.time() + float(self.cfg['cooldown_sec'])
        st['pos'] = False

        # ---------- 진입 판단 ----------
        if not self.stream.connected:
            st['sig'] = '스트림 연결중…'
            return
        if time.time() < self.cooldown.get(sym, 0):
            st['sig'] = f"쿨다운 {int(self.cooldown[sym]-time.time())}s"
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
        amount = float(self.pcfg(sym, 'amount'))
        lev = int(self.pcfg(sym, 'leverage'))
        tp_pct = float(self.pcfg(sym, 'tp_pct'))
        sl_pct = float(self.pcfg(sym, 'sl_pct'))
        arrow = '🟢 롱' if direction == 'long' else '🔴 숏'
        self.log(f"{arrow} [{sym}] 고래 순흐름 감지! 진입 시도 (기준가 {price:,.4f})")
        self.api.cancel_all(sym)
        self.api.set_isolated(sym)
        self.api.set_leverage(sym, lev)

        raw_qty = amount * lev / price
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

        close_side = 'SELL' if direction == 'long' else 'BUY'
        if direction == 'long':
            tp = self.api.fmt_price(sym, fill * (1 + tp_pct / 100))
            sl = self.api.fmt_price(sym, fill * (1 - sl_pct / 100))
        else:
            tp = self.api.fmt_price(sym, fill * (1 - tp_pct / 100))
            sl = self.api.fmt_price(sym, fill * (1 + sl_pct / 100))
        ok_tp = self.api.place_tp(sym, close_side, tp)
        ok_sl = self.api.place_sl(sym, close_side, sl)
        self.log(f"   🎯 익절 {tp} ({'등록' if ok_tp else '실패'})"
                 f" | 🛡️ 손절 {sl} ({'등록' if ok_sl else '실패'})")
        if not ok_sl:
            self.log(f"   ⚠️ [{sym}] 손절 주문 실패! 수동으로 확인하세요")

        st = self.state.setdefault(sym, {})
        st['pos'] = True
        st['pside'] = direction
        self._record(sym, direction, fill, qty, tp, sl, amount, lev)

    def _close(self, sym, pos):
        close_side = 'SELL' if pos['side'] == 'long' else 'BUY'
        qty_str, _, _, _ = self.api.fmt_qty(sym, abs(pos['amt']))
        self.api.cancel_all(sym)
        r = self.api.close_market(sym, close_side, qty_str)
        self.log(f"{'✅' if r else '❌'} [{sym}] 반대신호 청산 (손익 {pos['pnl']:+.2f} USDT)")
        st = self.state.setdefault(sym, {})
        st['pos'] = False
        st.pop('pside', None)
        self.cooldown[sym] = time.time() + float(self.cfg['cooldown_sec'])

    def _record(self, sym, direction, price, qty, tp, sl, amount, lev):
        try:
            new = not os.path.exists(TRADE_LOG_CSV)
            with open(TRADE_LOG_CSV, 'a', encoding='utf-8-sig') as f:
                if new:
                    f.write('시각,코인,방향,진입가,수량,익절가,손절가,진입금,레버리지\n')
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{sym},{direction},{price},"
                        f"{qty},{tp},{sl},{amount},{lev}\n")
        except Exception:
            pass

    def close_all(self):
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
    root.title("🐋 고래 추종 봇 + 🏆 리더보드 트레이더 추적")
    root.geometry("1240x820")
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
    tk.Label(left, text="⚙️ 공통 설정 (코인별 미지정 시)", bg=PANEL, fg=ACC,
             font=('Arial', 11, 'bold')).pack(pady=(8, 4))

    vars_ = {}
    form = tk.Frame(left, bg=PANEL)
    form.pack(padx=10)
    rows = [
        ('API Key', 'api_key', 24), ('API Secret', 'api_secret', 24),
        ('코인 (쉼표로 구분)', 'symbols', 24),
        ('고래 기준금액 ($)', 'whale_usd', 13),
        ('집계 창 (초)', 'window_sec', 13),
        ('진입 임계금액 ($)', 'net_usd', 13),
        ('진입금 (USDT)', 'amount', 13),
        ('레버리지 (배)', 'leverage', 13),
        ('익절 TP %', 'tp_pct', 13),
        ('손절 SL %', 'sl_pct', 13),
        ('재진입 쿨다운 (초)', 'cooldown_sec', 13),
        ('최대 포지션 수', 'max_positions', 13),
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
    reverse_exit_var = tk.BooleanVar(value=cfg['reverse_exit'])
    lb_enable_var = tk.BooleanVar(value=cfg['lb_enable'])
    testnet_var = tk.BooleanVar(value=cfg['testnet'])

    for text, var, fg in [("숏 진입 허용", allow_short_var, '#ffaa00'),
                          ("고래 반대신호 시 청산", reverse_exit_var, '#ffaa00'),
                          ("리더보드 트레이더 추적", lb_enable_var, '#00ccff')]:
        tk.Checkbutton(left, text=text, variable=var, bg=PANEL, fg=fg,
                       selectcolor=BG, activebackground=PANEL,
                       font=('Arial', 10)).pack(anchor='w', padx=10, pady=(4, 0))

    tk.Checkbutton(left, text="테스트넷(모의거래) 사용", variable=testnet_var,
                   bg=PANEL, fg='#00ffff', selectcolor=BG, activebackground=PANEL,
                   font=('Arial', 10, 'bold'),
                   command=lambda: (cfg.update(testnet=testnet_var.get()),
                                    paint_banner())).pack(anchor='w', padx=10, pady=6)

    # 리더보드 옵션 (period/stat/top/poll)
    lbf = tk.Frame(left, bg=PANEL)
    lbf.pack(padx=10, anchor='w')
    tk.Label(lbf, text='리더보드 기간', bg=PANEL, fg=FG,
             font=('Arial', 9)).grid(row=0, column=0, sticky='w')
    lb_period_var = tk.StringVar(value=cfg['lb_period'])
    ttk.Combobox(lbf, textvariable=lb_period_var, width=10, state='readonly',
                 values=['DAILY', 'WEEKLY', 'MONTHLY', 'ALL']).grid(row=0, column=1, pady=2)
    tk.Label(lbf, text='정렬 기준', bg=PANEL, fg=FG,
             font=('Arial', 9)).grid(row=1, column=0, sticky='w')
    lb_stat_var = tk.StringVar(value=cfg['lb_stat'])
    ttk.Combobox(lbf, textvariable=lb_stat_var, width=10, state='readonly',
                 values=['ROI', 'PNL']).grid(row=1, column=1, pady=2)
    tk.Label(lbf, text='추적 인원', bg=PANEL, fg=FG,
             font=('Arial', 9)).grid(row=2, column=0, sticky='w')
    lb_top_var = tk.StringVar(value=str(cfg['lb_top']))
    tk.Entry(lbf, textvariable=lb_top_var, width=12,
             font=('Arial', 9)).grid(row=2, column=1, pady=2)
    tk.Label(lbf, text='갱신주기(초)', bg=PANEL, fg=FG,
             font=('Arial', 9)).grid(row=3, column=0, sticky='w')
    lb_poll_var = tk.StringVar(value=str(cfg['lb_poll_sec']))
    tk.Entry(lbf, textvariable=lb_poll_var, width=12,
             font=('Arial', 9)).grid(row=3, column=1, pady=2)

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
        c['lb_enable'] = lb_enable_var.get()
        c['lb_period'] = lb_period_var.get()
        c['lb_stat'] = lb_stat_var.get()
        try:
            c['lb_top'] = int(float(lb_top_var.get()))
        except ValueError:
            c['lb_top'] = DEFAULTS['lb_top']
        try:
            c['lb_poll_sec'] = int(float(lb_poll_var.get()))
        except ValueError:
            c['lb_poll_sec'] = DEFAULTS['lb_poll_sec']
        c['per_coin'] = cfg.get('per_coin', {})
        return c

    def gui_log(m):
        log_q.put(str(m))

    # ---------- 코인별 설정 편집 팝업 ----------
    def open_per_coin():
        syms = [s.strip().upper() for s in vars_['symbols'].get().split(',') if s.strip()]
        if not syms:
            messagebox.showinfo("코인별 설정", "먼저 코인을 입력하세요.")
            return
        top = tk.Toplevel(root)
        top.title("🪙 코인별 개별 설정 (비우면 공통값 사용)")
        top.configure(bg=BG)
        top.geometry("720x360")
        hdr = ['코인'] + list(PER_COIN_KEYS)
        labels = ['코인', '고래기준$', '진입임계$', '진입금', '레버리지', 'TP%', 'SL%']
        for j, lab in enumerate(labels):
            tk.Label(top, text=lab, bg=BG, fg=ACC, font=('Arial', 9, 'bold'),
                     width=11).grid(row=0, column=j, padx=2, pady=4)
        cell_vars = {}
        pc = cfg.get('per_coin', {})
        for i, sym in enumerate(syms, start=1):
            tk.Label(top, text=sym, bg=BG, fg=FG,
                     font=('Arial', 9, 'bold')).grid(row=i, column=0, padx=2, pady=2)
            ov = pc.get(sym, {})
            for j, key in enumerate(PER_COIN_KEYS, start=1):
                val = ov.get(key, '')
                sv = tk.StringVar(value='' if val in (None, '') else str(val))
                tk.Entry(top, textvariable=sv, width=11,
                         font=('Arial', 9)).grid(row=i, column=j, padx=2, pady=2)
                cell_vars[(sym, key)] = sv
        tk.Label(top, text="※ 칸을 비우면 왼쪽 공통 설정값이 적용됩니다.",
                 bg=BG, fg='#888888', font=('Arial', 8)).grid(
            row=len(syms) + 1, column=0, columnspan=7, pady=6)

        def save_pc():
            newpc = {}
            for sym in syms:
                d = {}
                for key in PER_COIN_KEYS:
                    raw = cell_vars[(sym, key)].get().strip()
                    if raw == '':
                        continue
                    try:
                        d[key] = int(float(raw)) if key == 'leverage' else float(raw)
                    except ValueError:
                        pass
                if d:
                    newpc[sym] = d
            cfg['per_coin'] = newpc
            save_settings(collect())
            gui_log(f"🪙 코인별 설정 저장: {', '.join(newpc.keys()) or '(모두 공통값)'}")
            top.destroy()

        tk.Button(top, text="💾 저장", command=save_pc, bg='#00aa00', fg='#fff',
                  font=('Arial', 10, 'bold'), padx=12, pady=4).grid(
            row=len(syms) + 2, column=0, columnspan=7, pady=8)

    tk.Button(left, text="🪙 코인별 개별 설정…", command=open_per_coin,
              bg='#334455', fg='#fff', font=('Arial', 10, 'bold'),
              padx=10, pady=4).pack(pady=(8, 0))

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

    # ---------- 우측: 탭 (고래 흐름 / 리더보드) + 로그 ----------
    right = tk.Frame(root, bg=BG)
    right.pack(side='left', fill='both', expand=True, padx=(0, 8), pady=8)

    nb = ttk.Notebook(right)
    nb.pack(fill='both', expand=True)
    tab_flow = tk.Frame(nb, bg=BG)
    tab_lb = tk.Frame(nb, bg=BG)
    nb.add(tab_flow, text='  🐋 고래 흐름 · 내 포지션  ')
    nb.add(tab_lb, text='  🏆 리더보드 트레이더  ')

    style = ttk.Style()
    try:
        style.theme_use('clam')
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=24)
        style.configure('Treeview.Heading', background='#3d3d3d', foreground=FG)
    except Exception:
        pass

    # 탭1: 고래 흐름
    cols = ['코인', '상태', '현재가', '순흐름($)', '매수($)', '매도($)',
            '고래수', '진입가', '손익', 'ROI%']
    tree = ttk.Treeview(tab_flow, columns=cols, show='headings', height=10)
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=95 if c in ('순흐름($)', '매수($)', '매도($)') else 72,
                    anchor='center')
    tree.column('상태', width=120)
    tree.pack(fill='both', expand=True)

    # 탭2: 리더보드 (부모=트레이더, 자식=포지션)
    lb_cols = ['ROI%/방향', '누적손익/진입가', '관찰승률/ROE%', '관찰수/평가손익', '레버리지']
    lb_tree = ttk.Treeview(tab_lb, columns=lb_cols, show='tree headings', height=14)
    lb_tree.heading('#0', text='순위 · 트레이더 / ↳ 포지션')
    lb_tree.column('#0', width=240, anchor='w')
    for c in lb_cols:
        lb_tree.heading(c, text=c)
        lb_tree.column(c, width=120, anchor='center')
    lb_tree.tag_configure('long', foreground='#00ff88')
    lb_tree.tag_configure('short', foreground='#ff6666')
    lb_tree.tag_configure('trader', foreground=FG)
    lb_tree.pack(fill='both', expand=True)
    tk.Label(tab_lb, text="↳ 자식행 = 그 트레이더의 현재 공개 포지션 (어떻게 진입했는지) · "
             "승률은 이 프로그램이 청산을 관찰한 만큼만 집계",
             bg=BG, fg='#888888', font=('Arial', 8)).pack(anchor='w', padx=4, pady=(2, 0))

    # 로그
    tk.Label(right, text="📋 로그", bg=BG, fg=ACC,
             font=('Arial', 11, 'bold')).pack(anchor='w', pady=(8, 0))
    lf = tk.Frame(right, bg=BG)
    lf.pack(fill='both', expand=True)
    sb = tk.Scrollbar(lf)
    sb.pack(side='right', fill='y')
    log_text = tk.Text(lf, bg='#141414', fg='#ccc', font=('Consolas', 9), height=8,
                       yscrollcommand=sb.set, wrap='word')
    log_text.pack(side='left', fill='both', expand=True)
    sb.config(command=log_text.yview)

    def refresh_status():
        pass

    def _usd(v):
        return f"{v:+,.0f}" if v else '0'

    lb_open = set()   # 펼쳐둔 트레이더 uid 기억

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
            # 탭1 갱신
            for i in tree.get_children():
                tree.delete(i)
            for sym in bot[0]._symbols():
                st = bot[0].state.get(sym, {})
                tree.insert('', 'end', values=[
                    sym, st.get('sig', '-'),
                    f"{st.get('price', 0):,.4f}" if st.get('price') else '-',
                    _usd(st.get('net', 0)),
                    f"{st.get('buy', 0):,.0f}", f"{st.get('sell', 0):,.0f}",
                    st.get('cnt', 0),
                    f"{st.get('entry', 0):,.4f}" if st.get('entry') else '-',
                    f"{st.get('pnl', 0):+.2f}" if st.get('pos') else '-',
                    f"{st.get('roi', 0):+.2f}%" if st.get('pos') else '-',
                ])

            # 탭2 갱신 (리더보드)
            if bot[0].lb:
                # 펼침 상태 저장
                for iid in lb_tree.get_children():
                    uid = lb_tree.item(iid, 'tags')
                    if uid and lb_tree.item(iid, 'open'):
                        lb_open.add(iid)
                    elif iid in lb_open and not lb_tree.item(iid, 'open'):
                        lb_open.discard(iid)
                for i in lb_tree.get_children():
                    lb_tree.delete(i)
                for t in bot[0].lb.snapshot():
                    uid = t['uid']
                    wr = t.get('winrate')
                    wr_txt = f"{wr:.0f}% ({t.get('obs',0)})" if wr is not None else f"관찰중({t.get('obs',0)})"
                    shared = '' if t.get('shared') else ' 🔒'
                    parent = lb_tree.insert(
                        '', 'end', iid=uid, open=(uid in lb_open),
                        text=f"{t.get('rank','?')}. {t['nick']}{shared}",
                        tags=('trader',),
                        values=[f"ROI {t.get('roi',0):+.1f}%",
                                f"손익 {t.get('pnl',0):+,.0f}",
                                f"승률 {wr_txt}",
                                f"{len(t.get('positions',[]))}개 포지션", ''])
                    for p in t.get('positions', []):
                        side_txt = '🟢롱' if p['side'] == 'long' else '🔴숏'
                        lb_tree.insert(
                            parent, 'end',
                            text=f"   ↳ {p['symbol']}",
                            tags=(p['side'],),
                            values=[side_txt, f"진입 {p['entry']:,.4f}",
                                    f"ROE {p['roe']:+.1f}%",
                                    f"{p['pnl']:+,.0f}", f"{p['lev']:.0f}x"])
        root.after(1500, poll)

    paint_banner()
    gui_log("✅ 준비 완료. API 키 확인 후 [▶️ 시작]을 누르세요.")
    gui_log("   🪙 코인마다 기준이 다르면 [코인별 개별 설정]에서 따로 지정하세요.")
    gui_log("   🏆 [리더보드 트레이더] 탭에서 실명 고래의 포지션/ROI/관찰승률을 확인하세요.")
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
