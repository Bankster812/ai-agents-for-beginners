# 04 Quant Backtester — brief  (EARLY VERSION: API is stable, results pending)

## Delivered (files + one-line description each)
- `src/backtest/engine.py` — event-driven portfolio engine (`run_backtest`), `BacktestConfig`, `BacktestResult`, `PriceStore`.
- `src/backtest/costs.py` — placeholder cost model (until 03's `src/cost_model.py` exists; auto-adapted when it does), `ZeroCost` for gross runs, `ScaledCost` for sensitivity.
- `src/backtest/metrics.py` — summary metrics, event-clustered edge t-stat, PSR / deflated Sharpe, BH / Bonferroni, White RC / SPA bootstrap.
- `tests/test_backtest_engine.py` — hand-computed toy case reconciles to the cent (`python3 tests/test_backtest_engine.py`).
- (pending) `src/backtest/strategies.py`, `src/backtest/run_all.py`, `results/04_*.csv/md`.

## Key facts other specialists MUST use
```python
import sys; sys.path.insert(0, '/home/user/ai-agents-for-beginners/mirofish-lab/src')
from backtest import run_backtest, BacktestConfig
res = run_backtest(signal_df, BacktestConfig(bankroll=10_800, label='my_strategy'))
res.summary()   # dict: n_trades, hit_rate, edge_per_usd (net), gross_edge_per_usd, edge_t (event-clustered),
                # total_return, cagr, max_dd, sharpe (daily, excess of 4.5%), turnover, avg_utilisation ...
res.trades      # one row per filled trade (entry/exit px, shares, pnl, gross_pnl, hold_days, exit_reason ...)
res.equity      # daily UTC equity curve (pd.Series)
res.skipped     # signals not filled + reason (no_next_bar, after_resolution, entry_band, size_cap, position_open ...)
```
**signal_df columns**: `market_id` (int), `ts` (unix s, when the signal is KNOWN), `side` ('YES'/'NO' — we always BUY that
token), `target_weight` (fraction of book equity) **or** `size_usd`, `exit_rule` (default 'resolve'), optional `strategy`.
**exit_rule**: '|'-joined clauses, first to trigger wins: `resolve` · `hold:24h` / `hold:7d` (first bar ≥ fill+dur) ·
`at:<unix>` · `end:-24h` (relative to scheduled end_ts) · `tp:0.9` / `sl:0.2` (held-token mid at bar j → sell at bar j+1).
**Timing**: fill at the first bar strictly AFTER `ts` (displayed mid ± half-spread ± impact); bar must be before
`resolved_ts`, within 24h of the signal, not flagged after_known, and held-token mid in [0.01, 0.99]. Resolution pays $1/share
at `resolved_ts` (capital locked until then).
**Caps** (BacktestConfig): `max_pos_frac=0.05` of book equity (cash + cost of open positions), `liq_cap_frac=0.01` of lifetime
volume, `max_event_frac=0.10` per event_id, `min_order_usd=5`, one open position per market. Short-auto 1-min paths:
`BacktestConfig(price_source='short_auto_1min')`.
**Cost model (interim placeholder)**: half-spread 1.5¢ (volume < $100k), 1.0¢ ($100k–$1M), 0.5¢ (> $1M) per side + 0.5¢ per $1k
of order notional; fee 0. Replaced automatically by 03's model when `src/cost_model.py` appears (re-run!).
