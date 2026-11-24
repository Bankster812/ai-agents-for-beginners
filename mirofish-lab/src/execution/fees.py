"""Fee model used by PaperBroker (and by risk for worst-case cost).

Preference order:
1. `src/cost_model.py` from specialist 03 (microstructure), if it exists and exposes
   `taker_fee_usd(price, shares, fee_schedule)` (or `polymarket_fee(...)`) — it is the lab's
   single source of truth for costs.
2. Fallback: Polymarket's published fee curve for fee-enabled markets, as read from the
   Gamma `feeSchedule` field on each market (observed Nov 2026, e.g.
   {"rate": 0.05, "exponent": 1, "takerOnly": true, "rebateRate": 0.15}):

       fee_usdc = shares * p * rate * (p * (1 - p)) ** exponent      (taker only)

   e.g. rate=0.05, exponent=1, p=0.50 -> 1.25% of notional; p=0.90 -> 0.45%.
   Makers pay 0 when takerOnly. Markets with feesEnabled=false pay 0.
   This formula is an ASSUMPTION reconstructed from Polymarket docs; 03's model overrides it.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("execution.fees")

_cm = None
try:  # pragma: no cover - depends on whether 03 has delivered
    from src import cost_model as _cm  # type: ignore
except Exception:  # noqa: BLE001
    try:
        import cost_model as _cm  # type: ignore
    except Exception:  # noqa: BLE001
        _cm = None

# Used only when a market has fees enabled but no schedule was discovered (conservative).
DEFAULT_SCHEDULE = {"rate": 0.05, "exponent": 1, "takerOnly": True}


def fee_source() -> str:
    if _cm is not None and (hasattr(_cm, "taker_fee_usd") or hasattr(_cm, "polymarket_fee")):
        return "src.cost_model"
    return "builtin-feeSchedule"


def fee_usd(price: float, shares: float, liquidity: str = "taker",
            fee_schedule: Optional[dict] = None, fees_enabled: Optional[bool] = None) -> float:
    if shares <= 0:
        return 0.0
    if fees_enabled is False:
        return 0.0
    sched = fee_schedule or DEFAULT_SCHEDULE
    if liquidity == "maker" and sched.get("takerOnly", True):
        return 0.0
    if _cm is not None:
        fn = getattr(_cm, "taker_fee_usd", None) or getattr(_cm, "polymarket_fee", None)
        if fn is not None:
            try:
                return float(fn(price, shares, sched))
            except Exception as e:  # noqa: BLE001
                log.warning("cost_model fee fn failed (%s); using builtin", e)
    rate = float(sched.get("rate", DEFAULT_SCHEDULE["rate"]))
    expo = float(sched.get("exponent", 1))
    p = min(max(price, 0.0), 1.0)
    return shares * p * rate * (p * (1.0 - p)) ** expo
