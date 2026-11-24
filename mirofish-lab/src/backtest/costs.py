"""Execution-cost models for the backtester.

A cost model answers three questions for ONE outcome token (YES or NO) of one market:
    half_spread(mid, meta)            -> price units (e.g. 0.015 = 1.5 cents)
    impact(mid, notional_usd, meta)   -> extra price units paid on top of the half-spread for an order of that size
    fee_per_share(px, meta, side)     -> USDC fee per share traded (taker)

`mid` is the TOKEN's displayed mid (for a NO token: 1 - p_yes). `meta` is a dict with market fields
(volume_usd, category, is_short_auto, frac_hours_changed, neg_risk, end_ts, ts ...).

`PlaceholderCost` is the conservative interim model specified by the lab coordinator until 03's
`src/cost_model.py` exists. `load_default_cost_model()` returns 03's model wrapped in an adapter when it is
importable, else the placeholder.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]


@dataclass
class PlaceholderCost:
    """Half-spread by lifetime-volume tier (gamma liquidity is unusable) + linear impact.

    tier: volume < $100k -> 1.5c ; $100k-$1M -> 1.0c ; > $1M -> 0.5c   (half-spread, per side)
    impact: 0.5c per $1k of order notional (linear, applied to the average fill price)
    fee: taker fee rate (fraction of notional * min(p,1-p)-style schedule NOT assumed; flat 0 by default)
    """
    hs_low: float = 0.015
    hs_mid: float = 0.010
    hs_high: float = 0.005
    impact_per_1k: float = 0.005
    fee_rate: float = 0.0
    name: str = "placeholder(1.5/1.0/0.5c + 0.5c/$1k)"

    def half_spread(self, mid: float, meta: dict) -> float:
        v = float(meta.get("volume_usd", 0) or 0)
        if v < 100_000:
            return self.hs_low
        if v < 1_000_000:
            return self.hs_mid
        return self.hs_high

    def impact(self, mid: float, notional_usd: float, meta: dict) -> float:
        return self.impact_per_1k * max(notional_usd, 0.0) / 1000.0

    def fee_per_share(self, px: float, meta: dict, side: str = "BUY") -> float:
        return self.fee_rate * px


@dataclass
class ZeroCost:
    """Frictionless execution at the displayed mid (for GROSS numbers)."""
    name: str = "zero"

    def half_spread(self, mid, meta):
        return 0.0

    def impact(self, mid, notional_usd, meta):
        return 0.0

    def fee_per_share(self, px, meta, side="BUY"):
        return 0.0


@dataclass
class ScaledCost:
    """Multiply another model's spread+impact by `k` (cost sensitivity runs)."""
    base: object
    k: float = 2.0

    @property
    def name(self):
        return f"{getattr(self.base, 'name', 'base')}x{self.k:g}"

    def half_spread(self, mid, meta):
        return self.k * self.base.half_spread(mid, meta)

    def impact(self, mid, notional_usd, meta):
        return self.k * self.base.impact(mid, notional_usd, meta)

    def fee_per_share(self, px, meta, side="BUY"):
        return self.base.fee_per_share(px, meta, side)


class Adapter03:
    """Wrap 03's `src/cost_model.py`. Duck-typed: we look for, in order,
    - a class/obj with half_spread/impact/fee_per_share (used directly), or
    - functions `half_spread(mid, meta)`, `impact(mid, notional_usd, meta)`, `fee_per_share(px, meta, side)`, or
    - a function `fill_price(mid, notional_usd, side, meta)` / `taker_fee(px, shares, meta)`.
    The concrete binding is chosen in __init__ and recorded in `self.name`."""

    def __init__(self, mod):
        self.mod = mod
        self.name = "03:" + getattr(mod, "MODEL_NAME", "cost_model")
        obj = None
        for attr in ("BacktestCostModel", "CostModel", "DEFAULT_MODEL", "default_model"):
            if hasattr(mod, attr):
                obj = getattr(mod, attr)
                if isinstance(obj, type):
                    try:
                        obj = obj()
                    except TypeError:
                        obj = None
                if obj is not None and all(hasattr(obj, f) for f in ("half_spread", "impact", "fee_per_share")):
                    break
                obj = None
        if obj is None and all(hasattr(mod, f) for f in ("half_spread", "impact", "fee_per_share")):
            obj = mod
        if obj is None:
            raise ImportError("03 cost_model has no recognised interface")
        self.obj = obj

    def half_spread(self, mid, meta):
        return float(self.obj.half_spread(mid, meta))

    def impact(self, mid, notional_usd, meta):
        return float(self.obj.impact(mid, notional_usd, meta))

    def fee_per_share(self, px, meta, side="BUY"):
        return float(self.obj.fee_per_share(px, meta, side))


def load_default_cost_model(prefer_03: bool = True):
    if prefer_03 and (SRC / "cost_model.py").exists():
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        try:
            mod = importlib.import_module("cost_model")
            return Adapter03(mod)
        except Exception as e:  # pragma: no cover - depends on 03's file
            print(f"[backtest.costs] could not adapt 03 cost_model ({e}); using placeholder", file=sys.stderr)
    return PlaceholderCost()
