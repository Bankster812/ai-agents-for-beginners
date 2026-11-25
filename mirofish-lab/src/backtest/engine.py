"""Event-driven portfolio backtester for binary prediction-market tokens (Polymarket YES/NO).

Public API
----------
    run_backtest(signal_df, config=None, prices=None, markets=None) -> BacktestResult

signal_df columns
    market_id   int     Gamma market id (must exist in markets)
    ts          int     unix seconds UTC at which the signal is KNOWN (decision time)
    side        str     'YES' or 'NO' (we always BUY the chosen outcome token; no shorting on Polymarket)
    target_weight float  fraction of (book) equity to spend, e.g. 0.05     } give one of the two;
    size_usd    float   fixed USDC budget incl. fees                       } size_usd wins if both
    exit_rule   str     '|'-joined clauses, first one to trigger wins (default 'resolve'):
                          'resolve'       hold to resolution (payout $1/share if side won, else 0, at resolved_ts)
                          'hold:24h'      sell at the first bar with ts >= fill_ts + 24h   (also '3d', '90m')
                          'at:<unix>'     sell at the first bar with ts >= <unix>
                          'end:-24h'      sell at the first bar with ts >= scheduled end_ts - 24h
                          'tp:0.90'       held-token mid >= 0.90 at bar j -> sell at bar j+1
                          'sl:0.20'       held-token mid <= 0.20 at bar j -> sell at bar j+1
                        if no exit bar exists before resolution, the position resolves.
    strategy    str     optional label carried into the trade log

Timing (no same-bar look-ahead)
    * entry fills at the FIRST price bar with bar_ts > signal ts (strictly later), at that bar's displayed mid plus
      the cost model's half-spread + size impact. The bar must be < resolved_ts and within max_entry_delay_h.
    * time exits fill at the first bar >= target time; price-triggered exits fill one bar after the trigger bar.
    * resolution payout is credited at resolved_ts (capital is locked until then).

Portfolio accounting
    * cash starts at bankroll. Buying spends budget B = shares*px + fees. Selling credits shares*px - fees.
    * sizing equity = cash + cost basis of open positions ("book equity"; known at decision time, no MTM needed).
    * caps: B <= max_pos_frac*equity, B <= liq_cap_frac*volume_usd, open cost per event_id + B <= max_event_frac*equity,
      B <= cash. Orders below min_order_usd are skipped (reason logged).
    * one open position per market by default (later signals on that market are skipped while it is open).
    * daily equity curve = cash + sum(shares * held-token mid as-of each UTC day end) (or bid if config.mtm='bid').

Prices are YES-token mids; the NO token's mid is 1 - p_yes.
"""
from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .costs import load_default_cost_model

DAY = 86_400


# ----------------------------------------------------------------------------------------------------------------
# data containers
# ----------------------------------------------------------------------------------------------------------------
class PriceStore:
    """Per-market price paths as numpy arrays + market metadata (dict of dicts)."""

    META_COLS = ("volume_usd", "category", "is_short_auto", "event_id", "neg_risk", "end_ts", "resolved_ts",
                 "yes_won", "start_ts", "frac_hours_changed", "sample_weight", "split")

    def __init__(self, prices: pd.DataFrame, markets: pd.DataFrame):
        prices = prices.sort_values(["market_id", "ts"])
        mids = prices["market_id"].to_numpy()
        ts = prices["ts"].to_numpy(np.int64)
        p = prices["p_yes"].to_numpy(float)
        ak = prices["after_known"].to_numpy(np.int8) if "after_known" in prices else np.zeros(len(prices), np.int8)
        self.paths: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        if len(mids):
            cut = np.flatnonzero(np.diff(mids)) + 1
            starts = np.r_[0, cut]
            ends = np.r_[cut, len(mids)]
            for a, b in zip(starts, ends):
                self.paths[int(mids[a])] = (ts[a:b], p[a:b], ak[a:b])
        cols = [c for c in self.META_COLS if c in markets.columns]
        mk = markets[["market_id"] + cols].copy()
        for c in ("end_ts", "resolved_ts", "start_ts"):
            if c in mk:
                mk[c] = mk[c].astype("float").fillna(-1).astype(np.int64)
        self.meta: dict[int, dict] = {int(r["market_id"]): r for r in mk.to_dict("records")}

    def path(self, market_id: int):
        return self.paths.get(int(market_id))


_DEFAULT_STORES: dict[str, PriceStore] = {}


def default_store(source: str = "hourly") -> PriceStore:
    """Lazy-load the lab dataset (hourly paths for all 4,000 markets, or 1-min short-auto paths)."""
    if source not in _DEFAULT_STORES:
        import sys
        from pathlib import Path
        src = str(Path(__file__).resolve().parents[1])
        if src not in sys.path:
            sys.path.insert(0, src)
        from data_loader import load_markets, load_prices
        m = load_markets()
        if source == "hourly":
            p = load_prices()
        elif source == "short_auto_1min":
            p = load_prices(short_auto_1min=True)
            p["after_known"] = 0
            m = m[m.is_short_auto == 1]
        else:
            raise ValueError(source)
        _DEFAULT_STORES[source] = PriceStore(p, m)
    return _DEFAULT_STORES[source]


@dataclass
class BacktestConfig:
    bankroll: float = 10_800.0
    max_pos_frac: float = 0.05           # max budget per position as share of book equity
    liq_cap_frac: float = 0.01           # max budget per position as share of market LIFETIME volume (ex-post!)
    max_event_frac: float = 0.10         # max open cost per event_id (neg-risk legs are correlated)
    min_order_usd: float = 5.0
    cost_model: object = None            # None -> 03's model if src/cost_model.py exists, else placeholder
    price_source: str = "hourly"         # 'hourly' | 'short_auto_1min'
    start_ts: Optional[int] = None       # signals before this are ignored; equity calendar starts here
    end_ts: Optional[int] = None         # signals after this are ignored; calendar ends at max(end_ts, last close)
    allow_after_known_entry: bool = False  # forbid fills on bars flagged after_known (outcome-derived, audit filter)
    entry_band: tuple = (0.01, 0.99)     # held-token mid at fill must be inside (ex-ante pinned-price filter)
    max_entry_delay_h: float = 24.0      # next bar must arrive within this many hours of the signal
    one_position_per_market: bool = True
    cash_yield: float = 0.045            # p.a. benchmark (opportunity cost); used for excess returns
    accrue_cash_yield: bool = True       # idle cash earns cash_yield (so Sharpe/excess measure deployed capital vs cash)
    mtm: str = "mid"                     # 'mid' | 'bid' (liquidation value) for the daily curve
    label: str = ""


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    skipped: pd.DataFrame
    equity: pd.Series                    # daily (UTC day end) equity, index = pd.Timestamp
    config: BacktestConfig
    cost_model_name: str
    metrics: dict = field(default_factory=dict)

    def summary(self) -> dict:
        if not self.metrics:
            from .metrics import summarize
            self.metrics = summarize(self)
        return self.metrics


# ----------------------------------------------------------------------------------------------------------------
# exit rules
# ----------------------------------------------------------------------------------------------------------------
_DUR = re.compile(r"^(-?\d+(?:\.\d+)?)([mhd])$")


def _dur_s(s: str) -> float:
    m = _DUR.match(s.strip())
    if not m:
        raise ValueError(f"bad duration {s!r}")
    v = float(m.group(1))
    return v * {"m": 60, "h": 3600, "d": DAY}[m.group(2)]


def parse_exit_rule(rule: str) -> list[tuple[str, float]]:
    out = []
    for clause in str(rule or "resolve").split("|"):
        clause = clause.strip()
        if not clause or clause == "resolve":
            continue
        k, _, v = clause.partition(":")
        if k == "hold":
            out.append(("time_rel", _dur_s(v)))
        elif k == "at":
            out.append(("time_abs", float(v)))
        elif k == "end":
            out.append(("end_rel", _dur_s(v)))
        elif k in ("tp", "sl"):
            out.append((k, float(v)))
        else:
            raise ValueError(f"unknown exit clause {clause!r}")
    return out


def _find_exit(ts, pm, i0, fill_ts, end_ts, clauses):
    """Return (exit_index or -1, reason). pm = held-token mid path."""
    n = len(ts)
    best_j, best_r = n, "resolve"
    for kind, v in clauses:
        if kind in ("time_rel", "time_abs", "end_rel"):
            tgt = fill_ts + v if kind == "time_rel" else (v if kind == "time_abs" else end_ts + v)
            j = int(np.searchsorted(ts, tgt, side="left"))
            j = max(j, i0 + 1)
        else:
            seg = pm[i0 + 1:]
            hit = seg >= v if kind == "tp" else seg <= v
            if not hit.any():
                continue
            j = i0 + 1 + int(np.argmax(hit)) + 1      # trigger bar + 1
        if j < best_j:
            best_j, best_r = j, kind
    if best_j >= n:
        return -1, "resolve"
    return best_j, best_r


# ----------------------------------------------------------------------------------------------------------------
# main entry point
# ----------------------------------------------------------------------------------------------------------------
def run_backtest(signal_df: pd.DataFrame, config: Optional[BacktestConfig] = None,
                 prices: Optional[pd.DataFrame] = None, markets: Optional[pd.DataFrame] = None,
                 store: Optional[PriceStore] = None) -> BacktestResult:
    cfg = config or BacktestConfig()
    cost = cfg.cost_model if cfg.cost_model is not None else load_default_cost_model()
    if store is None:
        store = PriceStore(prices, markets) if prices is not None else default_store(cfg.price_source)

    sig = signal_df.copy()
    if "exit_rule" not in sig:
        sig["exit_rule"] = "resolve"
    if "strategy" not in sig:
        sig["strategy"] = cfg.label
    for c in ("size_usd", "target_weight"):
        if c not in sig:
            sig[c] = np.nan
    sig["ts"] = sig["ts"].astype(np.int64)
    if cfg.start_ts is not None:
        sig = sig[sig.ts >= cfg.start_ts]
    if cfg.end_ts is not None:
        sig = sig[sig.ts <= cfg.end_ts]

    skipped = []
    cands = []   # pre-computed fills / exits per signal
    rule_cache: dict[str, list] = {}
    lo, hi = cfg.entry_band
    for r in sig.itertuples(index=False):
        mid_ = int(r.market_id)
        pth = store.path(mid_)
        meta = store.meta.get(mid_)
        if pth is None or meta is None:
            skipped.append((mid_, r.ts, r.side, "no_data")); continue
        ts, py, ak = pth
        i0 = int(np.searchsorted(ts, r.ts, side="right"))
        if i0 >= len(ts):
            skipped.append((mid_, r.ts, r.side, "no_next_bar")); continue
        fts = int(ts[i0])
        if fts >= meta["resolved_ts"] > 0:
            skipped.append((mid_, r.ts, r.side, "after_resolution")); continue
        if fts - r.ts > cfg.max_entry_delay_h * 3600:
            skipped.append((mid_, r.ts, r.side, "entry_delay")); continue
        if not cfg.allow_after_known_entry and ak[i0]:
            skipped.append((mid_, r.ts, r.side, "after_known")); continue
        side = str(r.side).upper()
        pm = py if side == "YES" else 1.0 - py
        m0 = float(pm[i0])
        if not (lo <= m0 <= hi):
            skipped.append((mid_, r.ts, r.side, "entry_band")); continue
        rule = str(r.exit_rule)
        if rule not in rule_cache:
            rule_cache[rule] = parse_exit_rule(rule)
        j, why = _find_exit(ts, pm, i0, fts, meta["end_ts"], rule_cache[rule])
        cands.append(dict(market_id=mid_, signal_ts=int(r.ts), side=side, fill_ts=fts, i0=i0, entry_mid=m0,
                          exit_j=j, exit_reason=why, size_usd=r.size_usd, target_weight=r.target_weight,
                          strategy=r.strategy, exit_rule=rule))
    cands.sort(key=lambda d: (d["fill_ts"], d["market_id"]))

    cash = float(cfg.bankroll)
    open_cost = 0.0
    open_by_mkt: set[int] = set()
    open_by_evt: dict = {}
    heap: list = []          # (close_ts, seq, trade_idx)
    trades: list[dict] = []
    flows: list[tuple[int, float]] = []
    seq = 0

    def settle_until(t):
        nonlocal cash, open_cost
        while heap and heap[0][0] <= t:
            _, _, k = heapq.heappop(heap)
            tr = trades[k]
            cash += tr["close_value"]
            open_cost -= tr["entry_cost"]
            open_by_mkt.discard(tr["market_id"])
            open_by_evt[tr["event_id"]] -= tr["entry_cost"]

    for c in cands:
        t = c["fill_ts"]
        settle_until(t)
        mid_ = c["market_id"]
        meta = store.meta[mid_]
        if cfg.one_position_per_market and mid_ in open_by_mkt:
            skipped.append((mid_, c["signal_ts"], c["side"], "position_open")); continue
        eq = cash + open_cost
        if c["size_usd"] is not None and not (isinstance(c["size_usd"], float) and math.isnan(c["size_usd"])):
            B = float(c["size_usd"])
        else:
            w = c["target_weight"]
            B = float(w if w == w else cfg.max_pos_frac) * eq
        evt = meta.get("event_id", mid_)
        evt = mid_ if evt is None or (isinstance(evt, float) and math.isnan(evt)) else evt
        caps = [B, cfg.max_pos_frac * eq, cfg.liq_cap_frac * float(meta.get("volume_usd") or 0.0),
                cfg.max_event_frac * eq - open_by_evt.get(evt, 0.0), cash]
        B = min(caps)
        if B < cfg.min_order_usd:
            skipped.append((mid_, c["signal_ts"], c["side"], "size_cap")); continue
        m0 = c["entry_mid"]
        cmeta = dict(meta); cmeta["ts"] = t; cmeta["side"] = c["side"]
        if hasattr(cost, "executable") and not cost.executable("buy", m0, B, cmeta):
            skipped.append((mid_, c["signal_ts"], c["side"], "unexecutable")); continue
        hs = cost.half_spread(m0, cmeta)
        sl = cost.slip("buy", m0, B, cmeta) if hasattr(cost, "slip") else hs + cost.impact(m0, B, cmeta)
        px = min(m0 + sl, 0.999)
        fps = cost.fee_per_share(px, cmeta, "BUY")
        shares = B / (px + fps)
        entry_fee = shares * fps
        # ---- close (pre-computed; exit decision depends only on path after the fill bar)
        ts, py, _ = store.path(mid_)
        won = int(meta["yes_won"]) if c["side"] == "YES" else 1 - int(meta["yes_won"])
        if c["exit_j"] >= 0:
            j = c["exit_j"]
            mj = float(py[j]) if c["side"] == "YES" else 1.0 - float(py[j])
            cmeta2 = dict(meta); cmeta2["ts"] = int(ts[j]); cmeta2["side"] = c["side"]
            if hasattr(cost, "slip"):
                xpx = max(mj - cost.slip("sell", mj, shares * mj, cmeta2), 0.001)
            else:
                xpx = max(mj - cost.half_spread(mj, cmeta2) - cost.impact(mj, shares * mj, cmeta2), 0.001)
            xfee = shares * cost.fee_per_share(xpx, cmeta2, "SELL")
            close_value = shares * xpx - xfee
            close_ts = int(ts[j]); exit_mid = mj
            gross_value = shares * mj
        else:
            xpx = float(won); xfee = 0.0
            close_value = shares * won
            close_ts = int(meta["resolved_ts"]); exit_mid = float(won)
            gross_value = shares * won
        cash -= B
        open_cost += B
        open_by_mkt.add(mid_)
        open_by_evt[evt] = open_by_evt.get(evt, 0.0) + B
        tr = dict(market_id=mid_, event_id=evt, category=meta.get("category"),
                  is_short_auto=meta.get("is_short_auto"), strategy=c["strategy"], side=c["side"],
                  signal_ts=c["signal_ts"], fill_ts=t, entry_mid=m0, entry_px=px, half_spread=hs, shares=shares,
                  entry_cost=B, entry_fee=entry_fee, close_ts=close_ts, exit_reason=c["exit_reason"],
                  exit_mid=exit_mid, exit_px=xpx, exit_fee=xfee, close_value=close_value, won=won,
                  pnl=close_value - B, ret=close_value / B - 1.0,
                  gross_pnl=gross_value - shares * m0, gross_ret=exit_mid / m0 - 1.0,
                  hold_days=(close_ts - t) / DAY, volume_usd=meta.get("volume_usd"),
                  sample_weight=meta.get("sample_weight", 1.0), i0=c["i0"], exit_j=c["exit_j"])
        trades.append(tr)
        flows.append((t, -B)); flows.append((close_ts, close_value))
        heapq.heappush(heap, (close_ts, seq, len(trades) - 1)); seq += 1
    settle_until(np.iinfo(np.int64).max)

    tdf = pd.DataFrame(trades)
    sdf = pd.DataFrame(skipped, columns=["market_id", "signal_ts", "side", "reason"])
    equity = _equity_curve(tdf, store, cfg, cost)
    res = BacktestResult(trades=tdf.drop(columns=["i0", "exit_j"], errors="ignore"), skipped=sdf, equity=equity,
                         config=cfg, cost_model_name=getattr(cost, "name", type(cost).__name__))
    return res


def _equity_curve(tdf: pd.DataFrame, store: PriceStore, cfg: BacktestConfig, cost) -> pd.Series:
    if tdf.empty:
        t0 = cfg.start_ts or 0
        t1 = cfg.end_ts or t0 + DAY
    else:
        t0 = cfg.start_ts if cfg.start_ts is not None else int(tdf.fill_ts.min())
        t1 = max(cfg.end_ts or 0, int(tdf.close_ts.max()))
    d0 = (t0 // DAY) * DAY
    day_ends = np.arange(d0 + DAY, (t1 // DAY) * DAY + 2 * DAY, DAY, dtype=np.int64)   # end of each UTC day
    nd = len(day_ends)
    cashflow = np.zeros(nd)
    mtm = np.zeros(nd)
    if not tdf.empty:
        # a flow at ts belongs to the first day_end >= ts... day_end is exclusive upper bound of that day
        fi = np.searchsorted(day_ends, tdf.fill_ts.to_numpy(np.int64), side="right")
        ci = np.searchsorted(day_ends, tdf.close_ts.to_numpy(np.int64), side="right")
        np.add.at(cashflow, np.minimum(fi, nd - 1), -tdf.entry_cost.to_numpy())
        np.add.at(cashflow, np.minimum(ci, nd - 1), tdf.close_value.to_numpy())
        for k, r in enumerate(tdf.itertuples(index=False)):
            a, b = fi[k], min(ci[k], nd)          # open at day ends a..b-1
            if b <= a:
                continue
            ts, py, _ = store.path(r.market_id)
            de = day_ends[a:b]
            idx = np.searchsorted(ts, de, side="right") - 1
            idx = np.clip(idx, 0, len(ts) - 1)
            pm = py[idx] if r.side == "YES" else 1.0 - py[idx]
            if cfg.mtm == "bid":
                meta = store.meta[r.market_id]
                pm = np.maximum(pm - cost.half_spread(float(pm.mean()), meta), 0.0)
            mtm[a:b] += r.shares * pm
    cash = cfg.bankroll + np.cumsum(cashflow)
    if cfg.accrue_cash_yield:
        g = (1 + cfg.cash_yield) ** (1 / 365) - 1
        interest = np.zeros(nd)
        c = cfg.bankroll
        for i in range(nd):
            c += cashflow[i]
            inc = max(c, 0) * g
            c += inc
            interest[i] = inc
        cash = cfg.bankroll + np.cumsum(cashflow) + np.cumsum(interest)
    eq = cash + mtm
    idx = pd.to_datetime(day_ends - 1, unit="s", utc=True).normalize()
    return pd.Series(eq, index=idx, name="equity")
