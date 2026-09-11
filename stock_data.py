#!/usr/bin/env python3
"""
일봉 데이터 수집 모듈

야후 파이낸스 차트 API(무료, 키 불필요)로 일봉을 받아 CSV로 캐시합니다.
막혀 있는 망에서는 stooq(미국·일부 해외)로 자동 폴백하고,
그래도 안 되면 직접 받은 CSV 파일을 읽어 쓸 수 있습니다.
"""

import os
import time
import importlib
from datetime import datetime, timedelta

import pandas as pd
import requests

_config = importlib.import_module('stock_config')

HEADERS = {
    # 야후는 User-Agent 없으면 429/403을 줍니다
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}

YAHOO_HOSTS = ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']


def normalize_symbol(ticker):
    """
    입력 티커를 야후 심볼 후보 리스트로 변환

    '005930'  -> ['005930.KS', '005930.KQ']   (코스피 먼저, 실패 시 코스닥)
    '005930.KQ' -> ['005930.KQ']
    'AAPL'    -> ['AAPL']
    """
    t = ticker.strip().upper()
    if '.' in t:
        return [t]
    if t.isdigit() and len(t) == 6:
        return [f"{t}.KS", f"{t}.KQ"]
    return [t]


def _cache_path(ticker):
    os.makedirs(_config.CACHE_DIR, exist_ok=True)
    safe = ticker.replace('.', '_').replace('/', '_')
    return os.path.join(_config.CACHE_DIR, f"{safe}.csv")


def _read_cache(ticker):
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    age_h = (time.time() - os.path.getmtime(path)) / 3600
    if age_h > _config.CACHE_HOURS:
        return None
    try:
        return load_csv(path)
    except Exception:
        return None


def _fetch_yahoo(symbol, years):
    """야후 차트 API에서 일봉 가져오기"""
    params = {
        'range': f'{years}y',
        'interval': '1d',
        'includeAdjustedClose': 'true',
    }
    last_err = None
    for host in YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{symbol}"
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            js = r.json()
            result = (js.get('chart') or {}).get('result')
            if not result:
                last_err = (js.get('chart') or {}).get('error') or '빈 응답'
                continue
            res = result[0]
            ts = res.get('timestamp')
            if not ts:
                last_err = '일봉 데이터 없음'
                continue
            q = res['indicators']['quote'][0]
            df = pd.DataFrame({
                'date': pd.to_datetime(ts, unit='s').tz_localize(None).normalize(),
                'open': q.get('open'),
                'high': q.get('high'),
                'low': q.get('low'),
                'close': q.get('close'),
                'volume': q.get('volume'),
            })
            return _clean(df)
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"야후 실패({symbol}): {last_err}")


def _fetch_stooq(symbol):
    """stooq 폴백 (미국: aapl.us / 한국 종목은 지원 안 되는 경우가 많음)"""
    s = symbol.lower()
    if s.endswith('.ks') or s.endswith('.kq'):
        s = s.replace('.ks', '.kr').replace('.kq', '.kr')
    elif '.' not in s:
        s = f"{s}.us"
    url = f"https://stooq.com/q/d/l/?s={s}&i=d"
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200 or 'Date' not in r.text[:200]:
        raise RuntimeError(f"stooq 실패({s})")
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip().lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return _clean(df[['date', 'open', 'high', 'low', 'close', 'volume']])


def _clean(df):
    """결측 봉 제거 + 정렬 + 타입 정리"""
    df = df.dropna(subset=['close']).copy()
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col not in df.columns:
            df[col] = float('nan')
        df[col] = pd.to_numeric(df[col], errors='coerce')
    # 거래정지 등으로 거래량 0인 봉은 가격이 안 움직여 통계를 왜곡합니다
    df = df[df['close'] > 0]
    df = df.drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)
    return df[['date', 'open', 'high', 'low', 'close', 'volume']]


def load_csv(path):
    """직접 받은 CSV 읽기 (date,open,high,low,close,volume 컬럼 필요)"""
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if 'date' not in df.columns:
        raise ValueError(f"{path}: 'date' 컬럼이 없습니다")
    df['date'] = pd.to_datetime(df['date'])
    return _clean(df)


def get_daily(ticker, years=None, use_cache=True, quiet=False):
    """
    일봉 DataFrame 반환 (date, open, high, low, close, volume)

    캐시 → 야후 → stooq 순으로 시도합니다.
    """
    years = years or _config.HISTORY_YEARS

    if use_cache:
        cached = _read_cache(ticker)
        if cached is not None and len(cached) > 0:
            if not quiet:
                print(f"  [캐시] {ticker}: {len(cached)}봉")
            return cached

    errors = []
    for symbol in normalize_symbol(ticker):
        for fetcher, name in ((lambda: _fetch_yahoo(symbol, years), '야후'),
                              (lambda: _fetch_stooq(symbol), 'stooq')):
            try:
                df = fetcher()
                if len(df) < 50:
                    errors.append(f"{name}/{symbol}: 봉 {len(df)}개뿐")
                    continue
                df.to_csv(_cache_path(ticker), index=False)
                if not quiet:
                    print(f"  [{name}] {ticker}({symbol}): {len(df)}봉 "
                          f"{df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
                return df
            except Exception as e:
                errors.append(str(e))

    raise RuntimeError(
        f"{ticker} 데이터를 못 받았습니다.\n    - " + "\n    - ".join(errors) +
        "\n    ※ 회사망·방화벽에서 막히는 경우가 많습니다. "
        "그럴 땐 CSV를 직접 받아 --csv 옵션으로 쓰세요."
    )


if __name__ == '__main__':
    import sys
    for t in (sys.argv[1:] or _config.WATCH_LIST):
        try:
            df = get_daily(t, use_cache=False)
            print(df.tail(3).to_string(index=False))
        except Exception as e:
            print(f"  [실패] {e}")
