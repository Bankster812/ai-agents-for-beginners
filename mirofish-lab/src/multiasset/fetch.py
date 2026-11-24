"""Multi-asset raw fetchers (08 Multi-Asset Data Engineer).

Stages (python3 src/multiasset/fetch.py <stage>):
  yahoo     ETFs/indices, S&P100, futures (=F), FX (=X), vol indices, crypto (Yahoo)  -> data/raw/multiasset/yahoo/*.json
  penny     candidate list from nasdaqtrader symbol directory + Yahoo fetch           -> data/raw/multiasset/yahoo/*.json
  binance   daily top-10 + hourly BTC/ETH since 2018                                  -> data/raw/multiasset/binance/*.json
  deribit   BTC/ETH DVOL daily                                                        -> data/raw/multiasset/deribit/*.json
  fred      rates / VIXCLS / OECD 3m interbank                                        -> data/raw/multiasset/fred/*.csv
  all       everything
All raw responses are cached; re-running only fetches missing files. Build step: src/multiasset/build.py.

API notes (verified 2026-11-24):
- Yahoo v8 chart: query2 works with a desktop-browser UA + `Accept: application/json`; a bare UA got HTTP 429.
  period1=0 returns max history. `close` is SPLIT-adjusted only; `adjclose` is split+dividend adjusted.
  Timestamps are bar-open times; convert with meta.exchangeTimezoneName to get the session date.
- Yahoo has NO history for delisted tickers, and re-used tickers (e.g. BBBY today = Beyond Inc, 2026) show only the new
  issuer -> survivorship bias. Expired single futures contracts are not served; =F is a rolling front-month (unadjusted).
- Binance data-api.binance.vision klines: 1000 bars/request, UTC bars.
- Deribit get_volatility_index_data: DVOL since 2021-03-24, paginate via `continuation`.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "multiasset"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})

# ----------------------------------------------------------------------------- universes
ETF_INDEX = ["SPY", "QQQ", "IWM", "EFA", "EEM", "EWG", "EXS1.DE", "^GDAXI", "^GSPC", "^NDX", "^RUT", "^DJI",
             "^STOXX50E", "^N225",
             "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU",
             "TLT", "IEF", "SHY", "HYG", "LQD", "GLD", "SLV", "USO", "UNG", "DBC", "CPER", "CORN", "SOYB", "WEAT"]
# S&P 100 constituents as of 2025/26 (TODAY's list -> survivorship bias; see brief).
SP100 = """AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY BRK-B C CAT CHTR CL CMCSA
COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR FDX GD GE GILD GM GOOG GOOGL GS HD HON IBM INTC INTU ISRG JNJ JPM
KO LIN LLY LMT LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NOW NVDA ORCL PEP PFE PG PLTR PM
PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UBER UNH UNP UPS USB V VZ WFC WMT XOM""".split()
FUTURES = ["ES=F", "NQ=F", "RTY=F", "YM=F", "ZN=F", "ZB=F", "GC=F", "SI=F", "HG=F", "CL=F", "NG=F",
           "ZC=F", "ZS=F", "ZW=F", "6E=F", "6J=F", "6B=F"]
FX = ["EURUSD=X", "GBPUSD=X", "JPY=X", "AUDUSD=X", "CHF=X", "CAD=X", "EURCHF=X", "DX-Y.NYB"]
VOL = ["^VIX", "^VIX3M", "^VIX9D", "^VVIX", "^SKEW", "^VXN", "^OVX", "^GVZ"]
CRYPTO_YH = ["BTC-USD", "ETH-USD", "SOL-USD"]
BINANCE_DAILY = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "TRXUSDT", "ADAUSDT",
                 "LINKUSDT", "AVAXUSDT"]
BINANCE_HOURLY = ["BTCUSDT", "ETHUSDT"]
# Known delisted / ticker-reused penny & small names used to DEMONSTRATE missing history (not part of the sample).
DELISTED_PROBE = ["BBBY", "RIDE", "ELMS", "FSR", "LUNR", "NKLA", "WKHS", "CHK", "SPCE", "MULN", "FFIE", "GOEV",
                  "EXPR", "SDC", "BIRD", "VLDR", "ATVI", "TWTR", "SIVB", "FRC", "LEH", "WAMUQ", "ENRNQ", "BLIAQ",
                  "YRCW", "HTZGQ", "JCPNQ", "SHLDQ"]
FRED = ["DGS3MO", "DTB3", "DGS2", "DGS10", "DFF", "VIXCLS", "SOFR", "ECBESTRVOLWGTTRMDMNRT",
        "IR3TIB01EZM156N", "IR3TIB01GBM156N", "IR3TIB01JPM156N", "IR3TIB01AUM156N", "IR3TIB01CHM156N",
        "IR3TIB01CAM156N", "IR3TIB01USM156N", "DEXUSEU", "DEXJPUS", "DCOILWTICO", "BAMLH0A0HYM2"]

_last = [0.0]


def _get(url, params=None, tries=6, min_gap=0.55, timeout=30):
    for k in range(tries):
        dt = time.time() - _last[0]
        if dt < min_gap:
            time.sleep(min_gap - dt)
        _last[0] = time.time()
        try:
            r = S.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:  # network hiccup
            print("  net error", e, file=sys.stderr)
            time.sleep(2 ** k)
            continue
        if r.status_code == 200:
            return r
        if r.status_code in (404, 400, 422):
            return r
        wait = min(60, 2 ** (k + 1)) + random.random()
        print(f"  HTTP {r.status_code} {url[:80]} -> sleep {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
    return None


def yahoo_path(sym):
    return RAW / "yahoo" / (sym.replace("^", "IDX_").replace("=", "_").replace("/", "_") + ".json")


def fetch_yahoo(sym, force=False):
    p = yahoo_path(sym)
    if p.exists() and not force:
        return True
    p.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
    r = _get(url, params={"period1": 0, "period2": int(time.time()), "interval": "1d",
                          "events": "div,split", "includeAdjustedClose": "true"})
    if r is None:
        print("  FAILED", sym)
        return False
    try:
        d = r.json()
    except ValueError:
        print("  bad json", sym)
        return False
    p.write_text(json.dumps(d))
    ok = bool(d.get("chart", {}).get("result"))
    print(f"  {sym}: {'ok' if ok else 'NO DATA'} ({r.status_code})")
    return ok


def stage_yahoo():
    for s in ETF_INDEX + SP100 + FUTURES + FX + VOL + CRYPTO_YH + DELISTED_PROBE:
        fetch_yahoo(s)


def penny_candidates(n=420, seed=8):
    """Current US common stocks on Nasdaq Capital Market / NYSE American / deficient-status lists (likely small/penny)."""
    out = RAW / "penny_candidates.json"
    if out.exists():
        return json.loads(out.read_text())
    rows = []
    for url, kind in [("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "nasdaq"),
                      ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "other")]:
        txt = _get(url).text
        (RAW / f"symdir_{kind}.txt").write_text(txt)
        lines = txt.strip().splitlines()
        hdr = lines[0].split("|")
        for ln in lines[1:]:
            f = dict(zip(hdr, ln.split("|")))
            if "File Creation Time" in ln:
                continue
            sym = f.get("Symbol") or f.get("ACT Symbol")
            name = f.get("Security Name", "")
            if f.get("ETF") == "Y" or f.get("Test Issue") == "Y" or not sym:
                continue
            bad = any(w in name.lower() for w in ["warrant", " right", "unit", "preferred", "depositary", "notes",
                                                  "debenture", "% ", "trust", "fund", "etn", "acquisition"])
            if bad or any(c in sym for c in ".$+="):
                continue
            if kind == "nasdaq":
                small = f.get("Market Category") == "S" or f.get("Financial Status") in ("D", "E", "Q", "G", "H")
            else:
                small = f.get("Exchange") == "A"  # NYSE American
            if small:
                rows.append({"symbol": sym.replace(".", "-"), "name": name, "src": kind,
                             "fin_status": f.get("Financial Status", "")})
    random.Random(seed).shuffle(rows)
    rows = rows[:n]
    out.write_text(json.dumps(rows))
    return rows


def stage_penny():
    cands = penny_candidates()
    print(f"penny candidates: {len(cands)}")
    for c in cands:
        fetch_yahoo(c["symbol"])


def stage_binance():
    d = RAW / "binance"
    d.mkdir(parents=True, exist_ok=True)
    url = "https://data-api.binance.vision/api/v3/klines"
    jobs = [(s, "1d", 1500000000000) for s in BINANCE_DAILY] + [(s, "1h", 1514764800000) for s in BINANCE_HOURLY]
    for sym, iv, start in jobs:
        p = d / f"{sym}_{iv}.json"
        if p.exists():
            continue
        allk, t = [], start
        while True:
            r = _get(url, params={"symbol": sym, "interval": iv, "startTime": t, "limit": 1000}, min_gap=0.15)
            k = r.json() if r is not None else []
            if not k:
                break
            allk += k
            t = k[-1][0] + 1
            if len(k) < 1000:
                break
        p.write_text(json.dumps(allk))
        print(f"  binance {sym} {iv}: {len(allk)} bars")


def stage_deribit():
    d = RAW / "deribit"
    d.mkdir(parents=True, exist_ok=True)
    url = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
    for cur in ["BTC", "ETH"]:
        p = d / f"DVOL_{cur}.json"
        if p.exists():
            continue
        end = int(time.time() * 1000)
        rows = []
        while True:
            r = _get(url, params={"currency": cur, "start_timestamp": 1609459200000, "end_timestamp": end,
                                  "resolution": "1D"}, min_gap=0.2)
            res = r.json().get("result", {}) if r is not None else {}
            data = res.get("data", [])
            rows += data
            cont = res.get("continuation")
            if not data or not cont or cont >= end:
                break
            end = cont
        p.write_text(json.dumps(rows))
        print(f"  deribit DVOL {cur}: {len(rows)} rows")


def stage_fred():
    d = RAW / "fred"
    d.mkdir(parents=True, exist_ok=True)
    for sid in FRED:
        p = d / f"{sid}.csv"
        if p.exists():
            continue
        r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid}, min_gap=0.3)
        if r is not None and r.status_code == 200 and r.text.startswith("observation_date"):
            p.write_text(r.text)
            print(f"  fred {sid}: {r.text.count(chr(10))} lines")
        else:
            print(f"  fred {sid}: FAILED")


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "all"
    fns = {"yahoo": stage_yahoo, "penny": stage_penny, "binance": stage_binance, "deribit": stage_deribit,
           "fred": stage_fred}
    if st == "all":
        for name in ["binance", "deribit", "fred", "yahoo", "penny"]:
            print("== stage", name)
            fns[name]()
    else:
        fns[st]()
