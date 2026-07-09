#!/usr/bin/env python3
"""기술적 지표 계산 모듈"""

import pandas as pd
import numpy as np


class Indicators:
    @staticmethod
    def atr(df, period):
        """ATR with RMA (Wilder's smoothing) - TradingView 방식"""
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()
    
    @staticmethod
    def ut_bot(df, sensitivity, atr_period):
        """UT Bot - TradingView Pine Script 완전 복제"""
        atr = Indicators.atr(df, atr_period)
        close = df['close'].values
        nLoss = (sensitivity * atr).values
        xATRTrailingStop = np.zeros(len(close))
        pos = np.zeros(len(close), dtype=int)
        
        xATRTrailingStop[0] = close[0] - nLoss[0]
        pos[0] = 1
        
        for i in range(1, len(close)):
            if np.isnan(nLoss[i]):
                xATRTrailingStop[i] = xATRTrailingStop[i-1]
                pos[i] = pos[i-1]
                continue
            
            prev_stop = xATRTrailingStop[i-1]
            curr_close = close[i]
            prev_close = close[i-1]
            
            if curr_close > prev_stop and prev_close > prev_stop:
                xATRTrailingStop[i] = max(prev_stop, curr_close - nLoss[i])
            elif curr_close < prev_stop and prev_close < prev_stop:
                xATRTrailingStop[i] = min(prev_stop, curr_close + nLoss[i])
            elif curr_close > prev_stop:
                xATRTrailingStop[i] = curr_close - nLoss[i]
            else:
                xATRTrailingStop[i] = curr_close + nLoss[i]
            
            if prev_close < xATRTrailingStop[i-1] and curr_close > xATRTrailingStop[i]:
                pos[i] = 1
            elif prev_close > xATRTrailingStop[i-1] and curr_close < xATRTrailingStop[i]:
                pos[i] = -1
            else:
                pos[i] = pos[i-1]
        
        return pd.Series(pos, index=df.index)
    
    @staticmethod
    def ema(df, fast, slow):
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        return ema_fast, ema_slow
    
    @staticmethod
    def get_signals(df, ut_sens, ut_atr, ema_fast, ema_slow):
        """
        🔥🔥🔥 트레이딩뷰와 동일한 신호 계산
        
        중요: df는 이미 df[:-1]로 완성된 봉만 전달받아야 함!
        - ut_pos.iloc[-1]: 마지막 완성된 봉의 UT 포지션
        - ut_pos.iloc[-2]: 그 이전 봉의 UT 포지션
        - 크로스: iloc[-2] → iloc[-1] 변화 감지
        """
        ut_pos = Indicators.ut_bot(df, ut_sens, ut_atr)
        fast, slow = Indicators.ema(df, ema_fast, ema_slow)
        
        ut_long_cross = False
        ut_short_cross = False
        ut_position_long = False
        ut_position_short = False
        
        if len(ut_pos) >= 2 and not pd.isna(ut_pos.iloc[-2]) and not pd.isna(ut_pos.iloc[-1]):
            # 🔥 마지막 완성된 봉 기준!
            prev_pos = ut_pos.iloc[-2]  # 이전 완성 봉
            curr_pos = ut_pos.iloc[-1]  # 마지막 완성 봉 (트레이딩뷰 신호 기준!)
            
            # 🔥 크로스 신호: 이전 봉 → 현재 봉 변화
            if prev_pos == -1 and curr_pos == 1:
                ut_long_cross = True
            elif prev_pos == 1 and curr_pos == -1:
                ut_short_cross = True
            
            # 🔥 현재 포지션 상태 (스위칭용)
            ut_position_long = (curr_pos == 1)
            ut_position_short = (curr_pos == -1)
        
        # 🔥 EMA 크로스 상태 (마지막 완성 봉 기준!)
        ema_long = fast.iloc[-1] > slow.iloc[-1]
        ema_short = fast.iloc[-1] < slow.iloc[-1]
        
        return {
            'ut_long': ut_long_cross,       # UT LONG 크로스 (진입 신호)
            'ut_short': ut_short_cross,     # UT SHORT 크로스 (진입 신호)
            'ut_position_long': ut_position_long,   # UT 현재 LONG 상태 (스위칭용)
            'ut_position_short': ut_position_short, # UT 현재 SHORT 상태 (스위칭용)
            'ema_long': ema_long,           # EMA 골든크로스 상태
            'ema_short': ema_short,         # EMA 데드크로스 상태
            'price': df['close'].iloc[-1],
            'ema_fast_val': fast.iloc[-1],
            'ema_slow_val': slow.iloc[-1]
        }
    
    @staticmethod
    def calculate_adx(df, period=14):
        """ADX 계산"""
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.ewm(alpha=1/period, adjust=False).mean()
            
            up_move = high - high.shift(1)
            down_move = low.shift(1) - low
            
            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
            
            plus_dm = pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean()
            minus_dm = pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean()
            
            plus_di = 100 * (plus_dm / atr)
            minus_di = 100 * (minus_dm / atr)
            
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            adx = dx.ewm(alpha=1/period, adjust=False).mean()
            
            current_adx = adx.iloc[-1]
            if pd.isna(current_adx):
                return 20.0
            return float(current_adx)
        except Exception as e:
            print(f"ADX 계산 오류: {e}")
            return 20.0
    
    @staticmethod
    def check_volume(df, volume_multiplier=1.5, ma_period=20):
        try:
            volume_ma = df['volume'].rolling(ma_period).mean()
            current_volume = df['volume'].iloc[-1]
            avg_volume = volume_ma.iloc[-1]
            
            if pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume == 0:
                return True
            return current_volume >= (avg_volume * volume_multiplier)
        except:
            return True
    
    @staticmethod
    def get_volume_info(df, ma_period=20):
        try:
            if 'volume' not in df.columns:
                return {'current': 0, 'average': 0, 'ratio': 0}
            
            volume_ma = df['volume'].rolling(ma_period).mean()
            current_volume = df['volume'].iloc[-1]
            avg_volume = volume_ma.iloc[-1]
            
            if pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume == 0:
                return {'current': 0, 'average': 0, 'ratio': 0}
            
            return {
                'current': float(current_volume),
                'average': float(avg_volume),
                'ratio': float(current_volume / avg_volume)
            }
        except:
            return {'current': 0, 'average': 0, 'ratio': 0}
