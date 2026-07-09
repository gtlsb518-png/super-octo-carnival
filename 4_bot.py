#!/usr/bin/env python3
"""
트레이딩 봇 클래스 (참조용)

실제 봇 로직은 5_gui.py의 TradingBot 클래스에 통합되어 있습니다.
이 파일은 모듈 import 호환성을 위해 유지됩니다.
"""

import threading
import time
from datetime import datetime
import importlib

# 설정 import
_config = importlib.import_module('1_config')
FEE_RATE = _config.FEE_RATE

# 지표 import
_indicators = importlib.import_module('3_indicators')
Indicators = _indicators.Indicators


class TradingBot:
    """
    트레이딩 봇 클래스 (참조용)
    
    실제 사용되는 TradingBot은 5_gui.py에 있습니다.
    """
    def __init__(self, api, coin_config, log_callback, stats_callback, excel_callback=None):
        self.api = api
        self.config = coin_config
        self.log_callback = log_callback
        self.stats_callback = stats_callback
        self.excel_callback = excel_callback
        self.running = False
        
        if 'stats' not in self.config:
            self.config['stats'] = {
                'total_pnl': 0, 'long_count': 0, 'short_count': 0,
                'long_win': 0, 'short_win': 0, 'long_loss': 0, 'short_loss': 0,
                'long_profit': 0, 'short_profit': 0,
                'long_fee': 0, 'short_fee': 0, 'total_fee': 0
            }
    
    def log(self, msg, pos_type='LONG'):
        if self.log_callback:
            self.log_callback(msg, pos_type)
    
    def start(self):
        self.running = True
        print(f"[봇 시작] {self.config.get('symbol', 'UNKNOWN')}")
    
    def stop(self):
        self.running = False
        print(f"[봇 정지] {self.config.get('symbol', 'UNKNOWN')}")
