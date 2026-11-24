"""Plain data types shared by broker / risk / strategy / runner.

Conventions
-----------
* Prices are Polymarket outcome-token prices in USDC per share, 0 < p < 1.
* Sizes are in SHARES (outcome tokens). Notional (USDC) = price * size.
* Order books are NORMALISED here: bids sorted best-first (descending price),
  asks sorted best-first (ascending price). The raw CLOB /book response is the
  opposite (bids ascending, asks descending: best level is the LAST element).
* There is no short selling on Polymarket. "Betting against" = BUY the other
  outcome token. A SELL is only valid for shares you hold.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass
class OrderBook:
    token_id: str
    bids: list[Level]           # best (highest) first
    asks: list[Level]           # best (lowest) first
    ts: float = field(default_factory=time.time)
    tick_size: float = 0.01
    min_order_size: float = 5.0
    neg_risk: bool = False
    market: str = ""            # condition id

    @classmethod
    def from_clob(cls, raw: dict) -> "OrderBook":
        bids = sorted((Level(float(x["price"]), float(x["size"])) for x in raw.get("bids", []) or []),
                      key=lambda l: -l.price)
        asks = sorted((Level(float(x["price"]), float(x["size"])) for x in raw.get("asks", []) or []),
                      key=lambda l: l.price)
        ts = raw.get("timestamp")
        try:
            ts = float(ts) / 1000.0 if ts else time.time()
        except (TypeError, ValueError):
            ts = time.time()
        return cls(token_id=str(raw.get("asset_id", "")), bids=bids, asks=asks, ts=ts,
                   tick_size=float(raw.get("tick_size") or 0.01),
                   min_order_size=float(raw.get("min_order_size") or 5),
                   neg_risk=bool(raw.get("neg_risk") or False), market=str(raw.get("market", "")))

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def depth_usd(self, side: str, within: float = 0.05) -> float:
        """USDC notional resting within `within` (absolute price) of the best price on one side."""
        if side == BUY:   # liquidity a buyer can take = asks
            if not self.asks:
                return 0.0
            lim = self.asks[0].price + within
            return sum(l.price * l.size for l in self.asks if l.price <= lim + 1e-12)
        if not self.bids:
            return 0.0
        lim = self.bids[0].price - within
        return sum(l.price * l.size for l in self.bids if l.price >= lim - 1e-12)


@dataclass
class MarketInfo:
    """Static-ish market metadata from Gamma discovery."""
    condition_id: str
    question: str
    slug: str
    token_ids: list[str]
    outcomes: list[str]
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    end_date: str = ""
    neg_risk: bool = False
    tags: list[str] = field(default_factory=list)
    category: str = ""
    fee_schedule: Optional[dict] = None     # e.g. {"rate":0.05,"exponent":1,"takerOnly":true,...}
    fees_enabled: bool = False
    min_order_size: float = 5.0
    tick_size: float = 0.01
    accepting_orders: bool = True


@dataclass
class MarketSnapshot:
    """What a strategy sees on each tick for one market."""
    market: MarketInfo
    books: dict[str, OrderBook]                 # token_id -> book
    ts: float
    mode: str                                   # "paper" | "live"
    positions: dict[str, float] = field(default_factory=dict)   # token_id -> shares held
    equity_usd: float = 0.0
    cash_usd: float = 0.0


@dataclass
class OrderIntent:
    """A strategy's request. Nothing is sent until risk approves it."""
    token_id: str
    side: str                      # BUY | SELL
    price: float                   # limit price
    size: float                    # shares
    strategy: str = "unknown"
    intent_key: str = ""           # strategy-chosen stable key -> idempotent client order id
    tif: str = "GTC"               # GTC (rest remainder) | FAK (fill-and-kill) | FOK
    reduce_only: bool = False
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.price * self.size

    def client_order_id(self) -> str:
        """Deterministic id: the same intent (same strategy + intent_key) maps to the same id,
        so a crash/restart that replays a tick cannot double-submit."""
        key = self.intent_key or f"{self.token_id}|{self.side}|{self.price:.4f}|{self.size:.4f}"
        raw = f"{self.strategy}|{key}"
        return "c" + hashlib.sha256(raw.encode()).hexdigest()[:23]


@dataclass
class Order:
    client_order_id: str
    token_id: str
    side: str
    price: float
    size: float
    filled: float = 0.0
    status: str = "open"           # open | filled | cancelled | rejected | partial
    tif: str = "GTC"
    strategy: str = ""
    exchange_order_id: str = ""
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    avg_fill_price: float = 0.0
    fees_paid: float = 0.0
    reason: str = ""

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["remaining"] = self.remaining
        return d


@dataclass
class Fill:
    client_order_id: str
    token_id: str
    side: str
    price: float
    size: float
    fee: float
    liquidity: str                 # "taker" | "maker"
    ts: float = field(default_factory=time.time)


@dataclass
class Position:
    token_id: str
    size: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0

    @property
    def cost_basis(self) -> float:
        return self.size * self.avg_cost
