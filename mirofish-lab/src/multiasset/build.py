"""Build processed multi-asset datasets from the raw cache (run after fetch.py).

    python3 src/multiasset/build.py

Outputs in data/multiasset/ (all long format, gzip CSV, dates = exchange-local session dates):
  etf_index.csv.gz  sp100.csv.gz  penny.csv.gz   date,symbol,open,high,low,close,volume,close_unadj,close_raw,adj_factor
  futures.csv.gz    date,symbol,open,high,low,close,volume,close_unadj,close_panama,roll_window,roll_flag,roll_gap
  fx.csv.gz         date,symbol,open,high,low,close
  vol.csv.gz        date,symbol,open,high,low,close       (^VIX family, DVOL_BTC/ETH, VIXCLS from FRED)
  crypto_daily.csv.gz   date,symbol,open,high,low,close,volume   (Binance *USDT and Yahoo *-USD)
  crypto_hourly.csv.gz  ts_utc,symbol,open,high,low,close,volume (Binance BTCUSDT/ETHUSDT 1h since 2018)
  rates.csv.gz      date,series,value  (FRED; percent p.a.)
  corporate_actions.csv.gz date,symbol,type,value
  meta.csv          one row per symbol (+ futures contract specs, micro equivalents)
  quality.json      per-symbol QA stats used by data_quality.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch as F  # noqa: E402

ROOT = F.ROOT
RAW = F.RAW
OUT = ROOT / "data" / "multiasset"
OUT.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------------- futures specs
# point_value = USD P&L per 1.0 move of the Yahoo-quoted price for ONE contract.
FUT_SPECS = {
    # sym: name, exch, cycle, point_value, tick, micro, micro_point_value, proxy, roll_rule
    "ES=F": ("E-mini S&P 500", "CME", "HMUZ", 50, 0.25, "MES", 5, "^GSPC", "q_eq"),
    "NQ=F": ("E-mini Nasdaq-100", "CME", "HMUZ", 20, 0.25, "MNQ", 2, "^NDX", "q_eq"),
    "RTY=F": ("E-mini Russell 2000", "CME", "HMUZ", 50, 0.10, "M2K", 5, "^RUT", "q_eq"),
    "YM=F": ("E-mini Dow ($5)", "CBOT", "HMUZ", 5, 1.0, "MYM", 0.5, "^DJI", "q_eq"),
    "ZN=F": ("10Y T-Note", "CBOT", "HMUZ", 1000, 1 / 64, None, None, "IEF", "q_bond"),
    "ZB=F": ("30Y T-Bond", "CBOT", "HMUZ", 1000, 1 / 32, None, None, "TLT", "q_bond"),
    "GC=F": ("Gold", "COMEX", "GJMQVZ", 100, 0.10, "MGC", 10, "GLD", "phys"),
    "SI=F": ("Silver", "COMEX", "HKNUZ", 5000, 0.005, "SIL", 1000, "SLV", "phys"),
    "HG=F": ("Copper", "COMEX", "HKNUZ", 25000, 0.0005, "MHG", 2500, "CPER", "phys"),
    "CL=F": ("WTI Crude", "NYMEX", "FGHJKMNQUVXZ", 1000, 0.01, "MCL", 100, "USO", "cl"),
    "NG=F": ("Henry Hub Nat Gas", "NYMEX", "FGHJKMNQUVXZ", 10000, 0.001, "MNG", 1000, "UNG", "ng"),
    "ZC=F": ("Corn (cents/bu)", "CBOT", "HKNUZ", 50, 0.25, "MZC", 5, "CORN", "grain"),
    "ZS=F": ("Soybeans (cents/bu)", "CBOT", "FHKNQUX", 50, 0.25, "MZS", 5, "SOYB", "grain"),
    "ZW=F": ("Chicago Wheat (cents/bu)", "CBOT", "HKNUZ", 50, 0.25, "MZW", 5, "WEAT", "grain"),
    "6E=F": ("Euro FX", "CME", "HMUZ", 125000, 0.00005, "M6E", 12500, "EURUSD=X", "q_fx"),
    "6J=F": ("Japanese Yen", "CME", "HMUZ", 12500000, 0.0000005, "MJY", 1250000, "JPY=X", "q_fx"),
    "6B=F": ("British Pound", "CME", "HMUZ", 62500, 0.0001, "M6B", 6250, "GBPUSD=X", "q_fx"),
}
MONTH_CODE = dict(zip("FGHJKMNQUVXZ", range(1, 13)))

# approx. daily bar close time in UTC (winter / standard time); used for cross-market lag rules
CLOSE_UTC = {"America/New_York": "21:00 (20:00 summer)", "Europe/Berlin": "16:30 (15:30 summer)",
             "Europe/London": "~23:00 (FX bar = London calendar day)", "Asia/Tokyo": "06:00",
             "UTC": "00:00 (next day)", "America/Chicago": "21:00 (20:00 summer)"}


# ----------------------------------------------------------------------------- yahoo parsing
def parse_yahoo(sym):
    p = F.yahoo_path(sym)
    if not p.exists():
        return None, None, None
    d = json.loads(p.read_text())
    res = (d.get("chart") or {}).get("result")
    if not res:
        return None, None, None
    r = res[0]
    meta = r["meta"]
    ts = r.get("timestamp")
    if not ts:
        return None, meta, None
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({k: q.get(k) for k in ["open", "high", "low", "close", "volume"]})
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    df["adjclose"] = adj if adj is not None else df["close"]
    tz = meta.get("exchangeTimezoneName", "UTC")
    df["date"] = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz).tz_localize(None).normalize()
    df = df.dropna(subset=["close"]).drop_duplicates("date", keep="last").sort_values("date")
    df = df.astype({c: float for c in ["open", "high", "low", "close", "volume", "adjclose"]})
    ev = r.get("events", {})
    acts = []
    for k, v in (ev.get("splits") or {}).items():
        acts.append({"ts": v["date"], "type": "split", "value": v["numerator"] / v["denominator"]})
    for k, v in (ev.get("dividends") or {}).items():
        acts.append({"ts": v["date"], "type": "dividend", "value": v["amount"]})
    acts = pd.DataFrame(acts)
    if len(acts):
        acts["date"] = pd.to_datetime(acts["ts"], unit="s", utc=True).dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
        acts["symbol"] = sym
        acts = acts[["date", "symbol", "type", "value"]]
    return df.reset_index(drop=True), meta, acts


def equity_frame(sym):
    df, meta, acts = parse_yahoo(sym)
    if df is None or df.empty:
        return None, meta, acts
    df["symbol"] = sym
    # raw (as-traded) close: undo Yahoo split adjustment
    raw_mult = np.ones(len(df))
    if acts is not None and len(acts):
        for _, a in acts[acts.type == "split"].iterrows():
            raw_mult[df["date"].values < np.datetime64(a["date"])] *= a["value"]
    df["close_unadj"] = df["close"]
    df["close_raw"] = df["close"] * raw_mult
    fac = (df["adjclose"] / df["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    df["adj_factor"] = fac
    for c in ["open", "high", "low"]:
        df[c] = df[c] * fac
    df["close"] = df["adjclose"]
    return df[["date", "symbol", "open", "high", "low", "close", "volume", "close_unadj", "close_raw",
               "adj_factor"]], meta, acts


# ----------------------------------------------------------------------------- QA helpers
def qa_stats(df, sym, price_col="close"):
    s = df.set_index("date")[price_col]
    out = {"symbol": sym, "first": str(s.index.min().date()), "last": str(s.index.max().date()), "n": int(len(s))}
    pos = s[s > 0]
    out["n_nonpositive"] = int((s <= 0).sum())
    lr = np.log(pos).diff().dropna()
    mad = (lr - lr.rolling(63, min_periods=20).median()).abs().rolling(63, min_periods=20).median() * 1.4826
    z = (lr / mad.shift(1)).abs()
    nxt = lr.shift(-1)
    spike = (z > 10) & (np.sign(nxt) == -np.sign(lr)) & (nxt.abs() > 0.7 * lr.abs())
    out["n_spike_reversals"] = int(spike.sum())
    out["spike_dates"] = [str(x.date()) for x in lr.index[spike]][:10]
    out["n_absret_gt_25pct"] = int((lr.abs() > np.log(1.25)).sum())
    if {"high", "low"}.issubset(df.columns):
        h, l, c = df["high"], df["low"], df[price_col]
        out["n_ohlc_inconsistent"] = int(((h < l) | (c > h * 1.0001) | (c < l * 0.9999)).sum())
    if "volume" in df.columns:
        out["frac_zero_volume"] = round(float((df["volume"].fillna(0) == 0).mean()), 4)
    stale = (s.diff() == 0)
    runs = stale.groupby((~stale).cumsum()).sum()
    out["max_stale_run"] = int(runs.max()) if len(runs) else 0
    gaps = s.index.to_series().diff().dt.days.dropna()
    out["n_gaps_gt_5d"] = int((gaps > 5).sum())
    out["max_gap_days"] = int(gaps.max()) if len(gaps) else 0
    out["max_gap_end"] = str(gaps.idxmax().date()) if len(gaps) else ""
    return out


def clean_ohlc(df, price_col="close"):
    """Drop non-positive closes (except futures, handled separately); repair OHLC envelope."""
    df = df.copy()
    for c in ["open", "high", "low"]:
        if c in df:
            df[c] = df[c].where(df[c] > 0)
    if {"open", "high", "low"}.issubset(df.columns):
        df["high"] = df[["high", "open", price_col]].max(axis=1)
        df["low"] = df[["low", "open", price_col]].min(axis=1)
    return df


def write(df, name):
    p = OUT / name
    df.to_csv(p, index=False, compression="gzip", float_format="%.8g")
    print(f"wrote {p.relative_to(ROOT)}: {len(df):,} rows, {df.iloc[:, 1].nunique()} symbols")


# ----------------------------------------------------------------------------- futures roll handling
def roll_windows(dates, rule, cycle):
    """Boolean array: date lies in the window in which Yahoo's =F series switches to the next contract.

    Yahoo rolls its continuous front-month around expiry / first-notice, NOT on a volume roll. Windows are
    deliberately wide; inside each window we pick the single day with the largest proxy-residual.
    Returns window id per date (-1 = not in a window)."""
    wid = np.full(len(dates), -1)
    months = [MONTH_CODE[c] for c in cycle]
    years = range(dates.min().year - 1, dates.max().year + 2)
    k = 0
    for y in years:
        for m in months:
            first = pd.Timestamp(y, m, 1)
            # 3rd Friday of contract month
            fri = first + pd.offsets.WeekOfMonth(week=2, weekday=4) if first.weekday() != 4 else first + pd.Timedelta(days=14)
            if rule in ("q_eq", "q_fx"):
                lo, hi = fri - pd.Timedelta(days=12), fri + pd.Timedelta(days=3)
            elif rule == "q_bond":   # first notice = last biz day of month before; LTD ~ 7 bd before month end
                lo, hi = first - pd.Timedelta(days=12), first + pd.Timedelta(days=24)
            elif rule == "phys":     # COMEX: FND = last biz day of prior month
                lo, hi = first - pd.Timedelta(days=12), first + pd.Timedelta(days=3)
            elif rule == "cl":       # CL expires ~3 bd before the 25th of the PRIOR month
                pm = first - pd.offsets.MonthBegin(1)
                lo, hi = pm + pd.Timedelta(days=13), pm + pd.Timedelta(days=24)
            elif rule == "ng":       # NG expires 3 bd before first day of contract month
                lo, hi = first - pd.Timedelta(days=10), first + pd.Timedelta(days=1)
            elif rule == "grain":    # FND end of prior month; LTD business day before the 15th
                lo, hi = first - pd.Timedelta(days=12), first + pd.Timedelta(days=15)
            else:
                continue
            msk = (dates >= lo) & (dates <= hi) & (wid == -1)
            wid[msk] = k
            k += 1
    return wid


def build_futures(prox_close):
    frames, qa, meta_rows = [], [], []
    for sym, spec in FUT_SPECS.items():
        name, exch, cycle, pv, tick, micro, mpv, proxy, rule = spec
        df, meta, _ = parse_yahoo(sym)
        if df is None:
            print("no futures data", sym)
            continue
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        df["symbol"] = sym
        qa.append(qa_stats(df, sym))
        dates = df["date"]
        c = df["close"].values
        ret = pd.Series(np.where((c[1:] > 0) & (c[:-1] > 0), np.log(np.abs(c[1:]) / np.abs(c[:-1])), np.nan))
        ret = pd.concat([pd.Series([np.nan]), ret], ignore_index=True)
        # proxy residual: fut log-ret minus beta * proxy log-ret (beta from rolling 250d regression)
        px = prox_close.get(proxy)
        if px is not None:
            pr = np.log(px).diff().reindex(dates).values
        else:
            pr = np.full(len(df), np.nan)
        pr = pd.Series(pr)
        cov = (ret * pr).rolling(250, min_periods=60).mean() - ret.rolling(250, min_periods=60).mean() * pr.rolling(250, min_periods=60).mean()
        var = pr.rolling(250, min_periods=60).var()
        beta = (cov / var).shift(1).clip(0, 2).fillna(1.0)
        resid = ret - beta * pr
        has_proxy = pr.notna()
        scale = resid.abs().rolling(250, min_periods=40).median().shift(1) * 1.4826
        zr = (resid / scale)
        wid = roll_windows(dates, rule, cycle)
        df["roll_window"] = wid >= 0
        roll_flag = np.zeros(len(df), dtype=int)
        gap = np.zeros(len(df))
        n_win = n_det = 0
        for w in np.unique(wid[wid >= 0]):
            idx = np.where(wid == w)[0]
            idx = idx[has_proxy.values[idx] & zr.notna().values[idx]]
            if len(idx) == 0:
                continue
            n_win += 1
            j = idx[np.nanargmax(np.abs(zr.values[idx]))]
            if abs(zr.values[j]) >= 3.0:
                roll_flag[j] = 1
                gap[j] = resid.values[j]
                n_det += 1
        # ratio back-adjustment: divide out each detected gap; earlier prices scaled by cumulative factor
        # (only for positive-price segments; CL 2020-04-20 negative print kept as-is in close_unadj)
        fac_step = np.exp(gap)                  # price jump due to roll at day j  (new/old contract ratio)
        cum = np.cumprod(fac_step[::-1])[::-1]   # product of factors from day t..end
        back = np.append(cum[1:], 1.0)           # factors strictly after t
        # Prices on/after the last roll are unadjusted; before a roll on day j they are multiplied by exp(gap_j)
        df["close_unadj"] = df["close"]
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] * back * np.where(roll_flag == 1, 1.0, 1.0)
        # on a roll day itself the close is already new-contract; the open/high/low may be mixed -> leave as is
        # Panama (difference) adjustment: $-gap = close_t - close_{t-1}*exp(gap)... approximated as level gap
        lvl_gap = np.where(roll_flag == 1, df["close_unadj"].shift(1).fillna(0).values * (np.exp(gap) - 1), 0.0)
        cum_lvl = np.append(np.cumsum(lvl_gap[::-1])[::-1][1:], 0.0)
        df["close_panama"] = df["close_unadj"] + cum_lvl
        df["roll_flag"] = roll_flag
        df["roll_gap"] = np.round(gap, 6)
        frames.append(df[["date", "symbol", "open", "high", "low", "close", "volume", "close_unadj",
                          "close_panama", "roll_window", "roll_flag", "roll_gap"]])
        # signal-to-noise of detection: |z| on detected roll days vs typical
        meta_rows.append({"symbol": sym, "name": name, "exchange": exch, "roll_cycle": cycle,
                          "point_value_usd": pv, "tick_size": tick, "tick_value_usd": pv * tick,
                          "micro_symbol": micro, "micro_point_value_usd": mpv,
                          "micro_tick_value_usd": (mpv * tick if mpv else None),
                          "roll_proxy": proxy, "roll_windows_with_proxy": n_win, "rolls_detected": n_det,
                          "mean_abs_roll_gap": float(np.abs(gap[roll_flag == 1]).mean()) if n_det else 0.0,
                          "cum_roll_gap_logret": float(gap.sum()),
                          "proxy_first_date": str(px.index.min().date()) if px is not None else None})
    return pd.concat(frames, ignore_index=True), qa, pd.DataFrame(meta_rows)


# ----------------------------------------------------------------------------- main build
def main():
    meta_rows, qa_all, acts_all = [], [], []

    def add_meta(sym, universe, asset_class, meta, df, extra=None):
        tz = (meta or {}).get("exchangeTimezoneName", "UTC")
        row = {"symbol": sym, "universe": universe, "asset_class": asset_class,
               "name": (meta or {}).get("longName") or (meta or {}).get("shortName"),
               "currency": (meta or {}).get("currency"), "exchange": (meta or {}).get("fullExchangeName"),
               "tz": tz, "bar_close_utc": CLOSE_UTC.get(tz, tz),
               "first_date": str(df["date"].min().date()), "last_date": str(df["date"].max().date()),
               "n_rows": len(df)}
        row.update(extra or {})
        meta_rows.append(row)

    # --- equities / ETFs ---------------------------------------------------------------
    prox_close = {}
    eq_sets = [("etf_index", F.ETF_INDEX, "equity_etf_index"), ("sp100", F.SP100, "equity")]
    for uni, syms, ac in eq_sets:
        frames = []
        for s in syms:
            df, meta, acts = equity_frame(s)
            if df is None:
                print("missing", s)
                continue
            df = clean_ohlc(df)
            frames.append(df)
            prox_close[s] = df.set_index("date")["close"]
            qa_all.append({**qa_stats(df, s), "universe": uni})
            add_meta(s, uni, ac if not s.startswith("^") else "index", meta, df)
            if acts is not None and len(acts):
                acts_all.append(acts)
        write(pd.concat(frames, ignore_index=True), f"{uni}.csv.gz")

    # --- penny sample -------------------------------------------------------------------
    cands = F.penny_candidates()
    frames, pen_rows = [], []
    for c in cands:
        s = c["symbol"]
        df, meta, acts = equity_frame(s)
        info = {"symbol": s, "has_data": df is not None and len(df) > 0, "fin_status": c["fin_status"]}
        if df is not None and len(df):
            df = df[df["close_raw"] > 0]
            info.update({"n": len(df), "min_raw_close": float(df.close_raw.min()),
                         "last_raw_close": float(df.close_raw.iloc[-1]),
                         "first_date": str(df.date.min().date()),
                         "n_reverse_splits": int(((acts is not None) and len(acts) and
                                                  ((acts.type == "split") & (acts.value < 1)).sum()) or 0),
                         "n_splits": int(((acts is not None) and len(acts) and (acts.type == "split").sum()) or 0)})
        pen_rows.append(info)
    pen = pd.DataFrame(pen_rows)
    elig = pen[(pen.has_data) & (pen.n >= 252) & (pen.min_raw_close < 5)].copy()
    elig = elig.sample(n=min(200, len(elig)), random_state=8).sort_values("symbol")
    pen["in_sample"] = pen.symbol.isin(elig.symbol)
    pen.to_csv(OUT / "penny_candidates_screen.csv", index=False)
    for s in elig.symbol:
        df, meta, acts = equity_frame(s)
        df = clean_ohlc(df[df["close_raw"] > 0])
        df["penny_flag"] = (df["close_raw"] < 5).astype(int)  # as-traded price < $5 that day (point-in-time)
        frames.append(df)
        qa_all.append({**qa_stats(df, s), "universe": "penny"})
        add_meta(s, "penny", "equity_penny", meta, df)
        if acts is not None and len(acts):
            acts_all.append(acts)
    write(pd.concat(frames, ignore_index=True), "penny.csv.gz")

    # delisted probe
    probe = []
    for s in F.DELISTED_PROBE:
        df, meta, _ = parse_yahoo(s)
        probe.append({"symbol": s, "yahoo_has_data": df is not None and len(df) > 0,
                      "first_date": str(df.date.min().date()) if df is not None and len(df) else None,
                      "last_date": str(df.date.max().date()) if df is not None and len(df) else None,
                      "name_now": (meta or {}).get("longName")})
    pd.DataFrame(probe).to_csv(OUT / "delisted_probe.csv", index=False)

    # --- FX / vol / crypto (Yahoo) -----------------------------------------------------
    for uni, syms, ac in [("fx", F.FX, "fx"), ("vol", F.VOL, "vol_index"), ("crypto_yahoo", F.CRYPTO_YH, "crypto")]:
        frames = []
        for s in syms:
            df, meta, _ = parse_yahoo(s)
            if df is None:
                print("missing", s)
                continue
            df = clean_ohlc(df[df.close > 0])[["date", "open", "high", "low", "close", "volume"]]
            df.insert(1, "symbol", s)
            frames.append(df)
            prox_close[s] = df.set_index("date")["close"]
            qa_all.append({**qa_stats(df, s), "universe": uni})
            add_meta(s, uni if uni != "crypto_yahoo" else "crypto", ac, meta, df)
        if uni == "fx":
            write(pd.concat(frames, ignore_index=True).drop(columns="volume"), "fx.csv.gz")
        elif uni == "vol":
            vol_frames = frames
        else:
            cry_yh = frames

    # Deribit DVOL
    for cur in ["BTC", "ETH"]:
        p = RAW / "deribit" / f"DVOL_{cur}.json"
        if p.exists():
            d = pd.DataFrame(json.loads(p.read_text()), columns=["ts", "open", "high", "low", "close"])
            d["date"] = pd.to_datetime(d.ts, unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
            d = d.drop_duplicates("date").sort_values("date")
            d["symbol"] = f"DVOL_{cur}"
            d["volume"] = np.nan
            d = d[["date", "symbol", "open", "high", "low", "close", "volume"]]
            vol_frames.append(d)
            qa_all.append({**qa_stats(d, f"DVOL_{cur}"), "universe": "vol"})
            add_meta(f"DVOL_{cur}", "vol", "vol_index", {"exchangeTimezoneName": "UTC", "longName":
                     f"Deribit {cur} implied vol index (30d)", "currency": "vol pts", "fullExchangeName": "Deribit"}, d)

    # FRED
    rates = []
    for sid in F.FRED:
        p = RAW / "fred" / f"{sid}.csv"
        if not p.exists():
            continue
        r = pd.read_csv(p, na_values=["."])
        r.columns = ["date", "value"]
        r["date"] = pd.to_datetime(r["date"])
        r = r.dropna()
        r["series"] = sid
        rates.append(r[["date", "series", "value"]])
    rates = pd.concat(rates, ignore_index=True)
    vix = rates[rates.series == "VIXCLS"].rename(columns={"value": "close"})
    vix = vix.assign(symbol="VIXCLS", open=np.nan, high=np.nan, low=np.nan, volume=np.nan)
    vol_frames.append(vix[["date", "symbol", "open", "high", "low", "close", "volume"]])
    add_meta("VIXCLS", "vol", "vol_index", {"exchangeTimezoneName": "America/New_York", "longName": "FRED VIX close",
             "currency": "vol pts", "fullExchangeName": "FRED"}, vix)
    write(pd.concat(vol_frames, ignore_index=True).drop(columns="volume"), "vol.csv.gz")
    write(rates[rates.series != "VIXCLS"], "rates.csv.gz")

    # Binance
    cry = []
    for s in F.BINANCE_DAILY:
        p = RAW / "binance" / f"{s}_1d.json"
        if not p.exists():
            continue
        k = pd.DataFrame(json.loads(p.read_text())).iloc[:, :6]
        k.columns = ["ts", "open", "high", "low", "close", "volume"]
        k = k.astype(float)
        k["date"] = pd.to_datetime(k.ts, unit="ms").dt.normalize()
        k = k[k.date < pd.Timestamp.utcnow().tz_localize(None).normalize()]  # drop incomplete current bar
        k["symbol"] = s
        k = k[["date", "symbol", "open", "high", "low", "close", "volume"]]
        cry.append(k)
        qa_all.append({**qa_stats(k, s), "universe": "crypto"})
        add_meta(s, "crypto", "crypto", {"exchangeTimezoneName": "UTC", "longName": f"Binance spot {s}",
                 "currency": "USDT", "fullExchangeName": "Binance"}, k)
    write(pd.concat(cry + cry_yh, ignore_index=True), "crypto_daily.csv.gz")
    hr = []
    for s in F.BINANCE_HOURLY:
        k = pd.DataFrame(json.loads((RAW / "binance" / f"{s}_1h.json").read_text())).iloc[:, :6]
        k.columns = ["ts", "open", "high", "low", "close", "volume"]
        k = k.astype(float)
        k["ts_utc"] = pd.to_datetime(k.ts, unit="ms")
        k["symbol"] = s
        hr.append(k[["ts_utc", "symbol", "open", "high", "low", "close", "volume"]])
        gaps = k.ts_utc.diff().dt.total_seconds().div(3600).dropna()
        qa_all.append({"symbol": s + "_1h", "universe": "crypto_hourly", "first": str(k.ts_utc.min()),
                       "last": str(k.ts_utc.max()), "n": len(k), "n_missing_hours": int((gaps - 1).clip(lower=0).sum()),
                       "max_gap_hours": float(gaps.max())})
    write(pd.concat(hr, ignore_index=True), "crypto_hourly.csv.gz")

    # Futures
    fut, fqa, fmeta = build_futures(prox_close)
    write(fut, "futures.csv.gz")
    for q in fqa:
        q["universe"] = "futures"
    qa_all += fqa
    for sym in fut.symbol.unique():
        df, meta, _ = parse_yahoo(sym)
        add_meta(sym, "futures", "future", meta, fut[fut.symbol == sym])
    meta = pd.DataFrame(meta_rows)
    meta = meta.merge(fmeta.drop(columns=["name", "exchange"]), on="symbol", how="left")
    meta.to_csv(OUT / "meta.csv", index=False)
    print("wrote meta.csv", len(meta))
    pd.concat(acts_all, ignore_index=True).to_csv(OUT / "corporate_actions.csv.gz", index=False, compression="gzip")
    (OUT / "quality.json").write_text(json.dumps(qa_all, indent=1, default=str))


if __name__ == "__main__":
    main()
