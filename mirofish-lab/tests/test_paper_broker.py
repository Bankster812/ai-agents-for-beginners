"""PaperBroker tests against recorded / synthetic order books (no network unless marked)."""
import json
import os
from pathlib import Path

import pytest

from src.execution.broker import LiveTradingRefused, PaperBroker, PolymarketLiveBroker, assert_live_allowed
from src.execution.fees import fee_usd
from src.execution.models import BUY, SELL, Level, MarketInfo, OrderBook, OrderIntent

# A raw CLOB /book payload in the exchange's own ordering (bids ascending, asks descending:
# best level LAST) - recorded shape from clob.polymarket.com/book on 2026-11-24.
RAW_BOOK = {
    "market": "0xabc", "asset_id": "TOK", "timestamp": "1795528436448", "hash": "x",
    "bids": [{"price": "0.45", "size": "500"}, {"price": "0.47", "size": "200"}, {"price": "0.48", "size": "100"}],
    "asks": [{"price": "0.55", "size": "1000"}, {"price": "0.52", "size": "100"}, {"price": "0.50", "size": "50"}],
    "min_order_size": "5", "tick_size": "0.01", "neg_risk": False, "last_trade_price": "0.49",
}
SCHED = {"rate": 0.05, "exponent": 1, "takerOnly": True}
MKT = MarketInfo("0xabc", "Q?", "q", ["TOK", "TOK_NO"], ["Yes", "No"], liquidity_usd=1e5, fee_schedule=SCHED,
                 fees_enabled=True, category="sports_fees_v3")


@pytest.fixture
def pb(tmp_path):
    b = PaperBroker(tmp_path / "paper.db", starting_cash=1000.0)
    yield b
    b.close()


def ob(ts=1.0):
    b = OrderBook.from_clob(RAW_BOOK)
    b.ts = ts
    return b


def test_book_normalisation():
    b = ob()
    assert b.best_bid == 0.48 and b.best_ask == 0.50
    assert abs(b.mid - 0.49) < 1e-12 and abs(b.spread - 0.02) < 1e-12
    assert [l.price for l in b.asks] == [0.50, 0.52, 0.55]


def test_fee_formula_matches_cost_model():
    # fee = shares * rate * p * (1-p): 100 shares @0.5, rate 0.05 -> 1.25 USDC
    assert abs(fee_usd(0.5, 100, "taker", SCHED, True) - 1.25) < 1e-9
    assert fee_usd(0.5, 100, "maker", SCHED, True) == 0.0
    assert fee_usd(0.5, 100, "taker", SCHED, False) == 0.0
    from src import cost_model
    assert abs(fee_usd(0.3, 50, "taker", None, True, category="politics") -
               cost_model.fee(0.3, shares=50, category="politics")) < 1e-4


def test_walk_the_book_partial_fill_and_rest(pb):
    # BUY 200 @ limit 0.52: takes 50@0.50 + 100@0.52 = 150, rests 50 @0.52 (GTC)
    o = pb.place_limit_order(OrderIntent("TOK", BUY, 0.52, 200, "s", "k1"), ob(), MKT)
    assert o.status == "partial" and abs(o.filled - 150) < 1e-9
    assert abs(o.avg_fill_price - (50 * 0.50 + 100 * 0.52) / 150) < 1e-12
    exp_fee = 50 * 0.05 * 0.5 * 0.5 + 100 * 0.05 * 0.52 * 0.48
    assert abs(o.fees_paid - exp_fee) < 1e-9
    assert abs(pb.balance() - (1000 - 25 - 52 - exp_fee)) < 1e-9
    pos = pb.positions()["TOK"]
    assert abs(pos.size - 150) < 1e-9
    assert abs(pos.avg_cost - (77 + exp_fee) / 150) < 1e-12                # fee capitalised
    assert len(pb.open_orders()) == 1 and abs(pb.reserved_cash() - 50 * 0.52) < 1e-9


def test_same_snapshot_liquidity_not_double_counted(pb):
    b = ob(ts=42.0)
    o1 = pb.place_limit_order(OrderIntent("TOK", BUY, 0.50, 40, "s", "a", tif="FAK"), b, MKT)
    o2 = pb.place_limit_order(OrderIntent("TOK", BUY, 0.50, 40, "s", "b", tif="FAK"), b, MKT)
    assert o1.filled == 40 and abs(o2.filled - 10) < 1e-9                 # only 50 at 0.50 existed
    assert o2.status == "partial_cancelled"
    o3 = pb.place_limit_order(OrderIntent("TOK", BUY, 0.50, 40, "s", "c", tif="FAK"), ob(ts=43.0), MKT)
    assert o3.filled == 40                                                 # fresh snapshot -> fresh liquidity


def test_fok_all_or_nothing(pb):
    o = pb.place_limit_order(OrderIntent("TOK", BUY, 0.52, 1000, "s", "f", tif="FOK"), ob(), MKT)
    assert o.status == "rejected" and o.filled == 0 and pb.balance() == 1000


def test_passive_order_rests_then_fills_on_trade_through(pb):
    o = pb.place_limit_order(OrderIntent("TOK", BUY, 0.46, 100, "s", "p"), ob(), MKT)
    assert o.status == "open" and o.filled == 0
    # book unchanged -> no fill (we never assume queue fills without a cross)
    assert pb.on_book(ob(ts=2.0), MKT) == []
    # market falls: asks now at 0.45 -> our 0.46 bid is crossed -> maker fill at OUR price, no fee
    moved = OrderBook("TOK", bids=[Level(0.44, 100)], asks=[Level(0.45, 60), Level(0.46, 500)], ts=3.0)
    fills = pb.on_book(moved, MKT)
    assert len(fills) == 1 and fills[0].price == 0.46 and fills[0].liquidity == "maker" and fills[0].fee == 0
    assert abs(fills[0].size - 100) < 1e-9
    assert pb.get_order(o.client_order_id).status == "filled"


def test_sell_requires_shares_and_realises_pnl(pb):
    r = pb.place_limit_order(OrderIntent("TOK", SELL, 0.48, 10, "s", "nos"), ob(), MKT)
    assert r.status == "rejected" and "insufficient shares" in r.reason
    pb.place_limit_order(OrderIntent("TOK", BUY, 0.50, 50, "s", "buy"), ob(), MKT)
    cost = pb.positions()["TOK"].avg_cost
    s = pb.place_limit_order(OrderIntent("TOK", SELL, 0.47, 50, "s", "sell", tif="FAK"), ob(ts=9.0), MKT)
    # bids: 100@0.48 -> all 50 at 0.48
    assert s.status == "filled" and abs(s.avg_fill_price - 0.48) < 1e-12
    sell_fee = 50 * 0.05 * 0.48 * 0.52
    assert abs(pb.realized_pnl_total() - ((0.48 - cost) * 50 - sell_fee)) < 1e-9
    assert "TOK" not in pb.positions()


def test_insufficient_cash_rejected(tmp_path):
    b = PaperBroker(tmp_path / "p.db", starting_cash=10.0)
    o = b.place_limit_order(OrderIntent("TOK", BUY, 0.50, 100, "s", "x"), ob(), MKT)
    assert o.status == "rejected" and b.balance() == 10.0
    b.close()


def test_idempotent_client_order_id_survives_restart(tmp_path):
    db = tmp_path / "paper.db"
    it = OrderIntent("TOK", BUY, 0.50, 20, "strat", intent_key="market1:entry", tif="FAK")
    b1 = PaperBroker(db, 1000.0)
    o1 = b1.place_limit_order(it, ob(), MKT)
    cash_after = b1.balance()
    b1.close()
    b2 = PaperBroker(db, 999_999.0)               # starting_cash ignored for an existing account
    assert b2.balance() == cash_after
    o2 = b2.place_limit_order(OrderIntent("TOK", BUY, 0.50, 20, "strat", intent_key="market1:entry", tif="FAK"),
                              ob(ts=5.0), MKT)
    assert o2.client_order_id == o1.client_order_id and b2.balance() == cash_after   # no double fill
    assert len(b2.fills()) == 1
    b2.close()


def test_settlement(pb):
    pb.place_limit_order(OrderIntent("TOK", BUY, 0.50, 40, "s", "e", tif="FAK"), ob(), MKT)
    cash = pb.balance()
    credit = pb.settle("TOK", 1.0)
    assert credit == 40 and abs(pb.balance() - (cash + 40)) < 1e-9 and pb.positions() == {}


def test_cancel_and_cancel_all(pb):
    a = pb.place_limit_order(OrderIntent("TOK", BUY, 0.40, 10, "s", "c1"), ob(), MKT)
    pb.place_limit_order(OrderIntent("TOK", BUY, 0.41, 10, "s", "c2"), ob(), MKT)
    assert pb.cancel(a.client_order_id) and not pb.cancel(a.client_order_id)
    assert pb.cancel_all() == 1 and pb.open_orders() == []


# ------------------------------------------------------------------------- live-mode guard
def test_live_broker_refuses_without_both_env_flags(tmp_path, monkeypatch):
    for k in ("TRADING_MODE", "I_UNDERSTAND_REAL_MONEY", "POLY_PRIVATE_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(LiveTradingRefused):
        PolymarketLiveBroker(tmp_path / "j.db", confirm_live=True)
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(LiveTradingRefused):
        PolymarketLiveBroker(tmp_path / "j.db", confirm_live=True)
    monkeypatch.setenv("I_UNDERSTAND_REAL_MONEY", "YES")            # must be exactly "yes"
    with pytest.raises(LiveTradingRefused):
        PolymarketLiveBroker(tmp_path / "j.db", confirm_live=True)
    monkeypatch.setenv("I_UNDERSTAND_REAL_MONEY", "yes")
    with pytest.raises(LiveTradingRefused, match="config mode"):
        assert_live_allowed(confirm_live=False)
    # both flags + confirm, but no credentials -> still refused, and nothing touches the network
    with pytest.raises(LiveTradingRefused, match="missing POLY_PRIVATE_KEY"):
        PolymarketLiveBroker(tmp_path / "j.db", confirm_live=True)


def test_runner_refuses_non_live_strategy_in_live_mode(tmp_path, monkeypatch):
    from src.execution.runner import DEFAULTS, Runner, _merge

    class FakeLive:
        mode = "live"
        def positions(self): return {}
        def open_orders(self): return []
        def close(self): pass
    cfg = _merge(DEFAULTS, {"mode": "live", "state_dir": str(tmp_path)})
    with pytest.raises(LiveTradingRefused, match="live_allowed=False"):
        Runner(cfg, root=tmp_path, broker=FakeLive())


def test_example_strategy_never_trades_live():
    from src.execution.models import MarketSnapshot
    from src.execution.strategy_api import ExampleWiringStrategy
    s = ExampleWiringStrategy()
    snap = MarketSnapshot(MKT, {"TOK": ob(), "TOK_NO": ob()}, 0, mode="live")
    assert s.on_tick(snap) == [] and s.live_allowed is False
    assert len(s.on_tick(MarketSnapshot(MKT, {"TOK": ob(), "TOK_NO": ob()}, 0, mode="paper"))) == 2


def test_secrets_are_redacted_in_logs(monkeypatch, capsys):
    import logging
    from src.execution.logutil import setup_logging
    fake = "0x" + "ab" * 32
    monkeypatch.setenv("POLY_PRIVATE_KEY", fake)
    monkeypatch.setenv("POLY_API_SECRET", "supersecretvalue123")
    setup_logging(None)
    logging.getLogger("t").error("oops key=%s secret=%s", fake, "supersecretvalue123")
    out = capsys.readouterr().out
    assert fake not in out and "supersecretvalue123" not in out and "REDACTED" in out


# ------------------------------------------------------------------------- LIVE DATA smoke (read-only)
@pytest.mark.network
def test_live_public_book_smoke(tmp_path):
    """Fetch a real active market's public book and simulate a $5 taker buy + resting bid. Read-only."""
    from src.execution.marketdata import PublicClient
    data = PublicClient(rate=2, burst=2)
    mkts = data.discover_markets(min_volume_24h=50_000, min_liquidity=20_000, max_markets=10, pages=1)
    assert mkts, "no active markets discovered"
    books = data.get_books([t for m in mkts for t in m.token_ids])
    chosen = None
    for m in mkts:
        for t in m.token_ids:
            b = books.get(t)
            if b and b.best_bid and b.best_ask and b.spread <= 0.05 and 0.05 < b.best_ask < 0.95:
                chosen = (m, b)
                break
        if chosen:
            break
    assert chosen, "no two-sided book with spread <= 5c found"
    m, b = chosen
    pb = PaperBroker(tmp_path / "smoke.db", 100.0, data=data)
    size = max(b.min_order_size, round(5.0 / b.best_ask, 2))
    o = pb.place_limit_order(OrderIntent(b.token_id, BUY, b.best_ask, size, "smoke", "smoke", tif="FAK"), b, m)
    assert o.status in ("filled", "partial_cancelled") and o.filled > 0
    assert o.avg_fill_price <= b.best_ask + 1e-12
    assert pb.balance() < 100.0
    passive_px = round(round((b.best_bid - b.tick_size) / b.tick_size) * b.tick_size, 6)
    if passive_px > 0:
        r = pb.place_limit_order(OrderIntent(b.token_id, BUY, passive_px, size, "smoke", "rest"), b, m)
        assert r.status == "open"
    print(f"\nSMOKE {m.slug[:60]} token {b.token_id[:10]}.. bid {b.best_bid} ask {b.best_ask} "
          f"filled {o.filled}@{o.avg_fill_price:.4f} fee {o.fees_paid:.4f}")
    pb.close()
