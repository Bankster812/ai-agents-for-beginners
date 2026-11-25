"""10 Systematic — data access (own fetcher; falls back to 08's src/multiasset/loader.py when present).

Sources: Yahoo chart API v8 (daily, adjclose incl. dividends), FRED CSV, Deribit DVOL.
Cache: data/systematic_cache/ (CSV per series). Re-fetch with refresh=True.
"""
import os, time, json, io, urllib.request, urllib.parse
import pandas as pd, numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CACHE = os.path.join(ROOT, 'data', 'systematic_cache')
os.makedirs(CACHE, exist_ok=True)
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_last = [0.0]

def _get(url, timeout=30):
    dt = time.time() - _last[0]
    if dt < 0.55: time.sleep(0.55 - dt)          # <= 2 req/s
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    for k in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if k == 3: raise
            time.sleep(2 * (k + 1))

def yahoo(ticker, start='1970-01-01', refresh=False):
    """Daily bars: index=date (tz-naive), cols close, adjclose, volume. adjclose = total-return proxy."""
    fn = os.path.join(CACHE, 'yh_' + ticker.replace('^', 'IDX_').replace('=', '_') + '.csv')
    if os.path.exists(fn) and not refresh:
        return pd.read_csv(fn, index_col=0, parse_dates=True)
    p1 = int(pd.Timestamp(start).timestamp()); p2 = int(time.time())
    url = (f'https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}'
           f'?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit&includeAdjustedClose=true')
    js = json.loads(_get(url))
    res = js['chart']['result'][0]
    ts = pd.to_datetime(res['timestamp'], unit='s')
    q = res['indicators']['quote'][0]
    adj = res['indicators'].get('adjclose', [{}])[0].get('adjclose', q['close'])
    df = pd.DataFrame({'open': q['open'], 'high': q['high'], 'low': q['low'], 'close': q['close'],
                       'adjclose': adj, 'volume': q['volume']}, index=ts.normalize())
    df = df[~df.index.duplicated(keep='last')].dropna(subset=['close'])
    df.index.name = 'date'
    df.to_csv(fn)
    return df

def fred(series, refresh=False):
    fn = os.path.join(CACHE, 'fred_' + series + '.csv')
    if os.path.exists(fn) and not refresh:
        s = pd.read_csv(fn, index_col=0, parse_dates=True).iloc[:, 0]
    else:
        raw = _get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}')
        s = pd.read_csv(io.BytesIO(raw), index_col=0, parse_dates=True, na_values=['.']).iloc[:, 0]
        s.to_csv(fn)
    return pd.to_numeric(s, errors='coerce').dropna().rename(series)

def deribit_dvol(currency='BTC', refresh=False):
    fn = os.path.join(CACHE, f'dvol_{currency}.csv')
    if os.path.exists(fn) and not refresh:
        return pd.read_csv(fn, index_col=0, parse_dates=True).iloc[:, 0]
    out = []; end = int(time.time() * 1000); start = int(pd.Timestamp('2021-03-01').timestamp() * 1000)
    while end > start:
        url = (f'https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={currency}'
               f'&start_timestamp={start}&end_timestamp={end}&resolution=1D')
        js = json.loads(_get(url))['result']
        d = js['data']
        if not d: break
        out += d
        if js.get('continuation') is None: break
        end = int(js['continuation'])
    df = pd.DataFrame(out, columns=['ts', 'o', 'h', 'l', 'c']).drop_duplicates('ts')
    s = pd.Series(df['c'].values, index=pd.to_datetime(df['ts'], unit='ms').dt.normalize(), name='dvol').sort_index()
    s.to_csv(fn)
    return s

# ---------------------------------------------------------------- universes
# Tradable ETF universe for T1/T5 (UCITS equivalents noted in brief); start ~2006 for most.
ETF_TREND = {
    # equities
    'SPY': 'eq', 'QQQ': 'eq', 'IWM': 'eq', 'EFA': 'eq', 'EEM': 'eq', 'EWJ': 'eq', 'VGK': 'eq',
    # bonds
    'TLT': 'bond', 'IEF': 'bond', 'SHY': 'bond', 'TIP': 'bond', 'LQD': 'bond', 'BWX': 'bond',
    # commodities
    'GLD': 'cmd', 'SLV': 'cmd', 'USO': 'cmd', 'DBA': 'cmd', 'DBB': 'cmd', 'UNG': 'cmd', 'DBC': 'cmd',
    # fx (vs USD)
    'FXE': 'fx', 'FXY': 'fx', 'FXB': 'fx', 'FXA': 'fx', 'FXC': 'fx', 'FXF': 'fx',
    # real estate / crypto
    'VNQ': 'eq', 'BTC-USD': 'crypto',
}
# Long-history proxies (mutual funds / indices) for pre-2006 regime test of TSMOM.
LONG_PROXY = {
    'VFINX': 'eq', 'VEURX': 'eq', 'VPACX': 'eq', 'VEIEX': 'eq', 'NAESX': 'eq',
    'VUSTX': 'bond', 'VFITX': 'bond', 'VWEHX': 'bond', 'VBMFX': 'bond',
    'GC=F': 'cmd', 'SI=F': 'cmd', 'CL=F': 'cmd', 'HG=F': 'cmd', 'ZC=F': 'cmd', 'ZW=F': 'cmd', 'ZS=F': 'cmd', 'NG=F': 'cmd',
}
FUTURES_FRONT = ['ES=F', 'NQ=F', 'RTY=F', 'ZN=F', 'ZB=F', 'GC=F', 'CL=F', '6E=F', '6J=F', 'BTC=F']
VOL = ['^VIX', '^VIX3M', '^VIX9D', '^VVIX', '^SPX', '^GSPC']
FRED_SERIES = ['DGS3MO', 'DTB3', 'DGS10', 'DGS2', 'DEXUSEU', 'DEXJPUS', 'DEXUSUK', 'DEXUSAL', 'DEXCAUS', 'DEXSZUS',
               'DEXUSNZ', 'DEXNOUS', 'DEXSDUS', 'DEXMXUS', 'DEXBZUS', 'DEXINUS', 'DEXKOUS', 'DEXSFUS',
               'IR3TIB01DEM156N', 'IR3TIB01EZM156N', 'IR3TIB01JPM156N', 'IR3TIB01GBM156N', 'IR3TIB01AUM156N',
               'IR3TIB01CAM156N', 'IR3TIB01CHM156N', 'IR3TIB01NZM156N', 'IR3TIB01NOM156N', 'IR3TIB01SEM156N',
               'IR3TIB01MXM156N', 'IR3TIB01ZAM156N', 'IR3TIB01USM156N', 'IR3TIB01KRM156N', 'IR3TIB01INM156N',
               'IR3TIB01BRM156N', 'BAMLH0A0HYM2', 'DEXUSEU']

def panel(tickers, field='adjclose', start=None):
    d = {}
    for t in tickers:
        try:
            df = yahoo(t)
            d[t] = df[field]
        except Exception as e:
            print('WARN', t, e)
    p = pd.DataFrame(d).sort_index()
    if start: p = p.loc[start:]
    return p

def cash_rate_daily():
    """US 3m T-bill (annual decimal), daily ffill. Used as financing / cash return."""
    r = fred('DTB3') / 100
    return r

if __name__ == '__main__':
    import sys
    for t in list(ETF_TREND) + list(LONG_PROXY) + FUTURES_FRONT + VOL:
        try:
            df = yahoo(t); print(t, df.index[0].date(), df.index[-1].date(), len(df))
        except Exception as e:
            print('FAIL', t, e)
    for s in sorted(set(FRED_SERIES)):
        try:
            x = fred(s); print(s, x.index[0].date(), x.index[-1].date(), len(x))
        except Exception as e:
            print('FAIL', s, e)
    try:
        x = deribit_dvol('BTC'); print('DVOL BTC', x.index[0].date(), x.index[-1].date(), len(x))
        x = deribit_dvol('ETH'); print('DVOL ETH', x.index[0].date(), x.index[-1].date(), len(x))
    except Exception as e:
        print('FAIL dvol', e)
