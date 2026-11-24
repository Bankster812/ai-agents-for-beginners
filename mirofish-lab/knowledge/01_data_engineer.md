# 01 Data Engineer — brief

Built 2026-11-24 from the public Polymarket Gamma + CLOB APIs. Total fetch time ≈ 12 min Gamma universe + 7 min CLOB
(4 threads). Everything is cached under `data/raw/` (gitignored, ~240 MB), so `python3 src/fetch_polymarket.py all`
re-runs offline in about 2 minutes.

## Delivered (files + one-line description each)
- `data/markets.csv.gz`: 4,000 resolved binary markets, one row each: 3,600 regular plus 400 short-auto crypto Up/Down.
- `data/prices_hourly.csv.gz`: 3.82 M rows of hourly YES-token prices (`market_id, ts, p_yes, after_known`). Points after resolution are dropped.
- `data/shocks.csv.gz`: 26,122 candidate news shocks (|Δp| ≥ 0.10 within ≤ 6h) on regular markets. Each row has the price before and after, follow-up prices at +1 bar, 6h, 24h, 72h and 7d, and the final outcome.
- `data/prices_short_auto_1min.csv.gz`: 1-minute YES ("Up") price series for the 400 short-auto markets (868k rows).
- `src/data_loader.py`: `load_markets()`, `load_prices()`, `load_shocks()` and `train_test_split_by_time(cutoff='2025-07-01')`. The full schema is in the module docstring.
- `src/fetch_polymarket.py`: the reproducible pipeline. Stages are `universe`, `sample`, `prices`, `build` and `all`. Its docstring records the API behaviour found below.
- `src/data_quality.py` writes `results/01_data_quality_report.md` (the full QA report) and `results/01_*.csv`. Those CSVs are the calibration tables (overall, train, test), shock follow-through tables, and sample counts.
- `data/raw/sample_log.json`: the universe funnel and exclusion counts. `data/raw/population_clean.csv.gz` holds all 142,577 clean markets in the population (without price paths). Use it to reweight or re-sample.
- `data/raw/resolution_validation.csv`: a check of `yes_won` against the CLOB `winner` flag on 200 random markets.

## Key facts other specialists MUST use
**Loader API** (`import sys; sys.path.insert(0, 'src'); from data_loader import *`):
```
load_markets(include_short_auto=True, cutoff='2025-07-01') -> DataFrame   # adds period ('2025H2'), split ('train'/'test'), end_dt, resolved_dt
load_prices(market_ids=None, drop_after_known=False, short_auto_1min=False) -> DataFrame[market_id, ts, p_yes, after_known]
load_shocks(exclude_pinned=False, cutoff='2025-07-01') -> DataFrame       # adds split by detect_ts
train_test_split_by_time(cutoff='2025-07-01', include_short_auto=False, with_prices=True, purge=True) -> dict
   keys: markets_train(1783), markets_test(1817), prices_train, prices_test, shocks_train(11,152), shocks_test(14,147)
```
Split rule: TRAIN = `resolved_ts < 2025-07-01`. `shocks_test` only contains shocks on test markets with `detect_ts ≥ cutoff`. That purges 823 pre-cutoff shocks on test markets.

**markets.csv.gz schema.** Times are integer unix seconds UTC.
`market_id, condition_id, question, category{politics,sports,crypto,macro,geopolitics,culture,other}, is_short_auto, start_ts, end_ts (scheduled endDate), resolved_ts (Gamma closedTime), volume_usd (lifetime), liquidity_usd, neg_risk, yes_won{0,1}, yes_token_id, n_price_points, fidelity_min`. Extra columns: `slug, event_id, is_recurring, outcome_kind, first_price_ts, last_price_ts, last_raw_price_ts, n_points_after_resolution_dropped, last_p_yes, known_ts, frac_hours_changed, stratum, stratum_pop, sample_weight, tags`.
- `known_ts` is the earliest time after which `p_yes` stays pinned on the winning side (≥0.99 or ≤0.01) until resolution. **It is computed from the outcome, so use it only for auditing and labels, never as a feature.**
- `sample_weight = stratum_pop / n_sampled`. Use it to reweight sample statistics back to the population.
- `event_id`: legs of one neg-risk event are correlated. Cluster or block by `event_id` when computing standard errors. There are 2,748 distinct events, and 54% of regular markets are neg-risk legs.

**shocks.csv.gz schema:**
`market_id, category, pre_ts, detect_ts, p_pre, p_post, dp, direction(±1), lag_h, p_next_bar, next_bar_ts, p_6h, p_24h, p_72h, p_7d, yes_won, p_final, hours_to_end, hours_to_resolved, post_pinned, after_known, volume_usd, neg_risk, event_id, pre_vol_24h, spike_revert_next_bar, n_shocks_market`.
- Detection rule: at each hourly bar j, look back up to 6h. A shock fires at the first j where `p_j − min(window) ≥ 0.10` (an up shock) or `max(window) − p_j ≥ 0.10` (a down shock). `p_pre` is that window extreme, and the scan restarts at j. The first 6h of each market's price history are skipped.
- `p_h` is the as-of price at `detect_ts + h`. It is NaN if that time is past the last price.
- **Tradeable entry = `p_next_bar`, not `p_post`.** You only see the shock once the `detect_ts` bar prints.

**Price semantics.** `p_yes` comes from CLOB `/prices-history`. It is Polymarket's *displayed* price: the midpoint, or the last trade when the spread is above $0.10. **It is not an executable bid or ask**, so 04 must apply a spread/slippage model. 03's `data/orderbook_snapshot.csv` looks like the right input for that. Timestamps are the actual sample times, such as `hh:00:02` or `hh:20:02`. They are not floored to the hour, so use as-of (backward) joins.

**Headline counts.**
| | regular | short-auto |
|---|---|---|
| sample | 3,600 | 400 |
| train / test | 1,783 / 1,817 | 100 / 300 |
| by year (regular) | 2023: 185 · 2024: 1,016 · 2025: 1,204 · 2026: 1,195 | 2025H1–2026H2, 100 per half-year |
| YES base rate | **0.287** (weighted 0.332) | 0.4525 ("Up") |
| median hourly points | 315 (5%: 24, 95%: 4,316) | 24 hourly, about 1,450 at 1-min |

**Base-rate calibration, p_yes 7 days before the scheduled endDate.** Regular markets only; the snapshot must be before `resolved_ts`. n = 2,008, Brier 0.083. The time choice is ex-ante, so this table has no look-ahead.
| bucket | n | mean_p | yes_rate | se | yes_rate − mean_p |
|---|---|---|---|---|---|
| 0.00-0.05 | 1001 | 0.013 | 0.012 | 0.003 | -0.001 |
| 0.05-0.10 | 166 | 0.071 | 0.054 | 0.018 | -0.017 |
| 0.10-0.20 | 161 | 0.143 | 0.161 | 0.029 | +0.018 |
| 0.20-0.30 | 113 | 0.243 | 0.274 | 0.042 | +0.032 |
| 0.30-0.40 | 112 | 0.348 | 0.429 | 0.047 | +0.081 |
| 0.40-0.50 | 92 | 0.450 | 0.543 | 0.052 | +0.093 |
| 0.50-0.60 | 66 | 0.540 | 0.515 | 0.062 | -0.025 |
| 0.60-0.70 | 66 | 0.648 | 0.576 | 0.061 | -0.072 |
| 0.70-0.80 | 49 | 0.744 | 0.735 | 0.063 | -0.009 |
| 0.80-0.90 | 51 | 0.845 | 0.824 | 0.053 | -0.021 |
| 0.90-0.95 | 31 | 0.927 | 0.968 | 0.032 | +0.041 |
| 0.95-1.00 | 100 | 0.976 | 1.000 | 0.000 | +0.024 |

The market is well calibrated overall. The 0.30–0.50 buckets show a modest YES underpricing of about +8–9 pp, with se ≈ 5 pp. The same direction appears in both train and test (`results/01_calibration_7d_before_endDate_{train,test}.csv`), but the sample is small, so treat it as a hypothesis, not an edge. There is no favourite–longshot bias at the tails.

## Assumptions I made (and how sensitive results are to them)
1. **The universe is a stratified sample, not a census.** The clean population is 142,577 markets: 62k Yes/No regular plus 80k Up/Down. Regular markets were sampled uniformly at random inside half-year × category cells, about 86 per cell (water-filled), with seed 42. Every 2023 market was taken because 2023 cells are tiny (185 regular markets ≥ $50k exist in total). As a result the sample over-represents 2023–24 and the minority categories relative to the population, which is 2026-heavy and sports/crypto-heavy. Use `sample_weight` for population-level statements.
2. **Only Yes/No outcomes are included**, plus Up/Down for short-auto. I excluded 68,653 two-outcome markets with team-name or Over/Under outcomes, which are mostly sports games. Sports in this dataset therefore means "Will X win?"-style Yes/No legs.
3. **Exclusions for unclean resolution:** 103 UMA `proposed`, 18 with no status, 15 resolved 50-50, 2 with ambiguous prices, and 1 `settled`. That is 139 of 142,716, or 0.1%. Final `outcomePrices` with tiny dust values (such as 0.9999996) were treated as clean.
4. **Categories** come from Gamma tags first (ordered rules: sports > crypto > geopolitics > politics > macro > culture), with a keyword regex on question and slug as fallback. "other" is mostly weather, tech/AI, science and company events. Multi-tag markets go to the first matching rule, so for example "Trump tariffs" lands in politics, not macro.
5. **`is_short_auto`** = Up/Down crypto window markets (5m, 15m, 1h, 4h, daily), or `Recurring`-tagged markets with a planned life of ≤ 48h (daily crypto-price and daily temperature markets). Weekly and monthly recurring crypto "hit price" markets are **not** flagged; they are regular markets with `is_recurring=1`.
6. I dropped a leading 0.5 placeholder price when the next point differs by ≥ 0.05 (413 markets).
7. The volume filter is **lifetime volume ≥ $50k** (≥ $25k is cached, so it can be relaxed via `stage_sample(vol_min=25000)`).

## Findings / evidence
- **API behaviour, verified.**
  - Gamma `/markets` caps `limit` at 100 and returns an error above offset ~2,000. Use `/markets/keyset?after_cursor=` with `volume_num_min`, `end_date_min`/`end_date_max` and `include_tag=true`.
  - CLOB `prices-history` with `interval=max` returns **empty** history for every resolved market.
  - `startTs`+`endTs` windows longer than about 14 days are rejected.
  - **`startTs` alone, with no `endTs`, returns the full history in one call** at the requested fidelity. Hourly (fidelity=60) is available back to 2023, so old markets did not get coarser data: median spacing is 60.0 min, and only 21 markets differ, all very short ones. fidelity=1 also works.
  - Rate limiting was mild: a few 429s on Gamma and a slow patch on CLOB, all handled by backoff.
- **Resolution check:** `yes_won` matched the CLOB `winner` flag on 199/199 markets that CLOB knows. The 200th was a 2022 AMM-era market not on CLOB. The last kept price is on the wrong side of 0.5 in only 0.36% of markets.
- **Coverage:**
  - 9 markets (2022-created AMM-era, ids 240k–248k) have **no CLOB history**, and 125 have fewer than 24 points.
  - Median price span is 99% of market life, and the first price arrives a median 1.3h after `start_ts`.
  - 24 markets have a gap longer than 24h; none has a gap longer than 72h.
  - `liquidity_usd` is 0 for 96.5% of markets because it is a post-close snapshot. **Do not use it as a liquidity measure.** Proxies: `volume_usd`, `frac_hours_changed` (median 0.43), and `n_shocks_market` (a high value means a noisy, thin book).
- **Timestamp alignment:** the last raw price is a median 0.37h *before* `resolved_ts` (5–95%: −0.9h to −0.02h). Only 2.7% of markets had points after `resolved_ts`; 22,414 such points were dropped. `resolved_ts − end_ts` has median +8h, but 18.8% of markets resolve more than 24h *before* their endDate (early YES on "by-date" questions) and 23.2% resolve more than 24h *after* it.
- **Resolution leakage via pinned prices:**
  - 42.8% of regular markets are pinned on the winning side ≥ 24h before resolution, and 16.0% ≥ 7 days before.
  - By category, macro is 29.5% and politics 24.9% for ≥ 7 days; sports is lowest at 13.5% for ≥ 24h.
  - 3.4% are never pinned.
  - Any strategy that "buys at 0.995" in that zone collects pseudo-returns that actually just reflect the cost of capital and resolution risk. `after_known` is provided for auditing only.
- **Shocks:**
  - There are 26,122 shocks (25,400 clean, meaning not pinned and before known) on 2,630 markets. Up and down shocks are balanced (12.8k / 12.6k), and the median market has 17 shocks.
  - Mid-price follow-through, `direction × (p_h − p_post)`, before costs, is mean **−0.023 at the next bar, −0.045 at 24h, −0.059 at 7d, and −0.049 to final**. That is mean *reversion*, but the median to final is +0.07 and 55% continue to the final outcome. This is a skewed distribution: most shocks are right, a minority reverse hard.
  - Filtered (clean, not a 1-bar spike, volume ≥ $250k, measured from **next bar**): −0.011 at 6h, −0.020 at 24h, −0.028 to final, n ≈ 7k. The reversion shrinks a lot once bid/ask-bounce artefacts are removed.
  - Sports shows the strongest reversion (−0.07 at 24h). See `results/01_shock_continuation*.csv`.

## Warnings & pitfalls for downstream roles
1. **(04, 06) Prices are mid/last-trade, not executable.**
   - Reversion of 2–5 pp after a 10 pp jump is the same order as the half-spread on thin books.
   - Before claiming a fade-the-herd edge, apply a spread model: 03's snapshot, or at least 1–3 pp per side on markets under $250k.
   - Never use `p_post` as the fill price; use `p_next_bar` at the earliest.
2. **(06) There is ex-post selection.**
   - Lifetime volume ≥ $50k is only known after the fact, so markets that became big are over-represented.
   - Only *closed* markets that Gamma still serves are included.
   - Non-Yes/No two-outcome markets are excluded.
   - Results generalise to "markets that ended up liquid", not to every market at entry time.
3. **(03, 04, 05) Snapshots keyed to `resolved_ts` leak information.** The "24h before resolution" calibration table shows YES underpriced in every bucket by +3 to +11 pp. That is look-ahead: "by-date" markets resolve YES early, right after the event, so anchoring on the realised resolution time selects imminent YES events. Use **scheduled `end_ts`** (or a fixed calendar time) for anything tradeable. `results/01_calibration_7d_before_endDate*.csv` is the clean table.
4. **(04, 06) Correlation and duplication.**
   - Neg-risk legs of the same `event_id` are mutually exclusive; for example, 12 candidate legs in one election.
   - Treat events, not legs, as the independent unit. Otherwise standard errors are understated and "NO on every longshot leg" looks far safer than it is.
5. **(04) Base rate.** Only 28.7% of sampled regular markets resolved YES (33% weighted). A naive "always buy NO" baseline must be beaten after costs.
6. **(05) Short-auto markets are a different regime.**
   - They are 5- or 15-minute windows whose hourly series has about 24 points, mostly pre-window.
   - Use `prices_short_auto_1min.csv.gz`. They are excluded from `shocks.csv.gz`.
7. **(all) The test period is 2025H2–2026H2.** Activity there is dominated by sports and crypto (see the population table in `results/01_data_quality_report.md`). Per-category results need the stratum weights before you read them as "the market".
8. The first price is often an artefact of listing time, and the first 6h are excluded from shocks. Some markets also have `start_ts` long before trading began (`first_price_ts` is the truth).

## Open questions → addressed to <role>
- → **03 Microstructure**: please map `orderbook_snapshot.csv` spreads onto `volume_usd` / `frac_hours_changed` buckets, so 04 can attach a per-market historical spread estimate. Historical order books are not available from public APIs. Historical trades are available from `data-api.polymarket.com/trades?market=<condition_id>`, and I can fetch them for the shock subset if you want a tick-level check of whether shocks are real trades or quote flicker.
- → **04 Backtester**: is the 2023–24-heavy stratified sample acceptable, or do you want a population-proportional sample, for example of 2026 sports? `stage_sample()` can regenerate either from the cache in about a minute, plus a few minutes of new CLOB fetches.
- → **05 Swarm Modeler**: the shocks currently have no news labels. If you need exogenous-news versus endogenous-flow labels, tell me the event types and I'll tag them via question keywords and timing (for example, scheduled-event markets).
- → **06 Risk**: the pinned-leakage shares and `known_ts` are there for your audit. The residual survivorship I could not measure is markets deleted or archived from Gamma; the count of 2023 markets seems low (3,683 closed, 207 ≥ $50k).
