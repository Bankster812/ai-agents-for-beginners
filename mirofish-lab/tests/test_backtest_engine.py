"""Accounting tests for src/backtest (run: python3 -m pytest -q tests/  or  python3 tests/test_backtest_engine.py).

Toy case (hand computed, PlaceholderCost: half-spread 1.0c for $100k-$1M volume, 1.5c below $100k,
impact 0.5c per $1k, no fee), bankroll $1,000, max 5% per position:

t0 = 2025-01-01 00:00 UTC
Market A (vol $200k, YES won, resolves t0+60h). Bars: t0+1h 0.40, t0+2h 0.42, t0+26h 0.50, t0+50h 0.60
  signal YES at t0+1h (same ts as a bar)  -> fills at NEXT bar t0+2h, mid 0.42
  budget 5% * 1000 = 50 ; px = 0.42 + 0.010 + 0.005*50/1000 = 0.43025 ; shares = 50/0.43025 = 116.211505
  resolves YES -> payout 116.21 ; pnl +66.21
Market B (vol $50k, YES won => NO loses, resolves t0+100h). Bars: t0+3h 0.30, t0+4h 0.25, t0+28h 0.20, t0+30h 0.90
  signal NO size_usd=100 at t0+3h -> fills t0+4h, NO mid 0.75; book equity 950+50=1000 -> capped to 50
  px = 0.75 + 0.015 + 0.00025 = 0.76525 ; shares = 65.338125
  exit hold:24h -> first bar >= t0+28h is t0+28h, NO mid 0.80
  sell px = 0.80 - 0.015 - 0.005*(65.338125*0.80)/1000 = 0.78473865 ; proceeds 51.27 ; pnl +1.27
Day-end equity: day0 997.81 (900 cash + 116.2115*0.42 + 65.3381*0.75); day1 1009.38 (900 + 116.2115*0.50 + 51.27);
day2 1067.48 (A resolved at t0+60h). Final = 1067.48.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backtest import BacktestConfig, PlaceholderCost, run_backtest, PriceStore  # noqa: E402

T0 = 1735689600
H = 3600


def _toy():
    prices = pd.DataFrame({
        "market_id": [1, 1, 1, 1, 2, 2, 2, 2],
        "ts": [T0 + H, T0 + 2 * H, T0 + 26 * H, T0 + 50 * H, T0 + 3 * H, T0 + 4 * H, T0 + 28 * H, T0 + 30 * H],
        "p_yes": [0.40, 0.42, 0.50, 0.60, 0.30, 0.25, 0.20, 0.90],
        "after_known": 0,
    })
    markets = pd.DataFrame({
        "market_id": [1, 2], "volume_usd": [200_000.0, 50_000.0], "category": ["politics", "sports"],
        "is_short_auto": [0, 0], "event_id": [10, 20], "neg_risk": [0, 0], "end_ts": [T0 + 60 * H, T0 + 100 * H],
        "resolved_ts": [T0 + 60 * H, T0 + 100 * H], "yes_won": [1, 1], "start_ts": [T0, T0],
        "sample_weight": [1.0, 1.0]})
    return PriceStore(prices, markets)


def _cfg(**kw):
    base = dict(bankroll=1000.0, cost_model=PlaceholderCost(), start_ts=T0, end_ts=T0 + 3 * 86400, accrue_cash_yield=False)
    base.update(kw)
    return BacktestConfig(**base)


def test_toy_reconciles_to_the_cent():
    sig = pd.DataFrame({"market_id": [1, 2], "ts": [T0 + H, T0 + 3 * H], "side": ["YES", "NO"],
                        "target_weight": [0.05, np.nan], "size_usd": [np.nan, 100.0],
                        "exit_rule": ["resolve", "hold:24h"]})
    res = run_backtest(sig, _cfg(), store=_toy())
    t = res.trades.set_index("market_id")
    assert len(t) == 2
    assert t.loc[1, "fill_ts"] == T0 + 2 * H                      # next bar, not the signal bar
    assert round(t.loc[1, "entry_px"], 5) == 0.43025
    assert round(t.loc[1, "shares"], 6) == 116.211505
    assert round(t.loc[1, "pnl"], 2) == 66.21
    assert round(t.loc[2, "entry_cost"], 2) == 50.00             # 5% cap binds on size_usd=100
    assert round(t.loc[2, "entry_px"], 5) == 0.76525
    assert t.loc[2, "close_ts"] == T0 + 28 * H
    assert round(t.loc[2, "close_value"], 2) == 51.27
    assert round(t.loc[2, "pnl"], 2) == 1.27
    eq = res.equity.round(2).tolist()
    assert eq[:3] == [997.81, 1009.38, 1067.48], eq
    assert eq[-1] == 1067.48
    s = res.summary()
    assert round(s["net_pnl_usd"], 2) == 67.48
    assert abs(s["total_return"] - 0.06748) < 1e-4


def test_no_same_bar_and_band_and_one_position():
    st = _toy()
    sig = pd.DataFrame({"market_id": [1, 1, 2], "ts": [T0 + H - 1, T0 + 2 * H, T0 + 30 * H],
                        "side": ["YES", "YES", "NO"], "target_weight": 0.05})
    res = run_backtest(sig, _cfg(), store=st)
    # signal 1s before the first bar fills AT that bar (it is strictly later); second signal blocked: position open
    assert res.trades.fill_ts.tolist() == [T0 + H]
    reasons = set(res.skipped.reason)
    assert "position_open" in reasons
    assert "no_next_bar" in reasons                                # B has no bar after t0+30h


def test_event_cap_and_fee():
    st = _toy()
    st.meta[2]["event_id"] = 10                                    # same event as market 1
    sig = pd.DataFrame({"market_id": [1, 2], "ts": [T0 + H, T0 + 3 * H], "side": ["YES", "NO"],
                        "target_weight": 0.05})
    res = run_backtest(sig, _cfg(max_event_frac=0.08), store=st)
    t = res.trades.set_index("market_id")
    assert round(t.loc[2, "entry_cost"], 2) == 30.00                # 8% of 1000 - 50 already open on event 10
    fee = PlaceholderCost(fee_rate=0.02)
    res2 = run_backtest(sig.iloc[:1], _cfg(cost_model=fee), store=_toy())
    t2 = res2.trades.iloc[0]
    # shares = 50 / (0.43025 * 1.02)
    assert abs(t2.shares - 50 / (0.43025 * 1.02)) < 1e-9
    assert abs(t2.entry_fee + t2.shares * t2.entry_px - 50) < 1e-9


if __name__ == "__main__":
    test_toy_reconciles_to_the_cent()
    test_no_same_bar_and_band_and_one_position()
    test_event_cap_and_fee()
    print("all backtest accounting tests passed")
