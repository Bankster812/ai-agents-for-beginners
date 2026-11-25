"""
Polymarket execution-cost model (specialist 03 - microstructure).

Conservative, empirically calibrated on 163 live CLOB order books sampled
2026-11-24 13:33-13:50 UTC (data/orderbook_snapshot.csv), plus the official
per-market fee schedule (Gamma `feeSchedule` field / docs.polymarket.com/trading/fees).

Conventions
-----------
* `mid`  : mid price (0..1) of the *token you are trading* (YES or NO). YES and NO
           books are exact mirrors on Polymarket, so buying NO at q == selling YES at 1-q.
* `side` : 'buy' (take the ask of that token) or 'sell' (hit the bid of that token).
* `size_usd` : USDC notional of the order (shares * price).
* `liquidity_usd` : market TOTAL traded volume in USD *known at decision time*
           (never the final, ex-post volume -> look-ahead bias). Tiers:
           T0 < $10k (mostly dead books), T1 $10k-$100k, T2 $100k-$1M, T3 > $1M.
           Interpolated in log-volume between anchors (median sampled volume per
           tier: 1e3, 3.5e4, 3e5, 2.9e6); clamped outside (no extrapolated
           improvement above ~$3M).
* All slippage numbers are *total* distance from mid to the volume-weighted fill
  price (half-spread + depth impact), in price units (1.0 = $1 per share).
  Default quantile is the 75th percentile of the live sample ("p75") = conservative.
  'p50' is available for sensitivity analysis only.
* Fees: taker-only, fee_usdc = shares * rate * p * (1 - p)  (p = fill price).
  Makers pay 0. No fee at redemption/settlement. Gas is paid by Polymarket's relayer.

Main API
--------
execution_price(side, mid, size_usd, liquidity_usd, category, ...) -> float
    all-in effective price per share (fee included). For buys this is what you
    pay per share; for sells it is what you receive per share.
fee(price, size_usd=None, shares=None, category='other', ...) -> float  (USDC)
slippage(...), half_spread(...), fee_rate(...), is_executable(...), round_trip_cost(...)
"""
from __future__ import annotations

import math
from typing import Optional

# ---------------------------------------------------------------------------
# 1. Empirical slippage tables (price units), from data/orderbook_snapshot.csv
#    rows = liquidity tier, cols = token-price bucket, values at sizes
#    [0 (=half-spread), $100, $500, $2,500, $10,000]
#    price buckets refer to the price of the token being BOUGHT (for a sell of
#    a token at p we look up the complement at 1-p, which is the same book).
# ---------------------------------------------------------------------------
SIZE_GRID = [0.0, 100.0, 500.0, 2500.0, 10000.0]

SLIP_P75 = {
    "T0": {"cheap": [0.0358, 0.2121, 0.5486, 0.6713, 0.7793],
           "mid":   [0.2146, 0.2732, 0.3967, 0.4863, 0.4888],
           "rich":  [0.0358, 0.0459, 0.0459, 0.0459, 0.0511]},
    "T1": {"cheap": [0.0075, 0.0400, 0.1519, 0.3702, 0.6304],
           "mid":   [0.0300, 0.0370, 0.0926, 0.2131, 0.3301],
           "rich":  [0.0075, 0.0242, 0.0295, 0.0396, 0.0436]},
    "T2": {"cheap": [0.0042, 0.0076, 0.0155, 0.0611, 0.2010],
           "mid":   [0.0078, 0.0194, 0.0304, 0.1291, 0.2970],
           "rich":  [0.0042, 0.0062, 0.0144, 0.0337, 0.0490]},
    "T3": {"cheap": [0.0050, 0.0050, 0.0078, 0.0263, 0.0880],
           "mid":   [0.0050, 0.0050, 0.0129, 0.0243, 0.1156],
           "rich":  [0.0050, 0.0050, 0.0128, 0.0228, 0.0399]},
}
SLIP_P50 = {
    "T0": {"cheap": [0.0115, 0.1152, 0.2836, 0.5663, 0.6928],
           "mid":   [0.0200, 0.0791, 0.2699, 0.3800, 0.3919],
           "rich":  [0.0115, 0.0272, 0.0312, 0.0351, 0.0384]},
    "T1": {"cheap": [0.0050, 0.0150, 0.0560, 0.1810, 0.3270],
           "mid":   [0.0100, 0.0180, 0.0403, 0.1369, 0.1912],
           "rich":  [0.0050, 0.0122, 0.0195, 0.0254, 0.0279]},
    "T2": {"cheap": [0.0015, 0.0049, 0.0081, 0.0307, 0.1336],
           "mid":   [0.0050, 0.0070, 0.0138, 0.0310, 0.1459],
           "rich":  [0.0015, 0.0046, 0.0070, 0.0158, 0.0227]},
    "T3": {"cheap": [0.0010, 0.0024, 0.0050, 0.0163, 0.0588],
           "mid":   [0.0050, 0.0050, 0.0050, 0.0133, 0.0361],
           "rich":  [0.0010, 0.0039, 0.0054, 0.0093, 0.0289]},
}
TABLES = {"p75": SLIP_P75, "p50": SLIP_P50}

# anchors = median total volume of sampled books in each tier
TIER_ANCHORS = [("T0", 1.0e3), ("T1", 3.5e4), ("T2", 3.0e5), ("T3", 2.9e6)]
MIN_TICK = 0.001
PRICE_FLOOR, PRICE_CAP = 0.001, 0.999

# ---------------------------------------------------------------------------
# 2. Fee schedules (taker rate in fee = shares * rate * p * (1-p))
#    "2026-11": live per-market feeSchedule observed in Gamma on 2026-11-24
#               (identical to docs.polymarket.com/trading/fees).
#    "2026-03": schedule rolled out 2026-03-30 (rates back-derived from Pine
#               Analytics' peak-effective-rate table = rate/4; approximate).
#    "pre-2026": no taker fees on Polymarket international (crypto 15-min fees
#               started 2026-01-05, sports 2026-02-18, all others 2026-03-30).
#    "conservative" (DEFAULT): max(2026-11, 2026-03) per category.
# ---------------------------------------------------------------------------
FEE_RATES = {
    "2026-11": {"crypto_5m_15m": 0.05, "crypto": 0.01, "sports": 0.05, "finance": 0.05,
                "tech": 0.05, "politics_elections": 0.05, "politics": 0.01,
                "economics": 0.01, "culture": 0.01, "weather": 0.01, "mentions": 0.01,
                "geopolitics": 0.0, "combo": 0.05, "other": 0.05},
    "2026-03": {"crypto_5m_15m": 0.072, "crypto": 0.072, "sports": 0.03, "finance": 0.04,
                "tech": 0.04, "politics_elections": 0.04, "politics": 0.04,
                "economics": 0.06, "culture": 0.05, "weather": 0.05, "mentions": 0.0625,
                "geopolitics": 0.0, "combo": 0.05, "other": 0.0625},
    "pre-2026": {},  # everything 0
}

# Gamma `feeType` strings -> canonical category
FEE_TYPE_ALIASES = {
    "crypto_btc_5m_15m_fees": "crypto_5m_15m", "crypto_fees_v2": "crypto", "crypto_fees": "crypto",
    "sports_fees_v3": "sports", "sports_fees": "sports", "finance_prices_fees": "finance",
    "finance_fees": "finance", "tech_fees": "tech", "politics_elections_fees": "politics_elections",
    "politics_fees": "politics", "economics_fees": "economics", "culture_fees": "culture",
    "weather_fees": "weather", "mentions_fees": "mentions", "general_fees": "other",
    "zero_fees": "geopolitics", "none": "geopolitics", "geopolitics_fees": "geopolitics",
    "elections": "politics_elections", "crypto_15m": "crypto_5m_15m", "crypto_5m": "crypto_5m_15m",
    "general": "other", "pop_culture": "culture", "economy": "economics", "science": "tech",
    # 01 Data Engineer categories (markets.csv.gz `category`): politics -> politics (use
    # "politics_elections" for election markets), macro -> economics, sports/crypto/geopolitics/culture/other as is
    "macro": "economics",
}

# Non-trading frictions (documented in knowledge/03_microstructure.md)
FRICTIONS = {
    "gas_per_trade_usd": 0.0,             # relayer-paid (proxy/safe wallets)
    "polymarket_deposit_fee": 0.0,        # Polymarket charges none; intermediaries may
    "polymarket_withdraw_fee": 0.0,
    "eur_to_usdc_exchange_pct": 0.0025,   # 0.10-0.25% taker on EUR/USDC + FX spread, SEPA free/<=EUR1
    "usdc_to_eur_exchange_pct": 0.0025,
    "chain_transfer_usd": 1.0,            # exchange withdrawal to Polygon, generous
    "card_onramp_pct": 0.035,             # MoonPay/card style; avoid
    "tbill_yield_annual": 0.042,          # US 3m T-bill, Oct-2026 monthly avg 4.23% (FRED/AlphaVantage)
    "builder_fee_max_taker_pct": 0.01,    # third-party front-ends (copy bots) may add up to 1% of notional
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def canonical_category(category: Optional[str]) -> str:
    if category is None:
        return "other"
    c = str(category).strip().lower()
    c = FEE_TYPE_ALIASES.get(c, c)
    return c if c in FEE_RATES["2026-11"] else "other"


def liquidity_tier(liquidity_usd: float) -> str:
    v = float(liquidity_usd or 0.0)
    return "T0" if v < 1e4 else "T1" if v < 1e5 else ("T2" if v < 1e6 else "T3")


def _tier_weights(liquidity_usd: float):
    v = max(float(liquidity_usd or 0.0), 1.0)
    lv = math.log10(v)
    names = [t for t, _ in TIER_ANCHORS]
    anchors = [math.log10(a) for _, a in TIER_ANCHORS]
    if lv <= anchors[0]:
        return {names[0]: 1.0}
    if lv >= anchors[-1]:
        return {names[-1]: 1.0}
    for i in range(len(anchors) - 1):
        if anchors[i] <= lv <= anchors[i + 1]:
            w = (lv - anchors[i]) / (anchors[i + 1] - anchors[i])
            return {names[i]: 1.0 - w, names[i + 1]: w}
    return {names[-1]: 1.0}


def _bucket_weights(p_token: float):
    """Smooth membership in cheap(<0.15) / mid / rich(>0.85) with linear blends."""
    p = min(max(p_token, 0.0), 1.0)
    if p <= 0.10:
        return {"cheap": 1.0}
    if p < 0.20:
        w = (p - 0.10) / 0.10
        return {"cheap": 1.0 - w, "mid": w}
    if p <= 0.80:
        return {"mid": 1.0}
    if p < 0.90:
        w = (p - 0.80) / 0.10
        return {"mid": 1.0 - w, "rich": w}
    return {"rich": 1.0}


def _interp_size(vals, size_usd: float) -> float:
    s = max(float(size_usd), 0.0)
    g = SIZE_GRID
    if s <= g[1]:  # linear from half-spread to $100 value
        return vals[0] + (vals[1] - vals[0]) * s / g[1]
    for i in range(1, len(g) - 1):
        if s <= g[i + 1]:
            w = (math.log(s) - math.log(g[i])) / (math.log(g[i + 1]) - math.log(g[i]))
            return vals[i] + (vals[i + 1] - vals[i]) * w
    # beyond $10k: linear extrapolation in size from the last segment (conservative)
    slope = (vals[-1] - vals[-2]) / (g[-1] - g[-2])
    return vals[-1] + max(slope, 0.0) * (s - g[-1])


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def slippage(side: str, mid: float, size_usd: float, liquidity_usd: float,
             quantile: str = "p75") -> float:
    """Distance (price units) from mid to the VWAP fill for a taker order.
    Capped by the room to the price bound (cannot buy above 0.999 / sell below 0.001)."""
    side = side.lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    table = TABLES[quantile]
    mid = min(max(float(mid), PRICE_FLOOR), PRICE_CAP)
    # selling a token at p == buying its complement at 1-p (mirrored book)
    p_lookup = mid if side == "buy" else 1.0 - mid
    s = 0.0
    for tier, wt in _tier_weights(liquidity_usd).items():
        for bucket, wb in _bucket_weights(p_lookup).items():
            s += wt * wb * _interp_size(table[tier][bucket], size_usd)
    s = max(s, MIN_TICK / 2)
    room = (PRICE_CAP - mid) if side == "buy" else (mid - PRICE_FLOOR)
    return max(min(s, room), 0.0)


def half_spread(mid: float, liquidity_usd: float, quantile: str = "p75") -> float:
    return slippage("buy", mid, 0.0, liquidity_usd, quantile)


def fee_rate(category: Optional[str], regime: str = "conservative") -> float:
    cat = canonical_category(category)
    if regime == "conservative":
        return max(FEE_RATES["2026-11"].get(cat, 0.0), FEE_RATES["2026-03"].get(cat, 0.0))
    return FEE_RATES[regime].get(cat, 0.0)


def fee(price: float, size_usd: Optional[float] = None, shares: Optional[float] = None,
        category: Optional[str] = "other", maker: bool = False,
        regime: str = "conservative") -> float:
    """Taker fee in USDC: shares * rate * p * (1-p). Makers pay 0.
    Pass either size_usd (notional at `price`) or shares."""
    if maker:
        return 0.0
    p = min(max(float(price), 0.0), 1.0)
    if shares is None:
        if size_usd is None:
            raise ValueError("give size_usd or shares")
        shares = float(size_usd) / p if p > 0 else 0.0
    f = shares * fee_rate(category, regime) * p * (1.0 - p)
    return round(f, 5)


def execution_price(side: str, mid: float, size_usd: float, liquidity_usd: float,
                    category: Optional[str] = "other", quantile: str = "p75",
                    include_fee: bool = True, regime: str = "conservative",
                    builder_fee_pct: float = 0.0) -> float:
    """All-in effective price per share for a TAKER order.
    buy  -> price paid per share  (= fill VWAP + fee per share [+ builder fee])
    sell -> price received per share (= fill VWAP - fee per share [- builder fee])
    """
    side = side.lower()
    s = slippage(side, mid, size_usd, liquidity_usd, quantile)
    if side == "buy":
        px = min(mid + s, PRICE_CAP)
        if include_fee:
            px += fee_rate(category, regime) * px * (1.0 - px) + builder_fee_pct * px
        return px
    px = max(mid - s, PRICE_FLOOR)
    if include_fee:
        px -= fee_rate(category, regime) * px * (1.0 - px) + builder_fee_pct * px
    return max(px, 0.0)


def round_trip_cost(mid: float, size_usd: float, liquidity_usd: float,
                    category: Optional[str] = "other", quantile: str = "p75",
                    regime: str = "conservative") -> float:
    """Cost (price units per share) of buying then immediately selling at an unchanged mid."""
    return (execution_price("buy", mid, size_usd, liquidity_usd, category, quantile, True, regime)
            - execution_price("sell", mid, size_usd, liquidity_usd, category, quantile, True, regime))


def is_executable(side: str, mid: float, size_usd: float, liquidity_usd: float,
                  max_slip_abs: float = 0.03, max_slip_rel: float = 0.15,
                  quantile: str = "p75") -> bool:
    """Reject trades whose modelled p75 slippage exceeds 3c absolute or 15% of the price
    of the token being bought (or of the proceeds when selling). Backtests SHOULD skip
    (not just penalise) such trades - real fills there are unreliable."""
    s = slippage(side, mid, size_usd, liquidity_usd, quantile)
    return s <= max_slip_abs and (mid <= 0 or s / mid <= max_slip_rel)


def max_size_usd(side: str, mid: float, liquidity_usd: float, max_slip_abs: float = 0.03,
                 max_slip_rel: float = 0.15, quantile: str = "p75") -> float:
    """Largest order (USD) that passes is_executable, by bisection on [0, 100k]."""
    if not is_executable(side, mid, 1.0, liquidity_usd, max_slip_abs, max_slip_rel, quantile):
        return 0.0
    lo, hi = 1.0, 1.0e5
    if is_executable(side, mid, hi, liquidity_usd, max_slip_abs, max_slip_rel, quantile):
        return hi
    for _ in range(40):
        m = (lo + hi) / 2
        if is_executable(side, mid, m, liquidity_usd, max_slip_abs, max_slip_rel, quantile):
            lo = m
        else:
            hi = m
    return lo


def volume_at_time_proxy(volume_usd_lifetime: float, start_ts: float, end_ts: float,
                         t: float, power: float = 2.0) -> float:
    """Ex-ante proxy for cumulative volume at decision time t when only LIFETIME volume is
    known (01's markets.csv.gz). Volume is usually back-loaded toward resolution, so the
    default assumes cumulative volume grows with elapsed_fraction**2 (conservative vs linear).
    Floor $1k. Pass the result as `liquidity_usd`."""
    life = max(float(end_ts) - float(start_ts), 1.0)
    frac = min(max((float(t) - float(start_ts)) / life, 0.0), 1.0)
    return max(float(volume_usd_lifetime) * frac ** power, 1000.0)


def capital_lockup_cost(size_usd: float, days_held: float,
                        annual_rate: float = FRICTIONS["tbill_yield_annual"]) -> float:
    """Opportunity cost (USD) of collateral locked until exit/resolution."""
    return float(size_usd) * annual_rate * float(days_held) / 365.0


if __name__ == "__main__":
    print("mid  liq      size   buy_px  sell_px  rt_cost  maxbuy$")
    for liq in (2e4, 3e5, 3e6):
        for mid in (0.05, 0.5, 0.92):
            for size in (100, 500, 2500, 10000):
                b = execution_price("buy", mid, size, liq, "politics")
                s = execution_price("sell", mid, size, liq, "politics")
                print(f"{mid:.2f} {liq:8.0f} {size:6d}  {b:.4f}  {s:.4f}  {b - s:.4f}"
                      f"  {max_size_usd('buy', mid, liq):8.0f}")
