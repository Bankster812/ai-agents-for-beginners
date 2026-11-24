"""Unit tests for src/execution/risk.py (no network)."""
import json
import time

import pytest

from src.execution.models import BUY, SELL, Level, MarketInfo, OrderBook, OrderIntent
from src.execution.risk import AccountView, RiskLimits, RiskManager


def book(bid=0.49, ask=0.51, size=2000, tick=0.01, token="T1"):
    return OrderBook(token, bids=[Level(bid, size), Level(bid - 0.01, size)],
                     asks=[Level(ask, size), Level(ask + 0.01, size)], tick_size=tick, min_order_size=5)


def market(liq=50_000, tags=None, slug="some-market"):
    return MarketInfo("C1", "Q?", slug, ["T1", "T2"], ["Yes", "No"], liquidity_usd=liq, volume_24h_usd=1e5,
                      tags=tags or ["politics"])


def acct(equity=10_000, cash=10_000, pos=None, ob=None, held=None):
    return AccountView(equity=equity, free_cash=cash, position_cost=pos or {}, open_buy_notional=ob or {},
                       held_shares=held or {})


@pytest.fixture
def rm(tmp_path):
    return RiskManager(RiskLimits(kill_switch_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "risk.json")),
                       root=tmp_path)


def buy(px=0.51, size=50, token="T1"):
    return OrderIntent(token, BUY, px, size, "t")


def test_ok_order_passes(rm):
    d = rm.check(buy(), market(), book(), acct())
    assert d.ok, d.reason


def test_max_order_usd(rm):
    d = rm.check(buy(size=100), market(), book(), acct())          # $51 > $50
    assert not d.ok and "max_order_usd" in d.reason


def test_position_per_market_pct(rm):
    # equity 1000 -> 5% = $50 per market; already $30 in T2 (same market, other outcome)
    d = rm.check(buy(size=50), market(), book(), acct(equity=1000, cash=1000, pos={"T2": 30.0}))
    assert not d.ok and "market exposure" in d.reason
    # open BUY orders count too
    d = rm.check(buy(size=20), market(), book(), acct(equity=1000, cash=1000, ob={"T1": 45.0}))
    assert not d.ok and "market exposure" in d.reason


def test_total_exposure(rm):
    pos = {f"X{i}": 290.0 for i in range(10)}                       # $2,900 of $10k = 29%
    d = rm.check(buy(size=60), market(), book(), acct(pos=pos))     # +$30.6 -> 29.3% ok
    assert d.ok, d.reason
    d = rm.check(buy(size=60), market(), book(), acct(pos={**pos, "X10": 90.0}))   # 29.9% + 0.3% > 30%
    assert not d.ok and "total exposure" in d.reason


def test_insufficient_cash(rm):
    d = rm.check(buy(), market(), book(), acct(cash=10))
    assert not d.ok and "cash" in d.reason


def test_daily_loss_halt_and_next_day_resume(tmp_path):
    now = [1_800_000_000.0]                                         # fixed clock
    rm = RiskManager(RiskLimits(kill_switch_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "r.json")),
                     root=tmp_path, clock=lambda: now[0])
    rm.update_equity(10_000)
    rm.update_equity(9_750)                                         # -2.5%: fine
    assert rm.halted() is None
    rm.update_equity(9_690)                                         # -3.1%: halt for the day
    assert "daily loss" in rm.halted()
    assert rm.check(buy(), market(), book(), acct()).halt
    # restart does not clear it
    rm2 = RiskManager(rm.l, root=tmp_path, clock=lambda: now[0])
    assert "daily loss" in rm2.halted()
    now[0] += 86_400                                                # next UTC day
    rm2.update_equity(9_690)
    assert rm2.halted() is None


def test_drawdown_kill_needs_manual_reset(tmp_path):
    now = [1_800_000_000.0]
    lim = RiskLimits(kill_switch_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "r.json"),
                     max_daily_loss_pct=0.99)                       # isolate the drawdown rule
    rm = RiskManager(lim, root=tmp_path, clock=lambda: now[0])
    rm.update_equity(10_000)
    rm.update_equity(12_000)                                        # new peak
    now[0] += 86_400 * 3
    rm.update_equity(10_150)                                        # -15.4% from peak
    assert "drawdown" in rm.halted()
    now[0] += 86_400 * 3
    rm.update_equity(10_500)
    assert "drawdown" in rm.halted()                                # does not self-heal
    RiskManager(lim, root=tmp_path, clock=lambda: now[0])           # restart keeps it
    assert "drawdown" in RiskManager(lim, root=tmp_path, clock=lambda: now[0]).halted()
    rm.reset_drawdown(10_500)
    assert rm.halted() is None


def test_orders_per_hour(tmp_path):
    now = [1_800_000_000.0]
    rm = RiskManager(RiskLimits(max_orders_per_hour=3, kill_switch_file=str(tmp_path / "KILL"),
                                state_file=str(tmp_path / "r.json")), root=tmp_path, clock=lambda: now[0])
    for _ in range(3):
        assert rm.check(buy(), market(), book(), acct()).ok
        rm.record_order()
    d = rm.check(buy(), market(), book(), acct())
    assert not d.ok and "orders/hour" in d.reason
    now[0] += 3601
    assert rm.check(buy(), market(), book(), acct()).ok


def test_min_liquidity(rm):
    d = rm.check(buy(), market(liq=1_000), book(), acct())
    assert not d.ok and "liquidity" in d.reason


def test_min_book_depth(rm):
    d = rm.check(buy(), market(), book(size=100), acct())          # ~$100 visible within 5c
    assert not d.ok and "depth" in d.reason


def test_max_spread(rm):
    d = rm.check(buy(px=0.60), market(), book(bid=0.40, ask=0.60), acct())
    assert not d.ok and "spread" in d.reason
    one_sided = OrderBook("T1", bids=[], asks=[Level(0.5, 1000)])
    assert "one-sided" in rm.check(buy(px=0.5), market(), one_sided, acct()).reason


def test_blacklist(rm):
    rm.l.blacklist_categories = ["sports", "up-or-down"]
    assert "blacklisted" in rm.check(buy(), market(tags=["sports", "nba"]), book(), acct()).reason
    assert "blacklisted" in rm.check(buy(), market(slug="btc-up-or-down-15m"), book(), acct()).reason
    assert rm.check(buy(), market(tags=["politics"]), book(), acct()).ok


def test_tick_and_min_size_and_price_band(rm):
    assert "tick" in rm.check(buy(px=0.515), market(), book(), acct()).reason
    assert "min" in rm.check(buy(size=2), market(), book(), acct()).reason
    assert "outside" in rm.check(buy(px=0.99), market(), book(bid=0.98, ask=0.99), acct()).reason


def test_sell_reduce_only_no_shorting(rm):
    s = OrderIntent("T1", SELL, 0.49, 10, "t")
    assert "no shorting" in rm.check(s, market(), book(), acct()).reason
    # a reduce-only sell is allowed even if it would be blocked by liquidity / blacklist rules
    rm.l.blacklist_categories = ["politics"]
    assert rm.check(s, market(liq=10), book(), acct(held={"T1": 10})).ok


def test_kill_switch_file(rm, tmp_path):
    (tmp_path / "KILL").write_text("stop")
    d = rm.check(buy(), market(), book(), acct())
    assert not d.ok and d.halt and "kill switch" in d.reason
    s = OrderIntent("T1", SELL, 0.49, 10, "t")                      # even reduce-only is blocked
    assert not rm.check(s, market(), book(), acct(held={"T1": 10})).ok


def test_corrupt_state_file_fails_closed(tmp_path):
    (tmp_path / "r.json").write_text("{not json")
    rm = RiskManager(RiskLimits(kill_switch_file=str(tmp_path / "KILL"), state_file=str(tmp_path / "r.json")),
                     root=tmp_path)
    assert rm.halted() is not None


def test_unknown_config_key_rejected():
    with pytest.raises(ValueError):
        RiskLimits.from_dict({"max_order_usdd": 10})
