#!/usr/bin/env python3
"""
종가 기반 신호 모듈

모든 신호는 '그날 종가까지의 정보만' 사용합니다 (미래 참조 없음).
각 신호는 boolean Series를 돌려주며, True인 날의 '다음날' 수익률로 검증합니다.

direction
    'up'   : 다음날 상승을 기대하는 신호 (매수 후보)
    'down' : 다음날 하락을 기대하는 신호 (매도/관망 후보 — 공매도 권유 아님)
"""

import pandas as pd
import numpy as np


# ==================== 기본 지표 ====================

def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, period=14):
    """RSI (Wilder 방식) — 3_indicators.py의 ATR과 같은 RMA 평활"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def add_indicators(df):
    """검증·알림에서 공통으로 쓰는 지표들을 df에 붙여 반환"""
    d = df.copy()
    c = d['close']

    d['ma5'] = sma(c, 5)
    d['ma20'] = sma(c, 20)
    d['ma60'] = sma(c, 60)
    d['ma200'] = sma(c, 200)
    d['rsi14'] = rsi(c, 14)
    d['ret'] = c.pct_change()
    d['std20'] = c.rolling(20).std()
    d['bb_up'] = d['ma20'] + 2 * d['std20']
    d['bb_dn'] = d['ma20'] - 2 * d['std20']
    d['vol_ma20'] = d['volume'].rolling(20).mean()
    d['high20'] = c.rolling(20).max()
    d['low20'] = c.rolling(20).min()
    d['high250'] = c.rolling(250).max()
    d['dist_ma20'] = (c - d['ma20']) / d['ma20'] * 100   # MA20 이격도(%)

    # 연속 상승/하락 일수
    up = (d['ret'] > 0).astype(int)
    dn = (d['ret'] < 0).astype(int)
    d['up_streak'] = up * (up.groupby((up != up.shift()).cumsum()).cumcount() + 1)
    d['dn_streak'] = dn * (dn.groupby((dn != dn.shift()).cumsum()).cumcount() + 1)

    # 다음날 수익률 (검증 전용 — 신호 계산에는 절대 쓰지 않음)
    d['next_ret'] = c.shift(-1) / c - 1
    return d


# ==================== 신호 정의 ====================
# (이름, 설명, direction, 계산 함수)

def _golden_cross(d):
    return (d['ma5'] > d['ma20']) & (d['ma5'].shift(1) <= d['ma20'].shift(1))


def _dead_cross(d):
    return (d['ma5'] < d['ma20']) & (d['ma5'].shift(1) >= d['ma20'].shift(1))


SIGNALS = {
    'golden_cross':  ('MA5가 MA20을 상향 돌파(골든크로스)', 'up',   _golden_cross),
    'dead_cross':    ('MA5가 MA20을 하향 돌파(데드크로스)', 'down', _dead_cross),
    'rsi_oversold':  ('RSI14 < 30 (과매도)',                'up',   lambda d: d['rsi14'] < 30),
    'rsi_overbought':('RSI14 > 70 (과매수)',                'down', lambda d: d['rsi14'] > 70),
    'down3':         ('3일 연속 하락 마감',                  'up',   lambda d: d['dn_streak'] >= 3),
    'up3':           ('3일 연속 상승 마감',                  'down', lambda d: d['up_streak'] >= 3),
    'bb_lower':      ('종가가 볼린저 하단 이탈',             'up',   lambda d: d['close'] < d['bb_dn']),
    'bb_upper':      ('종가가 볼린저 상단 돌파',             'down', lambda d: d['close'] > d['bb_up']),
    'breakout20':    ('20일 종가 신고가',                    'up',   lambda d: d['close'] >= d['high20']),
    'breakdown20':   ('20일 종가 신저가',                    'down', lambda d: d['close'] <= d['low20']),
    'vol_spike_up':  ('거래량 2배 폭증 + 상승 마감',         'up',
                      lambda d: (d['volume'] > 2 * d['vol_ma20']) & (d['ret'] > 0)),
    'vol_spike_dn':  ('거래량 2배 폭증 + 하락 마감',         'down',
                      lambda d: (d['volume'] > 2 * d['vol_ma20']) & (d['ret'] < 0)),
    'gap_down5':     ('MA20 대비 -5% 이하로 이격',           'up',   lambda d: d['dist_ma20'] <= -5),
    'gap_up5':       ('MA20 대비 +5% 이상 이격',             'down', lambda d: d['dist_ma20'] >= 5),
    'trend_pullback':('장기 상승추세(>MA200) 중 RSI<40 눌림','up',
                      lambda d: (d['close'] > d['ma200']) & (d['rsi14'] < 40)),
    'big_drop':      ('하루 -3% 이상 급락',                  'up',   lambda d: d['ret'] <= -0.03),
    'big_jump':      ('하루 +3% 이상 급등',                  'down', lambda d: d['ret'] >= 0.03),
}


def compute_all(df):
    """
    지표 + 모든 신호를 계산해서 (지표 df, 신호 dict) 반환
    신호 dict: {이름: boolean Series}
    """
    d = add_indicators(df)
    flags = {}
    for name, (_desc, _dir, fn) in SIGNALS.items():
        s = fn(d)
        flags[name] = s.fillna(False).astype(bool)
    return d, flags


def describe(name):
    desc, direction, _ = SIGNALS[name]
    return desc, direction
