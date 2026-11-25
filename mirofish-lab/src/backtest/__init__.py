"""MiroFish-lab backtester (04).  Quick start:

    import sys; sys.path.insert(0, 'src')
    from backtest import run_backtest, BacktestConfig
    res = run_backtest(signal_df, BacktestConfig(bankroll=10_800))
    res.summary(); res.trades; res.equity

See engine.py docstring for the signal_df schema and timing / accounting rules.
"""
from .engine import BacktestConfig, BacktestResult, PriceStore, default_store, parse_exit_rule, run_backtest
from .costs import PlaceholderCost, ZeroCost, ScaledCost, load_default_cost_model
from .metrics import (summarize, deflated_sharpe, psr, expected_max_sharpe, benjamini_hochberg, bonferroni,
                      edge_tstat_clustered, white_reality_check, sharpe, max_drawdown)

__all__ = ["run_backtest", "BacktestConfig", "BacktestResult", "PriceStore", "default_store", "parse_exit_rule",
           "PlaceholderCost", "ZeroCost", "ScaledCost", "load_default_cost_model", "summarize", "deflated_sharpe",
           "psr", "expected_max_sharpe", "benjamini_hochberg", "bonferroni", "edge_tstat_clustered",
           "white_reality_check", "sharpe", "max_drawdown"]
