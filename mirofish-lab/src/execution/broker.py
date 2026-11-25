"""Broker abstraction + PaperBroker (live public book, simulated fills) + PolymarketLiveBroker.

SAFETY
------
* PaperBroker never needs or touches credentials.
* PolymarketLiveBroker refuses to construct unless ALL of these hold:
    - env TRADING_MODE == "live"
    - env I_UNDERSTAND_REAL_MONEY == "yes"
    - the caller passes confirm_live=True (runner does so only when config mode == "live")
  Credentials are read ONLY from environment variables (optionally loaded from a gitignored .env
  by the runner). They are never logged, never written to disk, never returned in repr().
"""
from __future__ import annotations

import abc
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .fees import fee_usd
from .marketdata import DATA_HOST, PublicClient
from .models import BUY, SELL, Fill, MarketInfo, Order, OrderBook, OrderIntent, Position

log = logging.getLogger("execution.broker")


class BrokerError(RuntimeError):
    pass


class LiveTradingRefused(BrokerError):
    pass


class Broker(abc.ABC):
    mode: str = "abstract"

    @abc.abstractmethod
    def get_book(self, token_id: str) -> OrderBook: ...

    def get_mid(self, token_id: str) -> Optional[float]:
        return self.get_book(token_id).mid

    @abc.abstractmethod
    def place_limit_order(self, intent: OrderIntent, book: Optional[OrderBook] = None,
                          market: Optional[MarketInfo] = None) -> Order: ...

    @abc.abstractmethod
    def cancel(self, client_order_id: str) -> bool: ...

    @abc.abstractmethod
    def cancel_all(self) -> int: ...

    @abc.abstractmethod
    def open_orders(self) -> list[Order]: ...

    @abc.abstractmethod
    def positions(self) -> dict[str, Position]: ...

    @abc.abstractmethod
    def balance(self) -> float:
        """Free USDC cash (not counting value of positions)."""

    def equity(self, marks: dict[str, float]) -> float:
        eq = self.balance()
        for tid, p in self.positions().items():
            m = marks.get(tid)
            eq += p.size * (m if m is not None else p.avg_cost)
        return eq

    def on_book(self, book: OrderBook, market: Optional[MarketInfo] = None) -> list[Fill]:
        """Hook called by the runner with every fresh book (paper: match resting orders)."""
        return []

    def close(self) -> None:
        pass


# =============================================================================== PAPER
_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (key TEXT PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY, token_id TEXT NOT NULL, side TEXT NOT NULL,
  price REAL NOT NULL, size REAL NOT NULL, filled REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL, tif TEXT NOT NULL, strategy TEXT, created_ts REAL, updated_ts REAL,
  avg_fill_price REAL DEFAULT 0, fees_paid REAL DEFAULT 0, reason TEXT,
  fee_schedule TEXT, fees_enabled INTEGER);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT, token_id TEXT, side TEXT,
  price REAL, size REAL, fee REAL, liquidity TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS positions (
  token_id TEXT PRIMARY KEY, size REAL NOT NULL, avg_cost REAL NOT NULL, realized_pnl REAL NOT NULL);
CREATE TABLE IF NOT EXISTS marks (token_id TEXT PRIMARY KEY, price REAL, ts REAL);
"""


class PaperBroker(Broker):
    """Simulated broker that fills against the LIVE public order book.

    Fill model
    ----------
    * Marketable part of a limit order walks the opposite side of the *current* book level by level
      up to the limit price (taker fee per level via fees.fee_usd). Liquidity taken from a given book
      snapshot is remembered, so two orders on the same snapshot cannot both eat the same level.
    * GTC remainder rests at the limit price. It fills later ONLY when a fresh book shows the opposite
      side trading through our price (best ask <= our bid / best bid >= our ask), at OUR price, as maker
      (maker fee: 0 when the schedule is takerOnly). We do not see the trade tape, so fills from takers
      hitting us at an unchanged book are NOT simulated -> passive fill rate is pessimistic, adverse
      selection is realistic (we fill when the price moves through us).
    * FAK: remainder cancelled. FOK: all-or-nothing against the visible book.
    * No shorting: SELL requires held, un-reserved shares. BUY requires free cash (cash minus cash
      reserved by open BUY orders incl. worst-case taker fee).
    """

    mode = "paper"

    def __init__(self, db_path: str | Path, starting_cash: float = 10_800.0,
                 data: Optional[PublicClient] = None, default_fee_schedule: Optional[dict] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = data
        self.default_fee_schedule = default_fee_schedule
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)
        if self._acct("cash") is None:
            self._set_acct("cash", float(starting_cash))
            self._set_acct("starting_cash", float(starting_cash))
            log.info("paper_account_created", extra={"evt": "paper_account_created", "cash": starting_cash})
        self._consumed: dict[str, tuple[float, dict[float, float]]] = {}
        self.fill_listeners = []   # callables(Fill)

    # ------------------------------------------------------------------ db helpers
    def _acct(self, key: str) -> Optional[float]:
        r = self.db.execute("SELECT value FROM account WHERE key=?", (key,)).fetchone()
        return None if r is None else float(r[0])

    def _set_acct(self, key: str, value: float) -> None:
        self.db.execute("INSERT INTO account(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    def _row_to_order(self, r) -> Order:
        (cid, tid, side, price, size, filled, status, tif, strat, cts, uts, afp, fees, reason, _fs, _fe) = r
        return Order(client_order_id=cid, token_id=tid, side=side, price=price, size=size, filled=filled,
                     status=status, tif=tif, strategy=strat or "", created_ts=cts or 0, updated_ts=uts or 0,
                     avg_fill_price=afp or 0, fees_paid=fees or 0, reason=reason or "")

    def get_order(self, client_order_id: str) -> Optional[Order]:
        r = self.db.execute("SELECT * FROM orders WHERE client_order_id=?", (client_order_id,)).fetchone()
        return None if r is None else self._row_to_order(r)

    def _save_order(self, o: Order, fee_schedule=None, fees_enabled=None) -> None:
        o.updated_ts = time.time()
        self.db.execute(
            """INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(client_order_id) DO UPDATE SET filled=excluded.filled, status=excluded.status,
               updated_ts=excluded.updated_ts, avg_fill_price=excluded.avg_fill_price,
               fees_paid=excluded.fees_paid, reason=excluded.reason""",
            (o.client_order_id, o.token_id, o.side, o.price, o.size, o.filled, o.status, o.tif, o.strategy,
             o.created_ts, o.updated_ts, o.avg_fill_price, o.fees_paid, o.reason,
             json.dumps(fee_schedule) if fee_schedule else None,
             None if fees_enabled is None else int(bool(fees_enabled))))

    def _position(self, token_id: str) -> Position:
        r = self.db.execute("SELECT size, avg_cost, realized_pnl FROM positions WHERE token_id=?", (token_id,)).fetchone()
        return Position(token_id, *r) if r else Position(token_id)

    def _save_position(self, p: Position) -> None:
        if abs(p.size) < 1e-9:
            p.size, p.avg_cost = 0.0, 0.0
        self.db.execute("""INSERT INTO positions VALUES(?,?,?,?) ON CONFLICT(token_id) DO UPDATE SET
                           size=excluded.size, avg_cost=excluded.avg_cost, realized_pnl=excluded.realized_pnl""",
                        (p.token_id, p.size, p.avg_cost, p.realized_pnl))

    # ------------------------------------------------------------------ Broker API
    def get_book(self, token_id: str) -> OrderBook:
        if self.data is None:
            raise BrokerError("PaperBroker has no PublicClient for live books")
        return self.data.get_book(token_id)

    def balance(self) -> float:
        return float(self._acct("cash") or 0.0)

    def starting_cash(self) -> float:
        return float(self._acct("starting_cash") or 0.0)

    def reserved_cash(self) -> float:
        tot = 0.0
        for o in self.open_orders():
            if o.side == BUY:
                tot += o.remaining * o.price
        return tot

    def free_cash(self) -> float:
        return self.balance() - self.reserved_cash()

    def reserved_shares(self, token_id: str) -> float:
        r = self.db.execute("SELECT COALESCE(SUM(size-filled),0) FROM orders WHERE token_id=? AND side='SELL' "
                            "AND status IN ('open','partial')", (token_id,)).fetchone()
        return float(r[0])

    def positions(self) -> dict[str, Position]:
        rows = self.db.execute("SELECT token_id,size,avg_cost,realized_pnl FROM positions WHERE size>1e-9").fetchall()
        return {r[0]: Position(*r) for r in rows}

    def realized_pnl_total(self) -> float:
        r = self.db.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM positions").fetchone()
        return float(r[0])

    def open_orders(self) -> list[Order]:
        rows = self.db.execute("SELECT * FROM orders WHERE status IN ('open','partial')").fetchall()
        return [self._row_to_order(r) for r in rows]

    def fills(self, limit: int = 1000) -> list[Fill]:
        rows = self.db.execute("SELECT client_order_id,token_id,side,price,size,fee,liquidity,ts FROM fills "
                               "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Fill(*r) for r in rows]

    def save_marks(self, marks: dict[str, float]) -> None:
        now = time.time()
        for t, p in marks.items():
            if p is not None:
                self.db.execute("INSERT INTO marks VALUES(?,?,?) ON CONFLICT(token_id) DO UPDATE SET price=excluded.price,"
                                " ts=excluded.ts", (t, p, now))

    def last_marks(self) -> dict[str, float]:
        return {r[0]: r[1] for r in self.db.execute("SELECT token_id, price FROM marks").fetchall()}

    # ------------------------------------------------------------------ matching
    def _available(self, book: OrderBook, side_levels: str) -> list[tuple[float, float]]:
        """Levels on one side of `book` minus liquidity already consumed from this exact snapshot."""
        levels = book.asks if side_levels == "asks" else book.bids
        ts, used = self._consumed.get(book.token_id, (None, {}))
        if ts != book.ts:
            used = {}
            self._consumed[book.token_id] = (book.ts, used)
        return [(l.price, max(0.0, l.size - used.get(l.price, 0.0))) for l in levels]

    def _consume(self, book: OrderBook, price: float, size: float) -> None:
        ts, used = self._consumed.setdefault(book.token_id, (book.ts, {}))
        used[price] = used.get(price, 0.0) + size

    def _apply_fill(self, o: Order, price: float, size: float, liquidity: str, fee_schedule, fees_enabled) -> Fill:
        fee = fee_usd(price, size, liquidity, fee_schedule or self.default_fee_schedule, fees_enabled)
        cash = self.balance()
        pos = self._position(o.token_id)
        if o.side == BUY:
            cash -= price * size + fee
            new_size = pos.size + size
            pos.avg_cost = (pos.cost_basis + price * size + fee) / new_size   # fee capitalised into cost
            pos.size = new_size
        else:
            cash += price * size - fee
            pos.realized_pnl += (price - pos.avg_cost) * size - fee
            pos.size -= size
        self._set_acct("cash", cash)
        self._save_position(pos)
        prev = o.filled
        o.filled += size
        o.avg_fill_price = (o.avg_fill_price * prev + price * size) / o.filled
        o.fees_paid += fee
        f = Fill(o.client_order_id, o.token_id, o.side, price, size, fee, liquidity)
        self.db.execute("INSERT INTO fills(client_order_id,token_id,side,price,size,fee,liquidity,ts) VALUES(?,?,?,?,?,?,?,?)",
                        (f.client_order_id, f.token_id, f.side, f.price, f.size, f.fee, f.liquidity, f.ts))
        for cb in self.fill_listeners:
            try:
                cb(f)
            except Exception:  # noqa: BLE001
                log.exception("fill_listener_failed")
        return f

    def _walk(self, o: Order, book: OrderBook, max_size: float) -> list[tuple[float, float]]:
        """Return [(price, size)] the order can take right now, up to max_size."""
        takes, need = [], max_size
        if o.side == BUY:
            for price, avail in self._available(book, "asks"):
                if price > o.price + 1e-12 or need <= 1e-12:
                    break
                q = min(avail, need)
                if q > 0:
                    takes.append((price, q)); need -= q
        else:
            for price, avail in self._available(book, "bids"):
                if price < o.price - 1e-12 or need <= 1e-12:
                    break
                q = min(avail, need)
                if q > 0:
                    takes.append((price, q)); need -= q
        return takes

    def place_limit_order(self, intent: OrderIntent, book: Optional[OrderBook] = None,
                          market: Optional[MarketInfo] = None) -> Order:
        with self._lock:
            cid = intent.client_order_id()
            existing = self.get_order(cid)
            if existing is not None:      # idempotency: replayed intent after restart / duplicate tick
                log.info("duplicate_client_order_id", extra={"evt": "duplicate_order", "cid": cid})
                return existing
            fs = dict(market.fee_schedule or {}, _category=market.category) if market else None
            fe = market.fees_enabled if market else None
            o = Order(client_order_id=cid, token_id=intent.token_id, side=intent.side, price=intent.price,
                      size=intent.size, tif=intent.tif, strategy=intent.strategy, reason=intent.reason)

            def reject(why: str) -> Order:
                o.status, o.reason = "rejected", why
                self._save_order(o, fs, fe)
                log.warning("paper_order_rejected", extra={"evt": "order_rejected", "cid": cid, "why": why})
                return o

            if intent.side not in (BUY, SELL):
                return reject("bad side")
            if not (0 < intent.price < 1) or intent.size <= 0:
                return reject("bad price/size")
            if intent.side == SELL:
                held = self._position(intent.token_id).size - self.reserved_shares(intent.token_id)
                if intent.size > held + 1e-9:
                    return reject(f"insufficient shares: have {held:.4f}")
            else:
                worst = intent.price * intent.size * 1.03  # price + headroom for fee
                if worst > self.free_cash() + 1e-9:
                    return reject(f"insufficient free cash: {self.free_cash():.2f}")
            if book is None:
                book = self.get_book(intent.token_id)

            takes = self._walk(o, book, o.size)
            if o.tif == "FOK" and sum(q for _, q in takes) < o.size - 1e-9:
                return reject("FOK not fully fillable")
            for price, q in takes:
                self._consume(book, price, q)
                self._apply_fill(o, price, q, "taker", fs, fe)
            if o.remaining <= 1e-9:
                o.status = "filled"
            elif o.tif in ("FAK", "FOK"):
                o.status = "cancelled" if o.filled == 0 else "partial_cancelled"
            else:
                o.status = "partial" if o.filled > 0 else "open"
            self._save_order(o, fs, fe)
            log.info("paper_order", extra={"evt": "order_placed", "cid": cid, "side": o.side, "px": o.price,
                                           "size": round(o.size, 4), "filled": round(o.filled, 4),
                                           "avg_px": round(o.avg_fill_price, 4), "fee": round(o.fees_paid, 4),
                                           "status": o.status, "strategy": o.strategy, "token": o.token_id[:12]})
            return o

    def on_book(self, book: OrderBook, market: Optional[MarketInfo] = None) -> list[Fill]:
        """Match resting orders for this token against a fresh book (trade-through model)."""
        out = []
        with self._lock:
            rows = self.db.execute("SELECT * FROM orders WHERE token_id=? AND status IN ('open','partial')",
                                   (book.token_id,)).fetchall()
            for r in rows:
                o = self._row_to_order(r)
                fs = json.loads(r[14]) if r[14] else (market.fee_schedule if market else None)
                fe = None if r[15] is None else bool(r[15])
                takes = self._walk(o, book, o.remaining)
                qty = sum(q for _, q in takes)
                if qty <= 1e-9:
                    continue
                if o.side == SELL:
                    qty = min(qty, self._position(o.token_id).size)
                    if qty <= 1e-9:
                        continue
                for price, q in takes:
                    self._consume(book, price, q)
                out.append(self._apply_fill(o, o.price, qty, "maker", fs, fe))   # filled at OUR price
                o.status = "filled" if o.remaining <= 1e-9 else "partial"
                self._save_order(o)
                log.info("paper_resting_fill", extra={"evt": "resting_fill", "cid": o.client_order_id,
                                                      "px": o.price, "qty": round(qty, 4), "status": o.status})
        return out

    def cancel(self, client_order_id: str) -> bool:
        with self._lock:
            o = self.get_order(client_order_id)
            if o is None or o.status not in ("open", "partial"):
                return False
            o.status = "cancelled"
            self._save_order(o)
            return True

    def cancel_all(self) -> int:
        with self._lock:
            n = 0
            for o in self.open_orders():
                n += self.cancel(o.client_order_id)
            return n

    def settle(self, token_id: str, payout_per_share: float) -> float:
        """Resolve a position at 1.0 (won) or 0.0 (lost). Returns cash credited."""
        with self._lock:
            pos = self._position(token_id)
            if pos.size <= 0:
                return 0.0
            for o in self.open_orders():
                if o.token_id == token_id:
                    self.cancel(o.client_order_id)
            credit = pos.size * payout_per_share
            pos.realized_pnl += (payout_per_share - pos.avg_cost) * pos.size
            pos.size = 0.0
            self._set_acct("cash", self.balance() + credit)
            self._save_position(pos)
            log.info("paper_settled", extra={"evt": "settled", "token": token_id[:12], "payout": payout_per_share,
                                             "credit": round(credit, 4)})
            return credit

    def close(self) -> None:
        with self._lock:
            try:
                self.db.close()
            except Exception:  # noqa: BLE001
                pass


# =============================================================================== LIVE
SECRET_ENV_VARS = ("POLY_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE",
                   "TELEGRAM_BOT_TOKEN")


def assert_live_allowed(confirm_live: bool) -> None:
    problems = []
    if os.environ.get("TRADING_MODE", "").strip().lower() != "live":
        problems.append("env TRADING_MODE != 'live'")
    if os.environ.get("I_UNDERSTAND_REAL_MONEY", "").strip() != "yes":
        problems.append("env I_UNDERSTAND_REAL_MONEY != 'yes'")
    if not confirm_live:
        problems.append("config mode != 'live' (confirm_live=False)")
    if problems:
        raise LiveTradingRefused("LIVE TRADING REFUSED: " + "; ".join(problems))


class PolymarketLiveBroker(Broker):
    """Thin, defensive wrapper around the official `py-clob-client` (tested against 0.34.x).

    * signature_type: 0 = EOA (key holds funds), 1 = POLY_PROXY (email/Magic account; funder = proxy
      address shown on polymarket.com), 2 = POLY_GNOSIS_SAFE (browser-wallet account; funder = safe).
    * Orders are EIP-712 signed locally by py-clob-client (key never leaves the process); L2 HMAC
      headers authenticate the REST calls.
    * A local SQLite journal (state/live_journal.db) records every order BEFORE it is posted
      (status 'submitting'). On restart, 'submitting' rows are NOT re-sent; they are flagged for
      reconciliation against the exchange's open orders/trades. That makes restarts idempotent even
      though the CLOB has no client-order-id field.
    """

    mode = "live"
    HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137

    def __init__(self, journal_path: str | Path, confirm_live: bool = False, data: Optional[PublicClient] = None):
        assert_live_allowed(confirm_live)
        try:
            from py_clob_client.client import ClobClient           # noqa: F401
            from py_clob_client.clob_types import ApiCreds
        except ImportError as e:  # pragma: no cover
            raise LiveTradingRefused("py-clob-client not installed") from e
        key = os.environ.get("POLY_PRIVATE_KEY", "")
        funder = os.environ.get("POLY_FUNDER_ADDRESS", "")
        sig = os.environ.get("POLY_SIGNATURE_TYPE", "")
        if not key or not funder or sig not in ("0", "1", "2"):
            raise LiveTradingRefused("missing POLY_PRIVATE_KEY / POLY_FUNDER_ADDRESS / POLY_SIGNATURE_TYPE(0|1|2)")
        from py_clob_client.client import ClobClient
        self._client = ClobClient(self.HOST, chain_id=self.CHAIN_ID, key=key, signature_type=int(sig), funder=funder)
        del key
        ak, sec, pp = (os.environ.get("POLY_API_KEY"), os.environ.get("POLY_API_SECRET"),
                       os.environ.get("POLY_API_PASSPHRASE"))
        if ak and sec and pp:
            creds = ApiCreds(api_key=ak, api_secret=sec, api_passphrase=pp)
        else:
            # L1-signed request; derives the existing key for this wallet or creates one. Never logged.
            creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)
        self.funder = funder
        self.data = data or PublicClient()
        self.journal = sqlite3.connect(str(journal_path), check_same_thread=False, isolation_level=None)
        self.journal.executescript("""CREATE TABLE IF NOT EXISTS orders (client_order_id TEXT PRIMARY KEY,
            exchange_order_id TEXT, token_id TEXT, side TEXT, price REAL, size REAL, tif TEXT, strategy TEXT,
            status TEXT, response TEXT, created_ts REAL, updated_ts REAL)""")
        self._lock = threading.RLock()
        stuck = self.journal.execute("SELECT COUNT(*) FROM orders WHERE status='submitting'").fetchone()[0]
        if stuck:
            log.error("live_journal_has_unconfirmed_orders", extra={"evt": "reconcile_required", "n": stuck})
        log.warning("LIVE BROKER INITIALISED - REAL MONEY", extra={"evt": "live_broker_ready",
                                                                  "funder_suffix": funder[-4:], "sig_type": sig})

    def __repr__(self) -> str:  # never leak secrets
        return f"PolymarketLiveBroker(funder=...{self.funder[-4:]})"

    def get_book(self, token_id: str) -> OrderBook:
        return self.data.get_book(token_id)

    def place_limit_order(self, intent: OrderIntent, book: Optional[OrderBook] = None,
                          market: Optional[MarketInfo] = None) -> Order:
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        cid = intent.client_order_id()
        with self._lock:
            row = self.journal.execute("SELECT exchange_order_id,status FROM orders WHERE client_order_id=?",
                                       (cid,)).fetchone()
            if row is not None:
                log.info("duplicate_client_order_id", extra={"evt": "duplicate_order", "cid": cid, "status": row[1]})
                return Order(cid, intent.token_id, intent.side, intent.price, intent.size, status=row[1],
                             exchange_order_id=row[0] or "")
            now = time.time()
            self.journal.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (cid, None, intent.token_id, intent.side, intent.price, intent.size, intent.tif,
                                  intent.strategy, "submitting", None, now, now))
        opts = PartialCreateOrderOptions(tick_size=str(book.tick_size) if book else None,
                                         neg_risk=(market.neg_risk if market else None))
        ot = {"GTC": OrderType.GTC, "FAK": OrderType.FAK, "FOK": OrderType.FOK}.get(intent.tif, OrderType.GTC)
        status, oid, resp_txt = "rejected", "", ""
        try:
            signed = self._client.create_order(OrderArgs(token_id=intent.token_id, price=intent.price,
                                                         size=intent.size, side=intent.side), opts)
            resp = self._client.post_order(signed, ot)
            resp_txt = json.dumps(resp)[:2000]
            if isinstance(resp, dict) and resp.get("success"):
                oid = str(resp.get("orderID", ""))
                status = {"matched": "filled", "live": "open", "delayed": "open"}.get(str(resp.get("status")), "open")
            else:
                status = "rejected"
        except Exception as e:  # noqa: BLE001
            # unknown outcome: keep 'submitting' -> reconciliation decides; do NOT retry blindly
            status, resp_txt = "submitting", f"exception: {type(e).__name__}: {str(e)[:300]}"
            log.error("live_post_failed", extra={"evt": "live_post_failed", "cid": cid, "err": resp_txt})
        with self._lock:
            self.journal.execute("UPDATE orders SET exchange_order_id=?, status=?, response=?, updated_ts=? "
                                 "WHERE client_order_id=?", (oid, status, resp_txt, time.time(), cid))
        log.info("live_order", extra={"evt": "order_placed", "cid": cid, "oid": oid, "status": status,
                                      "side": intent.side, "px": intent.price, "size": intent.size})
        return Order(cid, intent.token_id, intent.side, intent.price, intent.size, status=status,
                     tif=intent.tif, strategy=intent.strategy, exchange_order_id=oid)

    def cancel(self, client_order_id: str) -> bool:
        row = self.journal.execute("SELECT exchange_order_id FROM orders WHERE client_order_id=?",
                                   (client_order_id,)).fetchone()
        if not row or not row[0]:
            return False
        resp = self._client.cancel(row[0])
        ok = bool(resp) and row[0] in (resp.get("canceled") or [])
        if ok:
            self.journal.execute("UPDATE orders SET status='cancelled', updated_ts=? WHERE client_order_id=?",
                                 (time.time(), client_order_id))
        return ok

    def cancel_all(self) -> int:
        resp = self._client.cancel_all() or {}
        n = len(resp.get("canceled") or [])
        self.journal.execute("UPDATE orders SET status='cancelled', updated_ts=? WHERE status IN ('open','partial')",
                             (time.time(),))
        log.warning("live_cancel_all", extra={"evt": "cancel_all", "n": n})
        return n

    def open_orders(self) -> list[Order]:
        out = []
        for o in self._client.get_orders() or []:
            out.append(Order(client_order_id="", token_id=str(o.get("asset_id")), side=str(o.get("side")),
                             price=float(o.get("price", 0)), size=float(o.get("original_size", 0)),
                             filled=float(o.get("size_matched", 0)), status="open",
                             exchange_order_id=str(o.get("id"))))
        return out

    def positions(self) -> dict[str, Position]:
        """Positions of the funder address from the public Data API (v2)."""
        raw = self.data._request("GET", f"{DATA_HOST}/v2/positions",
                                 params={"user": self.funder, "limit": 500, "sizeThreshold": 0})
        rows = raw.get("data", raw) if isinstance(raw, dict) else raw
        out = {}
        for p in rows or []:
            tid = str(p.get("asset") or p.get("asset_id") or p.get("token_id") or "")
            size = float(p.get("size") or 0)
            if tid and size > 0:
                out[tid] = Position(tid, size, float(p.get("avgPrice") or p.get("avg_price") or 0),
                                    float(p.get("realizedPnl") or 0))
        return out

    def balance(self) -> float:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        r = self._client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return float(r.get("balance", 0)) / 1e6     # USDC has 6 decimals

    def unconfirmed_orders(self) -> list[str]:
        return [r[0] for r in self.journal.execute("SELECT client_order_id FROM orders WHERE status='submitting'")]

    def close(self) -> None:
        try:
            self.journal.close()
        except Exception:  # noqa: BLE001
            pass
