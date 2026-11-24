"""24/7 async trading loop (strategy-agnostic, paper by default).

    python -m src.execution.runner --config state/config.yaml            # run forever
    python -m src.execution.runner --config ... --duration 240           # run 4 minutes then stop
    python -m src.execution.runner --config ... --status                 # print heartbeat + account
    python -m src.execution.runner --config ... --reset-drawdown         # operator: clear drawdown halt

Loop per tick (poll_interval_s):
  kill-switch check -> fetch books (batched, rate-limited, backoff) -> paper: match resting orders
  -> mark-to-market equity -> risk.update_equity (daily-loss / drawdown halts) -> strategies.on_tick
  -> every intent through RiskManager.check -> broker.place_limit_order (idempotent client ids)
Periodic: market discovery (Gamma), reconciliation, settlement of resolved markets (paper),
heartbeat file (state/heartbeat.json) for the Docker healthcheck.
SIGINT/SIGTERM -> finish the tick, cancel resting orders (configurable), write final heartbeat, exit 0.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import yaml

from .broker import Broker, LiveTradingRefused, PaperBroker, PolymarketLiveBroker
from .fees import fee_source
from .logutil import setup_logging
from .marketdata import PublicClient
from .models import BUY, SELL, MarketInfo, MarketSnapshot, OrderBook, OrderIntent
from .notify import Notifier
from .risk import AccountView, RiskLimits, RiskManager
from .strategy_api import load_strategy

log = logging.getLogger("execution.runner")
LAB_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS = {
    "mode": "paper", "state_dir": "state", "log_level": "INFO",
    "paper": {"starting_cash_usd": 10_800, "db_path": "state/paper.db"},
    "live": {"journal_path": "state/live_journal.db"},
    "loop": {"poll_interval_s": 10, "discovery_interval_s": 900, "reconcile_interval_s": 120,
             "heartbeat_interval_s": 15, "settlement_check_interval_s": 1800,
             "cancel_open_orders_on_shutdown": True, "max_consecutive_errors": 20, "backoff_on_errors_s": 120},
    "http": {"rate_per_s": 5, "burst": 10},
    "discovery": {"min_volume_24h_usd": 20_000, "min_liquidity_usd": 10_000, "max_markets": 20,
                  "include_slugs": [], "exclude_slugs": []},
    "risk": {},
    "strategies": [{"path": "src.execution.strategy_api:ExampleWiringStrategy", "params": {}}],
}


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Optional[str]) -> dict:
    cfg = DEFAULTS
    if path:
        with open(path) as f:
            cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    if cfg["mode"] not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")
    return cfg


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Does not override already-set env vars. Never logs values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


class Runner:
    def __init__(self, cfg: dict, root: Path = LAB_ROOT, broker: Optional[Broker] = None,
                 data: Optional[PublicClient] = None):
        self.cfg, self.root = cfg, root
        self.state_dir = self._p(cfg["state_dir"])
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.mode = cfg["mode"]
        self.notify = Notifier(prefix=f"[mirofish-exec/{self.mode}]")
        self.data = data or PublicClient(rate=cfg["http"]["rate_per_s"], burst=cfg["http"]["burst"])
        risk_cfg = dict(cfg["risk"])
        for k in ("kill_switch_file", "state_file"):
            if k in risk_cfg:
                risk_cfg[k] = str(self._p(risk_cfg[k]))
        risk_cfg.setdefault("kill_switch_file", str(self.state_dir / "KILL"))
        risk_cfg.setdefault("state_file", str(self.state_dir / "risk_state.json"))
        self.risk = RiskManager(RiskLimits.from_dict(risk_cfg), root=root, notifier=self.notify)

        if broker is not None:
            self.broker = broker
        elif self.mode == "live":
            if os.environ.get("TRADING_MODE") == "live" and os.environ.get("I_UNDERSTAND_REAL_MONEY") == "yes":
                log.warning("LIVE MODE REQUESTED", extra={"evt": "live_requested"})
            self.broker = PolymarketLiveBroker(self._p(cfg["live"]["journal_path"]), confirm_live=True, data=self.data)
        else:
            if os.environ.get("TRADING_MODE", "").lower() == "live":
                log.warning("env TRADING_MODE=live but config mode=paper -> staying in PAPER",
                            extra={"evt": "mode_mismatch"})
            self.broker = PaperBroker(self._p(cfg["paper"]["db_path"]), cfg["paper"]["starting_cash_usd"], data=self.data)

        self.strategies = []
        for s in cfg["strategies"]:
            st = load_strategy(s["path"], s.get("params"))
            if self.broker.mode == "live" and not getattr(st, "live_allowed", False):
                raise LiveTradingRefused(f"strategy {st.name} has live_allowed=False; refusing to run it live")
            self.strategies.append(st)

        self.markets: dict[str, MarketInfo] = {}            # condition_id -> info (discovered)
        self.token_market: dict[str, MarketInfo] = {}       # token -> info (discovered + cache)
        self._load_market_cache()
        self.books: dict[str, OrderBook] = {}
        self.marks: dict[str, float] = {}
        if isinstance(self.broker, PaperBroker):
            self.marks.update(self.broker.last_marks())
            self.broker.fill_listeners.append(self._on_fill)
        self.stop_event = asyncio.Event()
        self.status = "starting"
        self.ticks = 0
        self.consecutive_errors = 0
        self.last_error = ""
        self.counters: Counter = Counter()
        self.reject_reasons: Counter = Counter()
        self.trading_paused_reason = ""
        self.t_start = time.time()
        self.equity_start: Optional[float] = None
        self.equity_last: Optional[float] = None
        self._t = {"discovery": 0.0, "reconcile": 0.0, "heartbeat": 0.0, "settle": 0.0}

    def _p(self, s) -> Path:
        p = Path(s)
        return p if p.is_absolute() else self.root / p

    # ------------------------------------------------------------------ market cache (restart-safe)
    def _cache_path(self) -> Path:
        return self.state_dir / "markets_cache.json"

    def _load_market_cache(self) -> None:
        try:
            for d in json.loads(self._cache_path().read_text()):
                mi = MarketInfo(**d)
                for t in mi.token_ids:
                    self.token_market[t] = mi
        except (OSError, ValueError, TypeError):
            pass

    def _save_market_cache(self) -> None:
        uniq = {m.condition_id: asdict(m) for m in self.token_market.values()}
        tmp = self._cache_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(list(uniq.values())))
        tmp.replace(self._cache_path())

    # ------------------------------------------------------------------ periodic jobs
    async def discover(self) -> None:
        d = self.cfg["discovery"]
        found = await asyncio.to_thread(self.data.discover_markets, d["min_volume_24h_usd"], d["min_liquidity_usd"],
                                        max(d["max_markets"] * 3, 30))
        inc, exc = set(d.get("include_slugs") or []), set(d.get("exclude_slugs") or [])
        sel = []
        for m in found:
            if inc and m.slug not in inc:
                continue
            if m.slug in exc or self.risk._blacklisted(m):
                continue
            sel.append(m)
            if len(sel) >= d["max_markets"]:
                break
        self.markets = {m.condition_id: m for m in sel}
        for m in sel:
            for t in m.token_ids:
                self.token_market[t] = m
        self._save_market_cache()
        log.info("discovery", extra={"evt": "discovery", "candidates": len(found), "selected": len(sel),
                                     "markets": [m.slug[:50] for m in sel[:10]]})

    def _tracked_tokens(self) -> list[str]:
        toks = [t for m in self.markets.values() for t in m.token_ids]
        toks += [t for t in self.broker.positions() if t not in toks]
        toks += [o.token_id for o in self.broker.open_orders() if o.token_id not in toks]
        return toks

    def account_view(self) -> AccountView:
        pos = self.broker.positions()
        oo = self.broker.open_orders()
        ob: dict[str, float] = {}
        for o in oo:
            if o.side == BUY:
                ob[o.token_id] = ob.get(o.token_id, 0.0) + o.remaining * o.price
        cash = self.broker.balance()
        free = self.broker.free_cash() if isinstance(self.broker, PaperBroker) else cash - sum(ob.values())
        eq = cash + sum(p.size * self.marks.get(t, p.avg_cost) for t, p in pos.items())
        return AccountView(equity=eq, free_cash=free, position_cost={t: p.cost_basis for t, p in pos.items()},
                           open_buy_notional=ob, held_shares={t: p.size for t, p in pos.items()})

    async def reconcile(self) -> None:
        """Paper: rebuild positions from the fill ledger and compare. Live: compare exchange vs journal."""
        if isinstance(self.broker, PaperBroker):
            b = self.broker
            rows = b.db.execute("SELECT token_id, SUM(CASE WHEN side='BUY' THEN size ELSE -size END) FROM fills "
                                "GROUP BY token_id").fetchall()
            ledger = {t: s for t, s in rows if abs(s) > 1e-9}
            settled = {r[0] for r in b.db.execute("SELECT token_id FROM positions WHERE size<=1e-9")}
            book = {t: p.size for t, p in b.positions().items()}
            bad = {t: (ledger.get(t, 0), book.get(t, 0)) for t in set(ledger) | set(book)
                   if t not in settled and abs(ledger.get(t, 0) - book.get(t, 0)) > 1e-6}
            if b.balance() < -1e-6:
                bad["cash"] = (b.balance(), 0)
        else:
            exch = await asyncio.to_thread(self.broker.positions)
            unconf = self.broker.unconfirmed_orders() if hasattr(self.broker, "unconfirmed_orders") else []
            bad = {"unconfirmed_orders": len(unconf)} if unconf else {}
            log.info("reconcile_live", extra={"evt": "reconcile", "exchange_positions": len(exch)})
        if bad:
            self.trading_paused_reason = f"reconciliation mismatch: {str(bad)[:300]}"
            log.error("reconcile_mismatch", extra={"evt": "reconcile_mismatch", "detail": str(bad)[:500]})
            self.notify(f"RECONCILE MISMATCH -> trading paused: {str(bad)[:300]}")
        else:
            log.info("reconcile_ok", extra={"evt": "reconcile_ok"})

    async def settle_resolved(self) -> None:
        if not isinstance(self.broker, PaperBroker):
            return   # live: the exchange/UMA settles; redemption is a manual step (see deploy/README.md)
        for t in list(self.broker.positions()):
            mi = self.token_market.get(t)
            if mi is None:
                continue
            try:
                m = await asyncio.to_thread(self.data.market_status, mi.condition_id)
            except Exception as e:  # noqa: BLE001
                log.warning("settle_check_failed", extra={"evt": "settle_check_failed", "err": str(e)[:200]})
                continue
            if not m or not m.get("closed"):
                continue
            try:
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
                toks = [str(x) for x in json.loads(m.get("clobTokenIds") or "[]")]
            except ValueError:
                continue
            if t in toks and prices and max(prices) >= 0.99:
                self.broker.settle(t, 1.0 if prices[toks.index(t)] >= 0.99 else 0.0)
                self.counters["settlements"] += 1

    def heartbeat(self, final: bool = False) -> None:
        hb = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mode": self.broker.mode,
              "status": "stopped" if final else self.status, "ticks": self.ticks, "equity": self.equity_last,
              "markets": len(self.markets), "open_orders": len(self.broker.open_orders()),
              "positions": len(self.broker.positions()), "halt": self.risk.halted(),
              "paused": self.trading_paused_reason, "last_error": self.last_error[:300],
              "counters": dict(self.counters),
              "http": vars(self.data.stats), "pid": os.getpid(), "fee_source": fee_source()}
        p = self.state_dir / "heartbeat.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(hb, indent=1, default=str))
        tmp.replace(p)

    def _on_fill(self, f) -> None:
        self.counters["fills"] += 1
        self.counters["fees_usd_x1e4"] += int(round(f.fee * 1e4))
        self.counters["traded_usd_x100"] += int(round(f.price * f.size * 100))

    # ------------------------------------------------------------------ one tick
    async def tick(self) -> None:
        self.ticks += 1
        if self.risk.kill_switch_active():
            if self.status != "killed":
                n = await asyncio.to_thread(self.broker.cancel_all)
                self.status = "killed"
                msg = f"KILL SWITCH: cancelled {n} orders; trading stopped until KILL removed AND process restarted"
                log.critical(msg, extra={"evt": "kill_switch"})
                self.notify(msg)
            return
        tokens = self._tracked_tokens()
        if not tokens:
            return
        self.books = await asyncio.to_thread(self.data.get_books, tokens)
        for t, b in self.books.items():
            if b.mid is not None:
                self.marks[t] = b.mid
            self.broker.on_book(b, self.token_market.get(t))
        if isinstance(self.broker, PaperBroker):
            self.broker.save_marks({t: self.marks[t] for t in self.broker.positions() if t in self.marks})

        acct = await asyncio.to_thread(self.account_view)
        self.equity_last = acct.equity
        if self.equity_start is None:
            self.equity_start = acct.equity
        self.risk.update_equity(acct.equity)
        halt = self.risk.halted()
        if halt:
            if self.status != "halted":
                n = await asyncio.to_thread(self.broker.cancel_all)
                log.error("trading halted", extra={"evt": "halted", "why": halt, "cancelled": n})
                self.notify(f"HALTED: {halt} (cancelled {n} resting orders)")
            self.status = "halted"
            return
        if self.trading_paused_reason:
            self.status = "paused"
            return
        self.status = "running"

        for m in list(self.markets.values()):
            books = {t: self.books[t] for t in m.token_ids if t in self.books}
            if len(books) < len(m.token_ids):
                continue
            snap = MarketSnapshot(market=m, books=books, ts=time.time(), mode=self.broker.mode,
                                  positions={t: acct.held_shares.get(t, 0.0) for t in m.token_ids},
                                  equity_usd=acct.equity, cash_usd=acct.free_cash)
            for st in self.strategies:
                try:
                    intents = st.on_tick(snap) or []
                except Exception as e:  # noqa: BLE001 - a buggy strategy must not kill the loop
                    self.counters["strategy_errors"] += 1
                    log.exception("strategy_error", extra={"evt": "strategy_error", "strategy": st.name})
                    continue
                for it in intents:
                    await self._submit(it, m)

    async def _submit(self, it: OrderIntent, m: MarketInfo) -> None:
        self.counters["intents"] += 1
        it.strategy = it.strategy or "unknown"
        book = self.books.get(it.token_id)
        acct = await asyncio.to_thread(self.account_view)
        d = self.risk.check(it, m, book, acct)
        if not d.ok:
            self.counters["risk_rejected"] += 1
            self.reject_reasons[d.reason.split(" ")[0] + " " + " ".join(d.reason.split(" ")[1:3])] += 1
            log.info("risk_reject", extra={"evt": "risk_reject", "strategy": it.strategy, "why": d.reason,
                                           "side": it.side, "px": it.price, "size": it.size})
            return
        self.risk.record_order()
        o = await asyncio.to_thread(self.broker.place_limit_order, it, book, m)
        self.counters["orders_" + o.status] += 1

    # ------------------------------------------------------------------ main loop
    async def run(self, duration_s: Optional[float] = None) -> dict:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass
        L = self.cfg["loop"]
        log.info("runner_start", extra={"evt": "runner_start", "mode": self.broker.mode, "pid": os.getpid(),
                                        "strategies": [s.name for s in self.strategies], "fee_source": fee_source(),
                                        "limits": asdict(self.risk.l)})
        self.notify(f"runner started (mode={self.broker.mode})")
        deadline = time.time() + duration_s if duration_s else None
        while not self.stop_event.is_set():
            t0 = time.time()
            try:
                if t0 - self._t["discovery"] >= L["discovery_interval_s"] or not self.markets:
                    await self.discover()
                    self._t["discovery"] = t0
                await self.tick()
                if t0 - self._t["settle"] >= L["settlement_check_interval_s"]:
                    await self.settle_resolved()
                    self._t["settle"] = t0
                if t0 - self._t["reconcile"] >= L["reconcile_interval_s"]:
                    await self.reconcile()
                    self._t["reconcile"] = t0
                self.consecutive_errors = 0
            except Exception as e:  # noqa: BLE001
                self.consecutive_errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                self.counters["loop_errors"] += 1
                log.exception("loop_error", extra={"evt": "loop_error", "n": self.consecutive_errors})
                if self.consecutive_errors >= L["max_consecutive_errors"]:
                    self.notify(f"{self.consecutive_errors} consecutive loop errors; backing off. last: {self.last_error[:200]}")
                    await self._sleep(L["backoff_on_errors_s"])
                    self.consecutive_errors = 0
            if time.time() - self._t["heartbeat"] >= L["heartbeat_interval_s"]:
                self.heartbeat()
                self._t["heartbeat"] = time.time()
            if deadline and time.time() >= deadline:
                break
            await self._sleep(max(0.0, L["poll_interval_s"] - (time.time() - t0)))
        return await self.shutdown()

    async def _sleep(self, s: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=s)
        except asyncio.TimeoutError:
            pass

    async def shutdown(self) -> dict:
        self.status = "stopping"
        n = 0
        if self.cfg["loop"]["cancel_open_orders_on_shutdown"]:
            try:
                n = await asyncio.to_thread(self.broker.cancel_all)
            except Exception:  # noqa: BLE001
                log.exception("cancel_all_on_shutdown_failed")
        summary = self.summary()
        summary["cancelled_on_shutdown"] = n
        log.info("runner_stop", extra={"evt": "runner_stop", **summary})
        self.heartbeat(final=True)
        self.notify(f"runner stopped: equity {summary.get('equity_end')} ticks {self.ticks}")
        self.broker.close()
        return summary

    def summary(self) -> dict:
        s = {"mode": self.broker.mode, "runtime_s": round(time.time() - self.t_start, 1), "ticks": self.ticks,
             "markets": len(self.markets), "equity_start": self.equity_start, "equity_end": self.equity_last,
             "counters": dict(self.counters), "risk_reject_reasons": dict(self.reject_reasons.most_common(8)),
             "http": vars(self.data.stats), "halt": self.risk.halted()}
        if isinstance(self.broker, PaperBroker):
            s.update(cash=round(self.broker.balance(), 4), realized_pnl=round(self.broker.realized_pnl_total(), 4),
                     open_positions={t[:12]: round(p.size, 4) for t, p in self.broker.positions().items()},
                     fills_total=len(self.broker.fills(100000)))
        return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--duration", type=float, default=None, help="seconds, then graceful stop")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset-drawdown", action="store_true")
    a = ap.parse_args(argv)
    load_dotenv(LAB_ROOT / ".env")
    cfg = load_config(a.config)
    setup_logging(LAB_ROOT / cfg["state_dir"] / "logs", cfg["log_level"])
    if a.status:
        hb = LAB_ROOT / cfg["state_dir"] / "heartbeat.json"
        print(hb.read_text() if hb.exists() else "no heartbeat yet")
        return 0
    try:
        r = Runner(cfg)
    except LiveTradingRefused as e:
        log.critical(str(e), extra={"evt": "live_refused"})
        print(str(e), file=sys.stderr)
        return 2
    if a.reset_drawdown:
        r.risk.reset_drawdown(r.account_view().equity)
        return 0
    summary = asyncio.run(r.run(a.duration))
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
