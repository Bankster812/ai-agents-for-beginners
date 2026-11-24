"""
data_loader.py — load the MiroFish-lab Polymarket dataset (built by src/fetch_polymarket.py).

All timestamps are integer unix seconds, UTC.  All prices are the YES-token price in [0, 1].

IMPORTANT: p_yes comes from CLOB /prices-history = Polymarket's *displayed* price (midpoint, or last trade
when spread > $0.10), sampled ~hourly.  It is NOT an executable bid/ask.  Apply a spread/slippage model.

------------------------------------------------------------------------------------------------
markets.csv.gz  (one row per market; load_markets())
    market_id       int    Gamma market id (primary key)
    condition_id    str    CTF condition id (0x...)
    question        str
    category        str    politics | sports | crypto | macro | geopolitics | culture | other
    is_short_auto   int    1 = recurring short-window auto market (crypto Up/Down 5m/15m/1h/4h/daily,
                           daily crypto-price / temperature markets with <=48h life). Behave differently!
    start_ts        int    min(Gamma startDate, createdAt)
    end_ts          int    Gamma endDate (SCHEDULED end; markets often resolve before or after it)
    resolved_ts     int    Gamma closedTime (fallback umaEndDate) — when the market was closed/resolved
    volume_usd      float  lifetime volume (Gamma volumeNum)
    liquidity_usd   float  Gamma liquidityNum snapshot AT FETCH TIME (post-close => mostly ~0; not a history)
    neg_risk        int    1 = leg of a neg-risk multi-outcome event (legs of the same event_id are correlated)
    yes_won         int    1 if YES resolved to $1, else 0 (from final outcomePrices; 50-50/unresolved excluded)
    yes_token_id    str    CLOB token id of the YES outcome (for Up/Down markets: the "Up" token)
    n_price_points  int    hourly points kept (after dropping post-resolution points)
    fidelity_min    float  median spacing of the hourly series in minutes (60 = hourly)
  extras:
    slug, event_id, is_recurring, outcome_kind ('yesno' | 'updown')
    first_price_ts, last_price_ts, last_raw_price_ts (before dropping post-resolution points)
    n_points_after_resolution_dropped
    last_p_yes      float  last kept price
    known_ts        int    earliest ts after which p_yes stays pinned on the winning side (>=0.99 / <=0.01)
                           until resolution.  <NA> if never pinned.  *Uses the outcome* => only for
                           auditing/labelling, never as a trading-time feature.
    frac_hours_changed float  share of consecutive hourly samples where price changed (activity/liquidity proxy)
    stratum, stratum_pop, sample_weight   stratified-sample bookkeeping (weight = stratum_pop / n_sampled)
    tags            str    '|'-joined Gamma tag labels
    + derived by the loader: period ('2024H2' ...), split ('train'|'test'), end_dt / resolved_dt (Timestamp)

prices_hourly.csv.gz  (load_prices())
    market_id int, ts int, p_yes float, after_known int8 (1 = ts >= known_ts, i.e. outcome effectively known)
    sorted by (market_id, ts). Points after resolved_ts are already dropped.

shocks.csv.gz  (load_shocks()) — candidate "news shocks" on regular (non-short-auto) markets
    market_id, category
    pre_ts, detect_ts   bar where the move started / bar where |dp| >= 0.10 was first reached (within <= 6h)
    p_pre, p_post, dp, direction (+1 up / -1 down), lag_h (detect_ts - pre_ts in hours)
    p_next_bar, next_bar_ts   first bar AFTER detection (a realistic entry: you see the shock at detect_ts)
    p_6h, p_24h, p_72h, p_7d  as-of price at detect_ts + horizon (NaN if horizon is past last price)
    yes_won, p_final (= yes_won)
    hours_to_end, hours_to_resolved   from detect_ts
    post_pinned   1 if p_post >= 0.99 or <= 0.01 (often the resolution itself)
    after_known   1 if detect_ts >= known_ts
    volume_usd, neg_risk, event_id
    pre_vol_24h            std of hourly price changes in the 24h before pre_ts (noise level)
    spike_revert_next_bar  1 if the next bar is back within 0.03 of p_pre (likely spread/print artefact)
    n_shocks_market        number of shocks detected in this market (high = noisy/thin market)
    The first 6h of each market's price history are excluded (listing noise).

prices_short_auto_1min.csv.gz  (load_prices(short_auto_1min=True)) — 1-minute series for short-auto markets
    market_id, ts, p_yes
------------------------------------------------------------------------------------------------
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_CUTOFF = "2025-07-01"


def _cutoff_ts(cutoff: str) -> int:
    return int(pd.Timestamp(cutoff, tz="UTC").timestamp())


def load_markets(include_short_auto: bool = True, cutoff: str = DEFAULT_CUTOFF) -> pd.DataFrame:
    """One row per market. Adds `period`, `split` (train if resolved_ts < cutoff) and datetime helpers."""
    m = pd.read_csv(DATA / "markets.csv.gz", dtype={"yes_token_id": str, "condition_id": str})
    for c in ("start_ts", "end_ts", "resolved_ts", "first_price_ts", "last_price_ts", "last_raw_price_ts",
              "known_ts"):
        m[c] = m[c].astype("Int64")
    m["end_dt"] = pd.to_datetime(m["end_ts"], unit="s", utc=True)
    m["resolved_dt"] = pd.to_datetime(m["resolved_ts"], unit="s", utc=True)
    m["period"] = m["end_dt"].dt.year.astype(str) + "H" + (1 + (m["end_dt"].dt.month > 6)).astype(str)
    m["split"] = (m["resolved_ts"] >= _cutoff_ts(cutoff)).map({True: "test", False: "train"})
    if not include_short_auto:
        m = m[m.is_short_auto == 0]
    return m.reset_index(drop=True)


def load_prices(market_ids=None, drop_after_known: bool = False, short_auto_1min: bool = False) -> pd.DataFrame:
    """Hourly YES price paths (market_id, ts, p_yes, after_known). Optionally filter to `market_ids` and drop
    points after the outcome was effectively known (after_known==1)."""
    f = "prices_short_auto_1min.csv.gz" if short_auto_1min else "prices_hourly.csv.gz"
    p = pd.read_csv(DATA / f)
    if market_ids is not None:
        p = p[p.market_id.isin(set(market_ids))]
    if drop_after_known and "after_known" in p:
        p = p[p.after_known == 0]
    return p.sort_values(["market_id", "ts"]).reset_index(drop=True)


def load_shocks(exclude_pinned: bool = False, cutoff: str = DEFAULT_CUTOFF) -> pd.DataFrame:
    """Candidate news shocks (|dp|>=0.10 within <=6h). Adds `split` by detect_ts vs cutoff.
    exclude_pinned=True drops shocks that land at >=0.99/<=0.01 or occur after the outcome was known."""
    s = pd.read_csv(DATA / "shocks.csv.gz")
    s["split"] = (s["detect_ts"] >= _cutoff_ts(cutoff)).map({True: "test", False: "train"})
    if exclude_pinned:
        s = s[(s.post_pinned == 0) & (s.after_known == 0)]
    return s.reset_index(drop=True)


def train_test_split_by_time(cutoff: str = DEFAULT_CUTOFF, include_short_auto: bool = False,
                             with_prices: bool = True, purge: bool = True):
    """Walk-forward split by RESOLUTION time (lab convention: TRAIN = resolved before cutoff).

    Returns dict with keys: markets_train, markets_test, and (if with_prices) prices_train, prices_test,
    shocks_train, shocks_test.

    purge=True: train price/shock rows with ts >= cutoff are dropped (can't happen for train markets since
    they resolved before cutoff, kept as a guard) and TEST markets whose life started before the cutoff keep
    their full path — but shocks_test only contains shocks detected on/after the cutoff, so nothing tuned on
    pre-cutoff behaviour of test markets leaks. Note: test markets' pre-cutoff prices were observable in
    TRAIN time; that is information, not leakage.
    """
    m = load_markets(include_short_auto=include_short_auto, cutoff=cutoff)
    c = _cutoff_ts(cutoff)
    out = {"markets_train": m[m.split == "train"].reset_index(drop=True),
           "markets_test": m[m.split == "test"].reset_index(drop=True)}
    if with_prices:
        p = load_prices(market_ids=m.market_id)
        tr = set(out["markets_train"].market_id)
        p_tr = p[p.market_id.isin(tr)]
        if purge:
            p_tr = p_tr[p_tr.ts < c]
        out["prices_train"] = p_tr.reset_index(drop=True)
        out["prices_test"] = p[~p.market_id.isin(tr)].reset_index(drop=True)
        s = load_shocks(cutoff=cutoff)
        s = s[s.market_id.isin(set(m.market_id))]
        out["shocks_train"] = s[s.market_id.isin(tr) & (s.detect_ts < c)].reset_index(drop=True)
        out["shocks_test"] = s[~s.market_id.isin(tr) & (s.detect_ts >= c)].reset_index(drop=True)
    return out


if __name__ == "__main__":
    d = train_test_split_by_time()
    for k, v in d.items():
        print(k, v.shape)
