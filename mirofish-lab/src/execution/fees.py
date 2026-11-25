"""Fee model used by PaperBroker. Single source of truth is `src/cost_model.py` (specialist 03).

Formula (03, = docs.polymarket.com/trading/fees, taker only, makers pay 0, no fee at settlement):

    fee_usdc = shares * rate * p * (1 - p)        p = fill price

Rate selection, most specific first:
1. the market's LIVE Gamma `feeSchedule.rate` (e.g. {"rate":0.05,"exponent":1,"takerOnly":true}) -
   exactly what the exchange charges today; exponent != 1 is applied as (p(1-p))**exponent;
2. otherwise `cost_model.fee(price, shares=..., category=<Gamma feeType>)` - 03's conservative
   per-category rate (max of the 2026-11 and 2026-03 schedules);
3. if cost_model is unavailable: rate 0.05 ("other" in 2026-11).
Markets with feesEnabled == False pay 0.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("execution.fees")

try:
    from src import cost_model as _cm          # type: ignore
except Exception:  # noqa: BLE001
    try:
        import cost_model as _cm               # type: ignore
    except Exception:  # noqa: BLE001
        _cm = None

FALLBACK_RATE = 0.05


def fee_source() -> str:
    return "feeSchedule>src.cost_model" if _cm is not None else "feeSchedule>fallback0.05"


def fee_usd(price: float, shares: float, liquidity: str = "taker",
            fee_schedule: Optional[dict] = None, fees_enabled: Optional[bool] = None,
            category: Optional[str] = None) -> float:
    if shares <= 0 or fees_enabled is False:
        return 0.0
    if liquidity == "maker" and (fee_schedule or {}).get("takerOnly", True):
        return 0.0
    category = category or (fee_schedule or {}).get("_category")
    p = min(max(float(price), 0.0), 1.0)
    if fee_schedule and "rate" in fee_schedule:
        expo = float(fee_schedule.get("exponent", 1) or 1)
        return float(shares) * float(fee_schedule["rate"]) * (p * (1.0 - p)) ** expo
    if _cm is not None:
        try:
            return float(_cm.fee(p, shares=float(shares), category=category or "other", maker=False))
        except Exception as e:  # noqa: BLE001
            log.warning("cost_model.fee failed (%s); using fallback", e)
    return float(shares) * FALLBACK_RATE * p * (1.0 - p)
