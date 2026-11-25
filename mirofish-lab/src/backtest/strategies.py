"""Signal generators for the 04 strategy families.  Each returns a signal_df for run_backtest().

Every rule uses only information available at signal time `ts`:
  * as-of price = last bar with bar_ts <= ts (stale > max_age_h -> no signal)
  * scheduled end_ts, category, question text, start/first-price times (all known ex ante)
  * shocks: p_pre/p_post/dp/lag_h/pre_vol_24h/hours_to_end at detect_ts (NOT next-bar, spike_revert, n_shocks_market,
    hours_to_resolved, after_known — those use later information)
  * volume_usd (lifetime) is ex-post; used only as a filter variant and flagged as selection-biased.
The engine then fills at the NEXT bar after ts.

Families
  S1 momentum after shock     S2 fade after shock         S3a NO on longshots      S3b buy heavy favourite near end
  S4 'by <date>' NO drift     S5 geopolitics YES lottery  S6a mid-range YES (01 calibration hint) S6b (03 hypotheses)
  S7a buy market favourite    S7b always-NO baseline      S7c random entry (Monte Carlo null)
  SA1 short-auto favourite    SA2 short-auto momentum/fade (1-min paths)
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

H = 3600
D = 86400


def asof_prices(store, market_ids, ts, max_age_h=24.0):
    """p_yes as of ts (vectorised over markets). NaN if no bar in [ts - max_age_h, ts]."""
    out = np.full(len(market_ids), np.nan)
    for k, (m, t) in enumerate(zip(market_ids, ts)):
        pth = store.path(m)
        if pth is None:
            continue
        bts, py, _ = pth
        i = int(np.searchsorted(bts, t, side="right")) - 1
        if i >= 0 and t - bts[i] <= max_age_h * H:
            out[k] = py[i]
    return out


def _sig(market_ids, ts, side, w, exit_rule, name):
    return pd.DataFrame({"market_id": np.asarray(market_ids, dtype=np.int64), "ts": np.asarray(ts, dtype=np.int64),
                         "side": side, "target_weight": w, "exit_rule": exit_rule, "strategy": name})


def _before_end(store, mk, hours_before, cond, side_fn, w, exit_rule, name, max_age_h=24.0):
    """Generic 'snapshot at end_ts - hours_before' rule. cond(p, mk) -> bool mask; side_fn(p) -> 'YES'/'NO' array."""
    ts = mk.end_ts.to_numpy(np.int64) - int(hours_before * H)
    p = asof_prices(store, mk.market_id.to_numpy(), ts, max_age_h)
    ok = np.isfinite(p) & cond(p, mk)
    # the market must still be open at the signal time (otherwise nothing to trade); this is known at ts
    ok &= mk.resolved_ts.astype('float').fillna(0).to_numpy() > ts
    ok &= mk.first_price_ts.astype('float').fillna(9e18).to_numpy() < ts
    if not ok.any():
        return _sig([], [], "YES", w, exit_rule, name)
    sides = side_fn(p[ok])
    return _sig(mk.market_id.to_numpy()[ok], ts[ok], sides, w, exit_rule, name)


# ------------------------------------------------------------------------------------------------ S1 / S2 shocks
def shock_signals(shocks, thr, max_lag_h, hold, mode, w=0.02, vol_min=0.0, name=None, p_band=(0.05, 0.95)):
    s = shocks[(shocks.dp.abs() >= thr) & (shocks.lag_h <= max_lag_h) & (shocks.post_pinned == 0)
               & (shocks.p_post.between(*p_band)) & (shocks.volume_usd >= vol_min)]
    d = s.direction.to_numpy()
    if mode == "momentum":
        side = np.where(d > 0, "YES", "NO")
    else:
        side = np.where(d > 0, "NO", "YES")
    rule = "resolve" if hold == "resolve" else f"hold:{hold}"
    nm = name or f"{'S1' if mode == 'momentum' else 'S2'}_thr{thr}_lag{max_lag_h}_{hold}_v{int(vol_min/1000)}k"
    return _sig(s.market_id, s.detect_ts, side, w, rule, nm)


# ------------------------------------------------------------------------------------------------ S3 favourite-longshot
def s3a_longshot_no(store, mk, x, hours_before, w=0.02):
    return _before_end(store, mk, hours_before, lambda p, m: (p <= x) & (p >= 0.01),
                       lambda p: np.full(len(p), "NO"), w, "resolve", f"S3a_x{x}_H{hours_before}h")


def s3b_favourite(store, mk, y, hours_before, w=0.02):
    return _before_end(store, mk, hours_before, lambda p, m: (np.maximum(p, 1 - p) >= y) & (np.maximum(p, 1 - p) <= 0.99),
                       lambda p: np.where(p >= 0.5, "YES", "NO"), w, "resolve", f"S3b_y{y}_H{hours_before}h")


# ------------------------------------------------------------------------------------------------ S4 by-date decay
BY_DATE = re.compile(r"\b(by|before)\b|\bin 20\d\d\?|\bthis (year|month|week)\b", re.I)


def is_by_date(questions: pd.Series) -> np.ndarray:
    return questions.fillna("").str.contains(BY_DATE).to_numpy()


def s4_bydate_no(store, mk, lo, hi, hours_before, w=0.02):
    sub = mk[is_by_date(mk.question)]
    return _before_end(store, sub, hours_before, lambda p, m: (p >= lo) & (p <= hi),
                       lambda p: np.full(len(p), "NO"), w, "resolve", f"S4_p{lo}-{hi}_H{hours_before}h")


# ------------------------------------------------------------------------------------------------ S5 lottery tickets
DRAMATIC = re.compile(r"invade|invasion|war\b|strike|attack|nuclear|taiwan|blockade|missile|ceasefire|coup|"
                      r"assassinat|military|troops|bomb|annex|martial law|regime|declare", re.I)


def s5_lottery_yes(store, mk, x, hours_before, scope, w=0.01):
    sub = mk[mk.category == "geopolitics"]
    if scope == "dramatic":
        sub = sub[sub.question.fillna("").str.contains(DRAMATIC).to_numpy()]
    return _before_end(store, sub, hours_before, lambda p, m: (p <= x) & (p >= 0.01),
                       lambda p: np.full(len(p), "YES"), w, "resolve", f"S5_x{x}_H{hours_before}h_{scope}")


# ------------------------------------------------------------------------------------------------ S6 mid-range YES
def s6a_midrange_yes(store, mk, lo, hi, hours_before, w=0.02):
    return _before_end(store, mk, hours_before, lambda p, m: (p >= lo) & (p <= hi),
                       lambda p: np.full(len(p), "YES"), w, "resolve", f"S6a_p{lo}-{hi}_H{hours_before}h")


# ------------------------------------------------------------------------------------------------ S7 baselines
def s7a_market_favourite(store, mk, hours_before, w=0.02):
    return _before_end(store, mk, hours_before, lambda p, m: (p >= 0.01) & (p <= 0.99) & (p != 0.5),
                       lambda p: np.where(p > 0.5, "YES", "NO"), w, "resolve", f"S7a_H{hours_before}h")


def s7b_always_no(store, mk, hours_before, w=0.02):
    return _before_end(store, mk, hours_before, lambda p, m: (p >= 0.01) & (p <= 0.99),
                       lambda p: np.full(len(p), "NO"), w, "resolve", f"S7b_H{hours_before}h")


def s7c_random(store, mk, rng, w=0.02, t_min=None, t_max=None, hold="resolve"):
    """One random entry per market: uniform time in [first_price+6h, min(end, resolved)-1h] (clipped to window),
    random side, hold to resolution (or hold:<dur>)."""
    a = mk.first_price_ts.astype('float').fillna(9e15).to_numpy().astype(np.int64) + 6 * H
    b = np.minimum(mk.end_ts.to_numpy(np.int64), mk.resolved_ts.to_numpy(np.int64)) - H
    if t_min is not None:
        a = np.maximum(a, t_min)
    if t_max is not None:
        b = np.minimum(b, t_max)
    ok = b > a
    ts = a[ok] + (rng.random(ok.sum()) * (b[ok] - a[ok])).astype(np.int64)
    side = np.where(rng.random(ok.sum()) < 0.5, "YES", "NO")
    rule = "resolve" if hold == "resolve" else f"hold:{hold}"
    return _sig(mk.market_id.to_numpy()[ok], ts, side, w, rule, "S7c_random")


# ------------------------------------------------------------------------------------------------ short-auto (1-min)
def sa1_favourite(store, mk, y, hours_before, w=0.02):
    return _before_end(store, mk, hours_before,
                       lambda p, m: (np.maximum(p, 1 - p) >= y) & (np.maximum(p, 1 - p) <= 0.99),
                       lambda p: np.where(p >= 0.5, "YES", "NO"), w, "resolve", f"SA1_y{y}_H{hours_before}h",
                       max_age_h=0.5)


def sa2_move(store, mk, d, hours_before, lookback_h, mode, w=0.02):
    ts = mk.end_ts.to_numpy(np.int64) - int(hours_before * H)
    p1 = asof_prices(store, mk.market_id.to_numpy(), ts, 0.5)
    p0 = asof_prices(store, mk.market_id.to_numpy(), ts - int(lookback_h * H), 0.5)
    mv = p1 - p0
    ok = np.isfinite(mv) & (np.abs(mv) >= d) & (p1 >= 0.01) & (p1 <= 0.99)
    ok &= mk.resolved_ts.astype('float').fillna(0).to_numpy() > ts
    up = mv[ok] > 0
    side = np.where(up, "YES", "NO") if mode == "momentum" else np.where(up, "NO", "YES")
    return _sig(mk.market_id.to_numpy()[ok], ts[ok], side, w, "resolve",
                f"SA2_{mode}_d{d}_H{hours_before}h_lb{lookback_h}h")
