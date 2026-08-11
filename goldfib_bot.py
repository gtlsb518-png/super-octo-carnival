#!/usr/bin/env python3
"""
황금 피보나치 자동매매 봇 (1시간봉 · 롱 전용) — 단독 실행형

🔥 이 파일 하나만 있으면 실행됩니다.
   실행:  python goldfib_bot.py     (더블클릭 가능)

전략 (백테스터 goldfib와 동일한 공식):
  진입(롱) = 20선 > 60선(정배열)
           AND 최근 3봉 중 직전봉이 최저(V바닥)
           AND 양봉이면서 5선을 상향 돌파
           AND 거래량 > 20봉 평균 × 1.1
  청산     = 진입 즉시 거래소에 TP(익절) + SL(손절) 주문을 걸어둠
             → 프로그램이 꺼져도 거래소가 알아서 청산

안전장치:
  · 기본 테스트넷(모의) 모드. 실거래는 체크 해제 필요
  · 완성된 1시간봉만 사용(리페인팅 없음), 같은 봉에서 중복 진입 차단
  · 코인당 동시 1포지션, 수량/가격은 거래소 규격(stepSize/tickSize)에 맞춤
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
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from urllib.parse import urlencode

# ==================== 라이브러리 자동 설치 ====================
for _mod in ['pandas', 'numpy', 'requests']:
    try:
        __import__(_mod)
    except ImportError:
        print(f"📦 {_mod} 설치 중...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', _mod],
                              stdout=subprocess.DEVNULL)

import pandas as pd  # noqa: E402
import requests  # noqa: E402

SETTINGS_FILE = 'goldfib_settings.json'
TRADE_LOG_CSV = 'goldfib_trades.csv'

# ==================== 기본 설정값 ====================
DEFAULTS = {
    'api_key': '',
    'api_secret': '',
    'testnet': True,        # True=테스트넷(모의), False=실거래
    'symbols': 'BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT',
    'interval': '1h',       # 매매 시간봉
    'amount': 50.0,         # 코인당 진입금 (USDT)
    'leverage': 3,          # 레버리지 (배)
    'tp_pct': 3.0,          # 익절 % (가격 기준)
    'sl_pct': 2.0,          # 손절 % (가격 기준)
    'vol_mult': 1.1,        # 거래량 조건 (평균20봉 × 배수)
    'max_positions': 3,     # 동시 보유 최대 포지션 수
    'check_sec': 30,        # 신호 확인 주기 (초)
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


# ==================== 바이낸스 선물 API ====================
class BinanceFutures:
    def __init__(self, key, secret, testnet=True, log=print):
        self.key, self.secret, self.log = key, secret, log
        self.base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        self.chart = "https://fapi.binance.com"      # 차트는 항상 메인넷 실시세
        self.s = requests.Session()
        self.s.headers.update({'X-MBX-APIKEY': key})
        self.offset = 0
        self.filters = {}
        self.sync_time()

    # ---------- 기본 ----------
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

    def klines(self, symbol, interval, limit=300):
        """차트는 메인넷 실시세 사용"""
        d = self._req('GET', '/fapi/v1/klines',
                      {'symbol': symbol, 'interval': interval, 'limit': limit},
                      base=self.chart)
        if not d:
            return None
        df = pd.DataFrame(d, columns=['ts', 'open', 'high', 'low', 'close', 'volume',
                                      'ct', 'qv', 'n', 'tb', 'tq', 'ig'])
        df = df[['ts', 'open', 'high', 'low', 'close', 'volume']].astype(float)
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        return df.set_index('ts')

    def position(self, symbol):
        d = self._req('GET', '/fapi/v2/positionRisk', {'symbol': symbol}, signed=True)
        if not d:
            return None
        for p in d:
            amt = float(p['positionAmt'])
            if amt != 0:
                return {'amt': amt, 'entry': float(p['entryPrice']),
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

    def market_buy(self, symbol, qty_str):
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': 'BUY', 'type': 'MARKET',
                          'quantity': qty_str}, signed=True)

    def close_market(self, symbol, qty_str):
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                          'quantity': qty_str, 'reduceOnly': 'true'}, signed=True)

    def place_tp(self, symbol, stop_price):
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': 'SELL', 'type': 'TAKE_PROFIT_MARKET',
                          'stopPrice': stop_price, 'closePosition': 'true',
                          'workingType': 'CONTRACT_PRICE'}, signed=True)

    def place_sl(self, symbol, stop_price):
        return self._req('POST', '/fapi/v1/order',
                         {'symbol': symbol, 'side': 'SELL', 'type': 'STOP_MARKET',
                          'stopPrice': stop_price, 'closePosition': 'true',
                          'workingType': 'CONTRACT_PRICE'}, signed=True)

    def cancel_all(self, symbol):
        return self._req('DELETE', '/fapi/v1/allOpenOrders', {'symbol': symbol}, signed=True)


# ==================== 황금피보 신호 (백테스터와 동일 공식) ====================
def goldfib_signal(df, vol_mult=1.1):
    """완성된 봉들만 담긴 df를 받아 '마지막 완성봉'에 매수신호가 있는지 반환"""
    close, low, opn, vol = df['close'], df['low'], df['open'], df['volume']
    m5, m20, m60 = close.rolling(5).mean(), close.rolling(20).mean(), close.rolling(60).mean()
    t_ok = m20 > m60                                        # 정배열
    is_low_pass = low.rolling(3).min() == low.shift(1)      # V바닥
    crossover = (close > m5) & (close.shift(1) <= m5.shift(1))
    conf_green = (close > opn) & crossover                  # 양봉 + 5선 상향돌파
    v_ok = vol > vol.rolling(20).mean() * vol_mult          # 거래량
    buy = (t_ok & is_low_pass & conf_green & v_ok).fillna(False)
    detail = {
        '정배열(20>60)': bool(t_ok.iloc[-1]),
        'V바닥': bool(is_low_pass.iloc[-1]),
        '양봉+5선돌파': bool(conf_green.iloc[-1]),
        '거래량OK': bool(v_ok.iloc[-1]),
    }
    return bool(buy.iloc[-1]), detail


# ==================== 트레이딩 봇 ====================
class GoldFibBot:
    def __init__(self, cfg, log, status_cb):
        self.cfg, self.log, self.status_cb = cfg, log, status_cb
        self.api = BinanceFutures(cfg['api_key'], cfg['api_secret'],
                                  cfg['testnet'], log)
        self.running = False
        self.last_bar = {}      # 심볼별 마지막 처리 봉 시각 (중복 진입 방지)
        self.state = {}         # 심볼별 상태 표시용

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _symbols(self):
        return [s.strip().upper() for s in self.cfg['symbols'].split(',') if s.strip()]

    def _open_count(self):
        n = 0
        for sym in self._symbols():
            if self.state.get(sym, {}).get('pos'):
                n += 1
        return n

    def _loop(self):
        mode = "테스트넷(모의)" if self.cfg['testnet'] else "🔴 실거래"
        self.log("=" * 46)
        self.log(f"🚀 황금피보 봇 시작 | {mode} | {self.cfg['interval']}봉")
        self.log(f"   코인: {', '.join(self._symbols())}")
        self.log(f"   진입금 {self.cfg['amount']} USDT × {self.cfg['leverage']}배"
                 f" | 익절 {self.cfg['tp_pct']}% | 손절 {self.cfg['sl_pct']}%")
        bal = self.api.balance()
        self.log(f"   잔고: {bal:,.2f} USDT")
        self.log("=" * 46)

        while self.running:
            try:
                for sym in self._symbols():
                    if not self.running:
                        break
                    self._check(sym)
                self.status_cb()
            except Exception as e:
                self.log(f"⚠️ 루프 오류: {e}")
            for _ in range(int(self.cfg['check_sec'])):
                if not self.running:
                    break
                time.sleep(1)
        self.log("⏹️ 봇 정지됨")

    def _check(self, sym):
        pos = self.api.position(sym)
        st = self.state.setdefault(sym, {})

        # 포지션 보유 중 — 거래소 TP/SL이 처리하므로 상태만 갱신
        if pos:
            st['pos'] = True
            st['entry'] = pos['entry']
            st['pnl'] = pos['pnl']
            roi = (pos['pnl'] / (self.cfg['amount'] or 1)) * 100
            st['roi'] = roi
            return

        if pos is False and st.get('pos'):
            # 방금 청산됨 (TP나 SL 체결)
            self.log(f"✅ [{sym}] 포지션 청산 완료 (거래소 TP/SL 체결)")
            self.api.cancel_all(sym)     # 남은 반대편 주문 정리
            st['pos'] = False
            st.pop('entry', None)
            st.pop('roi', None)
        st['pos'] = bool(pos)

        # 신호 확인 (완성된 봉만)
        df = self.api.klines(sym, self.cfg['interval'], limit=300)
        if df is None or len(df) < 250:
            st['sig'] = '데이터 부족'
            return
        closed = df.iloc[:-1]                 # 진행 중 봉 제외 = 리페인팅 방지
        bar_time = closed.index[-1]
        buy, detail = goldfib_signal(closed, self.cfg['vol_mult'])
        st['sig'] = '🟢 매수신호' if buy else '대기중'
        st['detail'] = detail
        st['price'] = float(closed['close'].iloc[-1])

        if not buy:
            return
        if self.last_bar.get(sym) == bar_time:
            return                            # 이미 이 봉에서 처리함
        if self._open_count() >= int(self.cfg['max_positions']):
            self.log(f"⏸️ [{sym}] 신호 발생했지만 최대 포지션({self.cfg['max_positions']}개) 도달")
            self.last_bar[sym] = bar_time
            return

        self.last_bar[sym] = bar_time
        self._enter(sym, float(closed['close'].iloc[-1]))

    def _enter(self, sym, price):
        self.log(f"🟢 [{sym}] 매수 신호! 진입 시도 (기준가 {price:,.4f})")
        self.api.cancel_all(sym)
        self.api.set_isolated(sym)
        self.api.set_leverage(sym, int(self.cfg['leverage']))

        raw_qty = self.cfg['amount'] * self.cfg['leverage'] / price
        qty_str, qty, min_qty, min_notional = self.api.fmt_qty(sym, raw_qty)
        if qty < min_qty or qty * price < min_notional:
            self.log(f"❌ [{sym}] 주문금액 부족 (수량 {qty}, 최소 {min_qty} / "
                     f"최소금액 {min_notional} USDT) → 진입금이나 레버리지를 올리세요")
            return

        order = self.api.market_buy(sym, qty_str)
        if not order:
            self.log(f"❌ [{sym}] 진입 주문 실패")
            return

        fill = float(order.get('avgPrice') or 0) or price
        self.log(f"✅ [{sym}] 진입 완료! 체결가 {fill:,.4f} | 수량 {qty_str}")

        tp = self.api.fmt_price(sym, fill * (1 + self.cfg['tp_pct'] / 100))
        sl = self.api.fmt_price(sym, fill * (1 - self.cfg['sl_pct'] / 100))
        ok_tp = self.api.place_tp(sym, tp)
        ok_sl = self.api.place_sl(sym, sl)
        self.log(f"   🎯 익절 주문 {tp} ({'등록' if ok_tp else '실패'})"
                 f" | 🛡️ 손절 주문 {sl} ({'등록' if ok_sl else '실패'})")
        if not ok_sl:
            self.log(f"   ⚠️ [{sym}] 손절 주문 실패! 수동으로 확인하세요")
        self._record(sym, fill, qty, tp, sl)
        self.state.setdefault(sym, {})['pos'] = True

    def _record(self, sym, price, qty, tp, sl):
        try:
            new = not os.path.exists(TRADE_LOG_CSV)
            with open(TRADE_LOG_CSV, 'a', encoding='utf-8-sig') as f:
                if new:
                    f.write('시각,코인,진입가,수량,익절가,손절가,진입금,레버리지\n')
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{sym},{price},{qty},"
                        f"{tp},{sl},{self.cfg['amount']},{self.cfg['leverage']}\n")
        except Exception:
            pass

    def close_all(self):
        """모든 포지션 즉시 시장가 청산"""
        for sym in self._symbols():
            pos = self.api.position(sym)
            if pos:
                qty_str, _, _, _ = self.api.fmt_qty(sym, abs(pos['amt']))
                self.api.cancel_all(sym)
                r = self.api.close_market(sym, qty_str)
                self.log(f"{'✅' if r else '❌'} [{sym}] 강제 청산 "
                         f"(손익 {pos['pnl']:+.2f} USDT)")
                self.state.setdefault(sym, {})['pos'] = False
        self.status_cb()


# ==================== GUI ====================
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    BG, PANEL, FG, ACC = '#1e1e1e', '#2d2d2d', '#ffffff', '#00ff88'
    cfg = load_settings()
    log_q = queue.Queue()
    bot = [None]

    root = tk.Tk()
    root.title("🏆 황금피보 자동매매 봇 (1시간봉 · 롱 전용)")
    root.geometry("1080x760")
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
        ('코인 (쉼표로 구분)', 'symbols', 26), ('진입금 (USDT)', 'amount', 12),
        ('레버리지 (배)', 'leverage', 12), ('익절 TP %', 'tp_pct', 12),
        ('손절 SL %', 'sl_pct', 12), ('거래량 배수', 'vol_mult', 12),
        ('최대 포지션 수', 'max_positions', 12), ('확인 주기 (초)', 'check_sec', 12),
    ]
    for r, (label, key, w) in enumerate(rows):
        tk.Label(form, text=label, bg=PANEL, fg=FG, font=('Arial', 9),
                 anchor='w').grid(row=r, column=0, sticky='w', pady=2)
        v = tk.StringVar(value=str(cfg.get(key, '')))
        show = '*' if 'secret' in key or 'key' in key else None
        tk.Entry(form, textvariable=v, width=w, font=('Arial', 9),
                 show=show).grid(row=r, column=1, padx=6, pady=2)
        vars_[key] = v

    tk.Label(form, text='시간봉', bg=PANEL, fg=FG, font=('Arial', 9),
             anchor='w').grid(row=len(rows), column=0, sticky='w', pady=2)
    interval_var = tk.StringVar(value=cfg['interval'])
    ttk.Combobox(form, textvariable=interval_var, width=10, state='readonly',
                 values=['15m', '30m', '1h', '2h', '4h']).grid(row=len(rows), column=1,
                                                               padx=6, pady=2)

    testnet_var = tk.BooleanVar(value=cfg['testnet'])
    tk.Checkbutton(left, text="테스트넷(모의거래) 사용", variable=testnet_var,
                   bg=PANEL, fg='#00ffff', selectcolor=BG, activebackground=PANEL,
                   font=('Arial', 10, 'bold'),
                   command=lambda: (cfg.update(testnet=testnet_var.get()),
                                    paint_banner())).pack(anchor='w', padx=10, pady=6)

    tk.Label(left, text="※ 진입 조건 (자동)", bg=PANEL, fg='#ffaa00',
             font=('Arial', 9, 'bold')).pack(anchor='w', padx=10)
    for t in ["  · 20선 > 60선 (정배열)", "  · 최근 3봉 중 직전봉 최저 (V바닥)",
              "  · 양봉 + 5선 상향돌파", "  · 거래량 > 20봉평균 × 배수"]:
        tk.Label(left, text=t, bg=PANEL, fg='#888888',
                 font=('Arial', 8)).pack(anchor='w', padx=10)

    def collect():
        c = dict(cfg)
        for k, v in vars_.items():
            raw = v.get().strip()
            if k in ('api_key', 'api_secret', 'symbols'):
                c[k] = raw
            else:
                try:
                    c[k] = int(float(raw)) if k in ('leverage', 'max_positions',
                                                    'check_sec') else float(raw)
                except ValueError:
                    c[k] = DEFAULTS[k]
                    v.set(str(DEFAULTS[k]))
        c['interval'] = interval_var.get()
        c['testnet'] = testnet_var.get()
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
        bot[0] = GoldFibBot(cfg, gui_log, refresh_status)
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
    tk.Label(right, text="📊 코인별 상태", bg=BG, fg=ACC,
             font=('Arial', 12, 'bold')).pack(anchor='w')

    cols = ['코인', '상태', '현재가', '진입가', '평가손익', 'ROI%', '조건']
    style = ttk.Style()
    try:
        style.theme_use('clam')
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=24)
        style.configure('Treeview.Heading', background='#3d3d3d', foreground=FG)
    except Exception:
        pass
    tree = ttk.Treeview(right, columns=cols, show='headings', height=8)
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=150 if c == '조건' else 90, anchor='center')
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

    def poll():
        try:
            while True:
                msg = log_q.get_nowait()
                log_text.insert('end', f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                log_text.see('end')
        except queue.Empty:
            pass
        if bot[0]:
            for i in tree.get_children():
                tree.delete(i)
            for sym, st in bot[0].state.items():
                cond = st.get('detail', {})
                cond_txt = ''.join('✅' if v else '❌' for v in cond.values()) if cond else '-'
                tree.insert('', 'end', values=[
                    sym,
                    '📈 보유중' if st.get('pos') else st.get('sig', '-'),
                    f"{st.get('price', 0):,.4f}" if st.get('price') else '-',
                    f"{st.get('entry', 0):,.4f}" if st.get('entry') else '-',
                    f"{st.get('pnl', 0):+.2f}" if st.get('pos') else '-',
                    f"{st.get('roi', 0):+.2f}%" if st.get('pos') else '-',
                    cond_txt,
                ])
        root.after(1000, poll)

    paint_banner()
    gui_log("✅ 준비 완료. API 키 확인 후 [▶️ 시작]을 누르세요.")
    gui_log(f"   현재 설정: {cfg['interval']}봉 | 진입금 {cfg['amount']} USDT × "
            f"{cfg['leverage']}배 | 익절 {cfg['tp_pct']}% | 손절 {cfg['sl_pct']}%")
    gui_log("   조건 표시 순서: 정배열 / V바닥 / 양봉+5선돌파 / 거래량")
    poll()
    root.protocol("WM_DELETE_WINDOW", lambda: (stop(), save_settings(collect()),
                                               root.destroy()))
    root.mainloop()


if __name__ == '__main__':
    try:
        launch_gui()
    except ImportError:
        print("⚠️ tkinter(GUI)가 없습니다. Windows는 python.org 설치본을 쓰세요.")
        input("\n[엔터]로 종료...")
    except Exception as e:
        import traceback
        print("\n❌ 오류 발생:")
        traceback.print_exc()
        print(f"\n요약: {e}")
        input("\n[엔터]로 종료...")
