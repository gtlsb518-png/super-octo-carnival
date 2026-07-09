#!/usr/bin/env python3
"""
바이낸스 API 모듈
메인넷 차트 + 테스트넷/메인넷 주문
"""

import requests
import time
import hashlib
import hmac
from urllib.parse import urlencode
import pandas as pd
import importlib

# 설정 import
_config = importlib.import_module('1_config')
API_KEY = _config.API_KEY
API_SECRET = _config.API_SECRET
TESTNET = _config.TESTNET
MAX_ORDER_RETRY = _config.MAX_ORDER_RETRY


class BinanceAPI:
    def __init__(self, api_key, api_secret, testnet=True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        # 주문/포지션용 URL (테스트넷/메인넷)
        self.base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        
        # 차트 데이터용 URL (항상 메인넷! - 실제 바이낸스 선물 가격)
        self.chart_url = "https://fapi.binance.com"
        
        self.session = requests.Session()
        self.session.headers.update({'X-MBX-APIKEY': self.api_key})
        
        # 시간 오프셋 초기화
        self.time_offset = 0
        self._sync_time()
        
        # precision 캐시
        self._precision_cache = {}
    
    def _sync_time(self):
        """바이낸스 서버 시간과 동기화"""
        try:
            response = self.session.get(f"{self.base_url}/fapi/v1/time", timeout=10)
            server_time = response.json()['serverTime']
            local_time = int(time.time() * 1000)
            self.time_offset = server_time - local_time
            print(f"⏰ 시간 동기화 완료: 오프셋 {self.time_offset}ms")
        except Exception as e:
            print(f"⚠️ 시간 동기화 실패: {e}")
            self.time_offset = 0
    
    def _sign(self, params):
        query_string = urlencode(params)
        signature = hmac.new(self.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        params['signature'] = signature
        return params
    
    def _request(self, method, endpoint, params=None, signed=False):
        url = f"{self.base_url}{endpoint}"
        if params is None:
            params = {}
        if signed:
            params['timestamp'] = int(time.time() * 1000) + self.time_offset
            params = self._sign(params)
        try:
            if method == 'GET':
                response = self.session.get(url, params=params, timeout=10)
            elif method == 'POST':
                response = self.session.post(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                try:
                    error_data = e.response.json()
                    if error_data.get('code') == -1021:
                        print(f"⚠️ 시간 오류 감지! 재동기화 중...")
                        self._sync_time()
                    print(f"API 오류: {error_data}")
                except (ValueError, KeyError) as parse_error:
                    print(f"API 오류: {e.response.text}")
            return None
        except requests.exceptions.Timeout:
            print(f"API 타임아웃: {endpoint}")
            return None
        except requests.exceptions.ConnectionError:
            print(f"API 연결 오류: {endpoint}")
            return None
        except Exception as e:
            print(f"API 오류: {e}")
            return None
    
    def get_balance(self):
        account = self._request('GET', '/fapi/v2/account', signed=True)
        if account:
            return float(account.get('totalWalletBalance', 0))
        return 0.0
    
    def get_klines(self, symbol, interval, limit=200):
        """차트 데이터는 항상 메인넷에서 가져옴"""
        params = {'symbol': symbol.replace('/', ''), 'interval': interval, 'limit': limit}
        url = f"{self.chart_url}/fapi/v1/klines"
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                klines = response.json()
                df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                                   'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                                   'taker_buy_quote', 'ignore'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                df.set_index('timestamp', inplace=True)
                return df
        except Exception as e:
            print(f"차트 데이터 오류: {e}")
        return None
    
    def set_leverage(self, symbol, leverage):
        params = {'symbol': symbol.replace('/', ''), 'leverage': leverage}
        return self._request('POST', '/fapi/v1/leverage', params=params, signed=True) is not None
    
    def set_margin_type(self, symbol, margin_type='ISOLATED'):
        params = {'symbol': symbol.replace('/', ''), 'marginType': margin_type}
        try:
            result = self._request('POST', '/fapi/v1/marginType', params=params, signed=True)
            return result is not None
        except:
            return True  # 이미 설정되어 있으면 True
    
    def get_symbol_precision(self, symbol):
        symbol_clean = symbol.replace('/', '')
        if symbol_clean in self._precision_cache:
            return self._precision_cache[symbol_clean]
        
        precision_map = {
            'BTCUSDT': 3, 'ETHUSDT': 3, 'BNBUSDT': 2, 'SOLUSDT': 1, 'XRPUSDT': 1,
            'ADAUSDT': 1, 'DOGEUSDT': 0, 'TRXUSDT': 0, 'TONUSDT': 1, 'LINKUSDT': 2,
            'AVAXUSDT': 1, 'DOTUSDT': 1, 'LTCUSDT': 2, 'MATICUSDT': 0, 'SHIBUSDT': 0,
        }
        
        precision = precision_map.get(symbol_clean, 2)
        self._precision_cache[symbol_clean] = precision
        return precision
    
    def calculate_quantity(self, symbol, amount, leverage, price):
        precision = self.get_symbol_precision(symbol)
        qty = (amount * leverage) / price
        
        if precision == 0:
            qty = int(round(qty))
            if qty < 1:
                qty = 1
            min_qty = 1
        else:
            qty = round(qty, precision)
            min_qty = 10 ** (-precision)
        
        return qty, min_qty, precision
    
    def create_order(self, symbol, side, quantity):
        symbol_clean = symbol.replace('/', '')
        precision = self.get_symbol_precision(symbol)
        
        if precision == 0:
            quantity = int(round(quantity))
        else:
            quantity = round(quantity, precision)
        
        min_qty = 10 ** (-precision) if precision > 0 else 1
        if quantity < min_qty:
            print(f"주문 수량 부족: {quantity} < {min_qty}")
            return None
        
        params = {
            'symbol': symbol_clean,
            'side': side.upper(),
            'type': 'MARKET',
            'quantity': quantity
        }
        
        for attempt in range(MAX_ORDER_RETRY):
            result = self._request('POST', '/fapi/v1/order', params=params, signed=True)
            if result is not None:
                return result
            if attempt < MAX_ORDER_RETRY - 1:
                wait_time = (attempt + 1) * 2
                print(f"주문 재시도 {attempt + 2}/{MAX_ORDER_RETRY} ({wait_time}초 후)...")
                time.sleep(wait_time)
        
        print(f"주문 실패: {symbol} {side} {quantity}")
        return None
    
    def get_order(self, symbol, order_id):
        params = {'symbol': symbol.replace('/', ''), 'orderId': order_id}
        return self._request('GET', '/fapi/v1/order', params=params, signed=True)
    
    def get_position(self, symbol):
        positions = self._request('GET', '/fapi/v2/positionRisk', signed=True)
        if positions:
            for pos in positions:
                if pos['symbol'] == symbol.replace('/', '') and float(pos['positionAmt']) != 0:
                    entry_price = float(pos['entryPrice'])
                    mark_price = float(pos.get('markPrice', entry_price))
                    leverage = int(pos.get('leverage', 1))
                    pnl = float(pos['unRealizedProfit'])
                    
                    # 🔥 ROI % 계산 (바이낸스 공식!)
                    position_amt = abs(float(pos['positionAmt']))
                    initial_margin = (entry_price * position_amt) / leverage
                    if initial_margin > 0:
                        roi_pct = (pnl / initial_margin) * 100
                    else:
                        roi_pct = 0
                    
                    return {
                        'side': 'long' if float(pos['positionAmt']) > 0 else 'short',
                        'amount': position_amt,
                        'entry_price': entry_price,
                        'mark_price': mark_price,
                        'pnl': pnl,
                        'roi_pct': roi_pct,  # 🔥 바이낸스 ROI %
                        'leverage': leverage
                    }
        return None
    
    def close_position(self, symbol):
        position = self.get_position(symbol)
        if position:
            side = 'SELL' if position['side'] == 'long' else 'BUY'
            return self.create_order(symbol, side, position['amount'])
        return True
