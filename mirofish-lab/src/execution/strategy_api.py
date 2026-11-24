"""Strategy interface. A strategy is pure decision logic: it sees a MarketSnapshot and returns
OrderIntents. It never talks to a broker, never sees credentials, and cannot bypass risk.

To plug in a validated strategy (from 04/05, approved by 06):
    class MyStrategy:
        name = "my_strategy_v1"
        live_allowed = True          # only after the go-live checklist in knowledge/07_execution.md
        def on_tick(self, snap: MarketSnapshot) -> list[OrderIntent]: ...
and list it in config.yaml -> strategies: [{path: "pkg.module:MyStrategy", params: {...}}]
"""
from __future__ import annotations

import importlib
import math
from typing import Protocol, runtime_checkable

from .models import BUY, SELL, MarketSnapshot, OrderIntent


@runtime_checkable
class Strategy(Protocol):
    name: str
    live_allowed: bool

    def on_tick(self, snap: MarketSnapshot) -> list[OrderIntent]: ...


def load_strategy(path: str, params: dict | None = None) -> Strategy:
    mod, _, cls = path.partition(":")
    obj = getattr(importlib.import_module(mod), cls)(**(params or {}))
    if not isinstance(obj, Strategy):
        raise TypeError(f"{path} does not implement Strategy (name, live_allowed, on_tick)")
    return obj


def _round_tick(p: float, tick: float) -> float:
    return round(round(p / tick) * tick, 6)


class ExampleWiringStrategy:
    """WIRING TEST ONLY - has no edge and is not meant to make money.

    * In live mode it returns [] unconditionally (and live_allowed=False makes the runner refuse to
      even load it when mode=live).
    * In paper mode, on each market it trades a tiny fixed ticket to exercise every code path:
        1. tick k:   BUY  `ticket_usd` of the cheaper-spread outcome at the best ask (taker, FAK)
        2. same tick: post a passive GTC BID one tick below best bid for `ticket_usd` (resting)
        3. after `hold_ticks`: SELL the held shares at the best bid (taker, FAK) and cancel nothing -
           the runner's shutdown cancels resting orders.
    """

    name = "example_wiring"
    live_allowed = False

    def __init__(self, ticket_usd: float = 5.0, hold_ticks: int = 6, max_markets: int = 3):
        self.ticket_usd, self.hold_ticks, self.max_markets = ticket_usd, hold_ticks, max_markets
        self.entered: dict[str, int] = {}      # condition_id -> tick index at entry
        self.tick = 0

    def on_tick(self, snap: MarketSnapshot) -> list[OrderIntent]:
        if snap.mode != "paper":
            return []
        self.tick += 1
        m = snap.market
        out: list[OrderIntent] = []
        cid = m.condition_id
        if cid not in self.entered:
            if len(self.entered) >= self.max_markets:
                return []
            tid = min(snap.books, key=lambda t: snap.books[t].spread if snap.books[t].spread is not None else 9)
            b = snap.books[tid]
            if b.best_ask is None or b.best_bid is None:
                return []
            size = max(b.min_order_size, round(self.ticket_usd / b.best_ask, 2))
            out.append(OrderIntent(tid, BUY, b.best_ask, size, self.name, intent_key=f"{cid}:entry",
                                   tif="FAK", reason="wiring: taker entry"))
            bid_px = _round_tick(b.best_bid - b.tick_size, b.tick_size)
            if bid_px > 0:
                out.append(OrderIntent(tid, BUY, bid_px, max(b.min_order_size, round(self.ticket_usd / bid_px, 2)),
                                       self.name, intent_key=f"{cid}:passive", tif="GTC", reason="wiring: resting bid"))
            self.entered[cid] = self.tick
            return out
        if self.tick - self.entered[cid] >= self.hold_ticks:
            for tid, held in snap.positions.items():
                if held > 0 and tid in snap.books and snap.books[tid].best_bid:
                    b = snap.books[tid]
                    out.append(OrderIntent(tid, SELL, b.best_bid, math.floor(held * 100) / 100, self.name,
                                           intent_key=f"{cid}:exit:{self.entered[cid]}", tif="FAK",
                                           reason="wiring: taker exit"))
        return out
