"""Point-in-time shock table for the swarm (05).

Every column produced here is computable at the signal time `detect_ts` (the bar on which the shock is
first visible).  Entry is assumed at `p_next_bar` (first bar AFTER detection); outcomes are measured from it.

Look-ahead guards
-----------------
* `volume_usd` and `n_shocks_market` in shocks.csv.gz are LIFETIME quantities (known only ex post) -> NOT
  used as features.  Replaced by `act_7d` (share of hourly bars with a price change in the 168h before
  pre_ts) and `n_bars_7d` (number of bars in that window), both computed from the hourly price file
  strictly before pre_ts.
* `after_known`, `known_ts`, `yes_won`, `hours_to_resolved`, `spike_revert_next_bar` are NOT used as
  features or filters.  The universe filter is ex-ante: 0.02 < p_post < 0.98 (this alone removes every
  after_known row in the data).
* `hours_to_end` uses the SCHEDULED end_ts (ex ante).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from data_loader import load_shocks, load_markets, _cutoff_ts  # noqa: E402

CACHE = ROOT / "results" / "05_shock_features.csv.gz"
CATS = ["politics", "sports", "crypto", "macro", "geopolitics", "culture", "other"]
HORIZONS = (6, 24, 72)


def _activity_proxy(shocks: pd.DataFrame) -> pd.DataFrame:
    """act_7d / n_bars_7d from hourly prices strictly before pre_ts (no look-ahead)."""
    p = pd.read_csv(ROOT / "data" / "prices_hourly.csv.gz", usecols=["market_id", "ts", "p_yes"])
    p = p.sort_values(["market_id", "ts"])
    p["chg"] = (p.groupby("market_id").p_yes.diff().abs() > 1e-9).astype(np.int32)
    out_act = np.full(len(shocks), np.nan)
    out_n = np.zeros(len(shocks))
    g = {k: (v.ts.values, np.cumsum(v.chg.values)) for k, v in p.groupby("market_id")}
    for mid, idx in shocks.groupby("market_id").indices.items():
        if mid not in g:
            continue
        ts, cs = g[mid]
        pre = shocks.pre_ts.values[idx]
        hi = np.searchsorted(ts, pre, side="left")           # bars strictly before pre_ts
        lo = np.searchsorted(ts, pre - 168 * 3600, side="left")
        n = hi - lo
        c = np.where(hi > 0, cs[np.maximum(hi - 1, 0)], 0) - np.where(lo > 0, cs[np.maximum(lo - 1, 0)], 0)
        out_n[idx] = n
        out_act[idx] = np.where(n > 1, c / np.maximum(n - 1, 1), np.nan)
    shocks = shocks.copy()
    shocks["act_7d"] = np.clip(np.nan_to_num(out_act, nan=0.3), 0, 1)
    shocks["n_bars_7d"] = out_n
    return shocks


def build_features(force: bool = False, cutoff: str = "2025-07-01") -> pd.DataFrame:
    if CACHE.exists() and not force:
        return pd.read_csv(CACHE)
    s = load_shocks(cutoff=cutoff)
    m = load_markets(include_short_auto=False, cutoff=cutoff)[["market_id", "split"]].rename(
        columns={"split": "mkt_split"})
    s = s.merge(m, on="market_id", how="left")
    c = _cutoff_ts(cutoff)
    # lab split: TRAIN = shocks on train markets (resolved < cutoff); TEST = shocks on test markets detected >= cutoff
    s["split5"] = np.where(s.mkt_split == "train", "train",
                           np.where((s.mkt_split == "test") & (s.detect_ts >= c), "test", "purged"))
    s = s[s.split5 != "purged"]
    s = s[(s.p_post > 0.02) & (s.p_post < 0.98) & s.p_next_bar.notna()].reset_index(drop=True)
    s = _activity_proxy(s)
    s["hour_et"] = ((s.detect_ts // 3600) - 5) % 24          # US-Eastern hour of the detection bar
    s["log_hte"] = np.log1p(np.clip(s.hours_to_end, 0, None))
    s["pre_vol_24h"] = s.pre_vol_24h.fillna(s.pre_vol_24h.median())
    # outcomes (measured from the NEXT bar = tradeable entry), direction-adjusted
    for h in HORIZONS:
        s[f"cont_{h}h"] = s.direction * (s[f"p_{h}h"] - s.p_next_bar)
    s["cont_final"] = s.direction * (s.yes_won - s.p_next_bar)
    s["cont_nb_from_post"] = s.direction * (s.p_next_bar - s.p_post)
    keep = ["market_id", "event_id", "category", "split5", "pre_ts", "detect_ts", "next_bar_ts", "p_pre", "p_post",
            "dp", "direction", "lag_h", "p_next_bar", "p_6h", "p_24h", "p_72h", "yes_won", "hours_to_end", "log_hte",
            "pre_vol_24h", "act_7d", "n_bars_7d", "hour_et", "neg_risk", "volume_usd",
            "cont_6h", "cont_24h", "cont_72h", "cont_final", "cont_nb_from_post"]
    s = s[keep].sort_values(["detect_ts", "market_id"]).reset_index(drop=True)
    s.to_csv(CACHE, index=False)
    return s


if __name__ == "__main__":
    f = build_features(force=True)
    print(f.shape)
    print(f.split5.value_counts())
    print(f.describe().T[["mean", "50%", "min", "max"]])
