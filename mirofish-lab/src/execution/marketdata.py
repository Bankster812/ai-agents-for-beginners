"""Public (unauthenticated, read-only) Polymarket data: Gamma discovery + CLOB books.

* Thread-safe token-bucket rate limiter shared by all calls.
* Exponential backoff with jitter on 429 / 5xx / network errors; honours Retry-After.
* No credentials are ever sent from this module.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Iterable, Optional

import requests

from .models import MarketInfo, OrderBook

log = logging.getLogger("execution.marketdata")

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
DATA_HOST = "https://data-api.polymarket.com"


class RateLimiter:
    """Token bucket: `rate` requests/second, burst `burst`."""

    def __init__(self, rate: float = 5.0, burst: int = 10):
        self.rate, self.burst = float(rate), float(burst)
        self.tokens = float(burst)
        self.t = time.monotonic()
        self.lock = threading.Lock()
        self.penalty_until = 0.0

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                if now < self.penalty_until:
                    wait = self.penalty_until - now
                else:
                    self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
                    self.t = now
                    if self.tokens >= 1:
                        self.tokens -= 1
                        return
                    wait = (1 - self.tokens) / self.rate
            time.sleep(min(wait, 5.0))

    def penalise(self, seconds: float) -> None:
        with self.lock:
            self.penalty_until = max(self.penalty_until, time.monotonic() + seconds)


class HttpStats:
    def __init__(self):
        self.requests = 0
        self.retries = 0
        self.rate_limited = 0
        self.errors = 0


class PublicClient:
    def __init__(self, rate: float = 5.0, burst: int = 10, timeout: float = 20.0, tries: int = 6,
                 user_agent: str = "mirofish-lab-exec/0.1 (read-only)"):
        self.limiter = RateLimiter(rate, burst)
        self.timeout, self.tries = timeout, tries
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.stats = HttpStats()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def _request(self, method: str, url: str, **kw):
        last = None
        for attempt in range(self.tries):
            self.limiter.acquire()
            try:
                with self._lock:
                    self.stats.requests += 1
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429 or r.status_code >= 500:
                    ra = r.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.replace(".", "").isdigit() else min(60, 2 ** attempt) + random.random()
                    if r.status_code == 429:
                        self.stats.rate_limited += 1
                        self.limiter.penalise(wait)
                    self.stats.retries += 1
                    log.warning("http_backoff", extra={"evt": "http_backoff", "status": r.status_code,
                                                         "url": url.split("?")[0], "wait_s": round(wait, 2)})
                    time.sleep(wait)
                    last = f"HTTP {r.status_code}"
                    continue
                # 4xx other than 429: not retryable
                self.stats.errors += 1
                raise RuntimeError(f"HTTP {r.status_code} for {url.split('?')[0]}: {r.text[:200]}")
            except (requests.ConnectionError, requests.Timeout) as e:
                self.stats.retries += 1
                last = repr(e)
                time.sleep(min(30, 2 ** attempt) + random.random())
        self.stats.errors += 1
        raise RuntimeError(f"giving up on {url.split('?')[0]}: {last}")

    # ------------------------------------------------------------------ CLOB
    def get_book(self, token_id: str) -> OrderBook:
        return OrderBook.from_clob(self._request("GET", f"{CLOB_HOST}/book", params={"token_id": token_id}))

    def get_books(self, token_ids: Iterable[str], batch: int = 50) -> dict[str, OrderBook]:
        """Batched POST /books (falls back to GET /book per token on error)."""
        ids = list(dict.fromkeys(token_ids))
        out: dict[str, OrderBook] = {}
        for i in range(0, len(ids), batch):
            chunk = ids[i:i + batch]
            try:
                raw = self._request("POST", f"{CLOB_HOST}/books",
                                    data=json.dumps([{"token_id": t} for t in chunk]),
                                    headers={"Content-Type": "application/json"})
                for b in raw:
                    ob = OrderBook.from_clob(b)
                    out[ob.token_id] = ob
            except Exception as e:  # noqa: BLE001
                log.warning("books_batch_failed", extra={"evt": "books_batch_failed", "err": str(e)[:200]})
                for t in chunk:
                    try:
                        out[t] = self.get_book(t)
                    except Exception as e2:  # noqa: BLE001
                        log.warning("book_failed", extra={"evt": "book_failed", "token": t[:12], "err": str(e2)[:200]})
        return out

    def get_mid(self, token_id: str) -> Optional[float]:
        d = self._request("GET", f"{CLOB_HOST}/midpoint", params={"token_id": token_id})
        try:
            return float(d.get("mid"))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ Gamma
    def discover_markets(self, min_volume_24h: float = 10_000, min_liquidity: float = 5_000,
                         max_markets: int = 30, pages: int = 3, page_size: int = 100) -> list[MarketInfo]:
        """Active, order-book-enabled markets sorted by 24h volume."""
        found: list[MarketInfo] = []
        for page in range(pages):
            raw = self._request("GET", f"{GAMMA_HOST}/markets", params={
                "active": "true", "closed": "false", "archived": "false", "limit": page_size,
                "offset": page * page_size, "order": "volume24hr", "ascending": "false"})
            if not raw:
                break
            for m in raw:
                mi = parse_gamma_market(m)
                if mi is None:
                    continue
                if mi.volume_24h_usd < min_volume_24h or mi.liquidity_usd < min_liquidity:
                    continue
                found.append(mi)
            if len(found) >= max_markets or len(raw) < page_size:
                break
        return found[:max_markets]

    def market_status(self, condition_id: str) -> Optional[dict]:
        raw = self._request("GET", f"{GAMMA_HOST}/markets", params={"condition_ids": condition_id, "limit": 1})
        return raw[0] if raw else None


def _jl(v, default):
    if v is None:
        return default
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v


def parse_gamma_market(m: dict) -> Optional[MarketInfo]:
    if not m.get("enableOrderBook") or not m.get("acceptingOrders", True) or m.get("closed"):
        return None
    tokens = [str(t) for t in _jl(m.get("clobTokenIds"), [])]
    if len(tokens) < 2:
        return None
    tags = []
    for e in m.get("events") or []:
        for t in e.get("tags") or []:
            tags.append(str(t.get("slug") or t.get("label") or "").lower())
    for t in m.get("tags") or []:
        tags.append(str(t.get("slug") or t.get("label") or "").lower() if isinstance(t, dict) else str(t).lower())
    return MarketInfo(
        condition_id=str(m.get("conditionId", "")), question=str(m.get("question", "")),
        slug=str(m.get("slug", "")), token_ids=tokens, outcomes=[str(o) for o in _jl(m.get("outcomes"), [])],
        liquidity_usd=float(m.get("liquidityNum") or m.get("liquidity") or 0),
        volume_24h_usd=float(m.get("volume24hr") or 0), end_date=str(m.get("endDate", "")),
        neg_risk=bool(m.get("negRisk")), tags=[t for t in tags if t],
        category=str(m.get("category") or m.get("feeType") or "").lower(),
        fee_schedule=m.get("feeSchedule") if isinstance(m.get("feeSchedule"), dict) else None,
        fees_enabled=bool(m.get("feesEnabled", False)),
        min_order_size=float(m.get("orderMinSize") or 5), tick_size=float(m.get("orderPriceMinTickSize") or 0.01),
        accepting_orders=bool(m.get("acceptingOrders", True)))
