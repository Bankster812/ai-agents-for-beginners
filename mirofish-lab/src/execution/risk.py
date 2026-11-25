"""Pre-trade risk gate. Every order, paper or live, goes through RiskManager.check() first.

Hard limits (defaults; all configurable in config.yaml -> risk:)
  max_order_usd            50      notional of one order
  max_position_pct         0.05    per-market exposure (both outcome tokens + open BUYs) / equity
  max_total_exposure_pct   0.30    all positions at cost + open BUYs / equity
  max_daily_loss_pct       0.03    equity drop vs. start of UTC day -> HALT for the rest of the day
  max_drawdown_pct         0.15    equity drop vs. high-water mark -> HALT until manual reset
  max_orders_per_hour      30
  min_market_liquidity_usd 5000    Gamma liquidityNum
  min_book_depth_usd       200     visible depth within 5c on the side we take
  max_spread               0.05    absolute (5 cents)
  blacklist_categories     [...]   matched against market tags / category / slug
  kill_switch_file         state/KILL   exists -> cancel everything, trade nothing

Reduce-only SELLs are exempt from exposure/size checks but NOT from kill switch / halts / sanity.
Halt state is persisted (state/risk_state.json) so a restart cannot clear a halt.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import BUY, SELL, MarketInfo, OrderBook, OrderIntent

log = logging.getLogger("execution.risk")


@dataclass
class RiskLimits:
    max_order_usd: float = 50.0
    max_position_pct: float = 0.05
    max_total_exposure_pct: float = 0.30
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.15
    max_orders_per_hour: int = 30
    min_market_liquidity_usd: float = 5_000.0
    min_book_depth_usd: float = 200.0
    max_spread: float = 0.05
    min_price: float = 0.02
    max_price: float = 0.98
    blacklist_categories: list[str] = field(default_factory=lambda: ["crypto-15m", "up-or-down"])
    kill_switch_file: str = "state/KILL"
    state_file: str = "state/risk_state.json"

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "RiskLimits":
        d = dict(d or {})
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        unknown = set(d) - set(known)
        if unknown:
            raise ValueError(f"unknown risk keys: {sorted(unknown)}")
        return cls(**known)


@dataclass
class AccountView:
    """Everything risk needs to know about the account at decision time."""
    equity: float
    free_cash: float
    # token_id -> USDC exposure at cost (positions) ; open BUY orders' remaining notional
    position_cost: dict[str, float]
    open_buy_notional: dict[str, float]
    held_shares: dict[str, float]


@dataclass
class Decision:
    ok: bool
    reason: str = ""
    halt: bool = False        # caller should stop trading (kill / halt)


class RiskManager:
    def __init__(self, limits: RiskLimits, root: Path | str = ".", notifier=None, clock=time.time):
        self.l = limits
        self.root = Path(root)
        self.kill_path = self._p(limits.kill_switch_file)
        self.state_path = self._p(limits.state_file)
        self.notifier = notifier
        self.clock = clock
        self.order_times: deque[float] = deque()
        self.state = {"day": None, "day_start_equity": None, "peak_equity": None,
                      "halted_day": None, "drawdown_halt": False, "halt_reason": ""}
        self._load()

    def _p(self, s: str) -> Path:
        p = Path(s)
        return p if p.is_absolute() else self.root / p

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self.state_path.exists():
            try:
                self.state.update(json.loads(self.state_path.read_text()))
                self.order_times.extend(t for t in self.state.get("order_times", []) if self.clock() - t < 3600)
            except (OSError, ValueError):
                log.error("risk_state_unreadable -> halting until fixed", extra={"evt": "risk_state_bad"})
                self.state["drawdown_halt"] = True
                self.state["halt_reason"] = "risk state file unreadable"

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        s = dict(self.state, order_times=list(self.order_times))
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        tmp.replace(self.state_path)

    def _alert(self, msg: str) -> None:
        log.error(msg, extra={"evt": "risk_alert"})
        if self.notifier:
            try:
                self.notifier(msg)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ equity tracking / halts
    def kill_switch_active(self) -> bool:
        return self.kill_path.exists()

    def update_equity(self, equity: float) -> None:
        """Call every tick with marked-to-market equity; trips daily-loss / drawdown halts."""
        day = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")
        st = self.state
        if st["day"] != day:
            st["day"], st["day_start_equity"] = day, equity
        if st["peak_equity"] is None or equity > st["peak_equity"]:
            st["peak_equity"] = equity
        dse, peak = st["day_start_equity"], st["peak_equity"]
        if dse and equity < dse * (1 - self.l.max_daily_loss_pct) and st["halted_day"] != day:
            st["halted_day"] = day
            st["halt_reason"] = f"daily loss {(1 - equity / dse):.2%} >= {self.l.max_daily_loss_pct:.0%}"
            self._alert(f"RISK HALT (rest of UTC day {day}): {st['halt_reason']}")
        if peak and equity < peak * (1 - self.l.max_drawdown_pct) and not st["drawdown_halt"]:
            st["drawdown_halt"] = True
            st["halt_reason"] = f"drawdown {(1 - equity / peak):.2%} >= {self.l.max_drawdown_pct:.0%} (manual reset needed)"
            self._alert(f"RISK KILL: {st['halt_reason']}")
        self._save()

    def halted(self) -> Optional[str]:
        if self.kill_switch_active():
            return f"kill switch file present: {self.kill_path}"
        if self.state.get("drawdown_halt"):
            return "drawdown halt: " + str(self.state.get("halt_reason"))
        day = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("halted_day") == day:
            return "daily loss halt: " + str(self.state.get("halt_reason"))
        return None

    def reset_drawdown(self, equity: float) -> None:
        """Manual operator action only (CLI: runner --reset-drawdown)."""
        self.state.update(drawdown_halt=False, halt_reason="", peak_equity=equity)
        self._save()
        log.warning("drawdown halt manually reset", extra={"evt": "drawdown_reset", "equity": equity})

    # ------------------------------------------------------------------ pre-trade check
    def _blacklisted(self, m: Optional[MarketInfo]) -> Optional[str]:
        if m is None:
            return None
        hay = set(m.tags) | {m.category}
        for b in self.l.blacklist_categories:
            b = b.lower()
            if b in hay or (b and b in m.slug.lower()):
                return b
        return None

    def check(self, it: OrderIntent, market: Optional[MarketInfo], book: Optional[OrderBook],
              acct: AccountView) -> Decision:
        h = self.halted()
        if h:
            return Decision(False, h, halt=True)
        if it.side not in (BUY, SELL):
            return Decision(False, f"bad side {it.side}")
        if not (0 < it.price < 1) or it.size <= 0:
            return Decision(False, "price must be in (0,1) and size > 0")
        if book is None:
            return Decision(False, "no order book")
        tick = book.tick_size or (market.tick_size if market else 0.01)
        if abs(round(it.price / tick) * tick - it.price) > 1e-9:
            return Decision(False, f"price {it.price} not on tick {tick}")
        min_sz = book.min_order_size or (market.min_order_size if market else 5)
        if it.size < min_sz:
            return Decision(False, f"size {it.size} < market min {min_sz}")
        if market is not None and not market.accepting_orders:
            return Decision(False, "market not accepting orders")
        now = self.clock()
        while self.order_times and now - self.order_times[0] > 3600:
            self.order_times.popleft()
        if len(self.order_times) >= self.l.max_orders_per_hour:
            return Decision(False, f"orders/hour limit {self.l.max_orders_per_hour}")

        reduce_only = it.side == SELL
        if reduce_only:
            held = acct.held_shares.get(it.token_id, 0.0)
            if it.size > held + 1e-9:
                return Decision(False, f"SELL {it.size} > held {held} (no shorting)")
        else:
            bl = self._blacklisted(market)
            if bl:
                return Decision(False, f"blacklisted category '{bl}'")
            if not (self.l.min_price <= it.price <= self.l.max_price):
                return Decision(False, f"price {it.price} outside [{self.l.min_price},{self.l.max_price}]")
            if market is not None and market.liquidity_usd < self.l.min_market_liquidity_usd:
                return Decision(False, f"market liquidity ${market.liquidity_usd:,.0f} < ${self.l.min_market_liquidity_usd:,.0f}")
            sp = book.spread
            if sp is None:
                return Decision(False, "one-sided book")
            if sp > self.l.max_spread + 1e-12:
                return Decision(False, f"spread {sp:.3f} > {self.l.max_spread}")
            depth = book.depth_usd(BUY, within=0.05)
            if depth < self.l.min_book_depth_usd:
                return Decision(False, f"ask depth ${depth:,.0f} < ${self.l.min_book_depth_usd:,.0f}")
            notional = it.notional
            if notional > self.l.max_order_usd + 1e-9:
                return Decision(False, f"order ${notional:.2f} > max_order_usd ${self.l.max_order_usd}")
            if notional * 1.03 > acct.free_cash:
                return Decision(False, f"insufficient free cash ${acct.free_cash:.2f}")
            eq = max(acct.equity, 0.0)
            mkt_tokens = set(market.token_ids) if market else {it.token_id}
            mkt_exp = sum(acct.position_cost.get(t, 0) + acct.open_buy_notional.get(t, 0) for t in mkt_tokens)
            if mkt_exp + notional > self.l.max_position_pct * eq + 1e-9:
                return Decision(False, f"market exposure ${mkt_exp + notional:.2f} > {self.l.max_position_pct:.0%} of equity ${eq:,.2f}")
            tot = sum(acct.position_cost.values()) + sum(acct.open_buy_notional.values())
            if tot + notional > self.l.max_total_exposure_pct * eq + 1e-9:
                return Decision(False, f"total exposure ${tot + notional:.2f} > {self.l.max_total_exposure_pct:.0%} of equity")
        return Decision(True, "ok")

    def record_order(self) -> None:
        self.order_times.append(self.clock())
        self._save()
