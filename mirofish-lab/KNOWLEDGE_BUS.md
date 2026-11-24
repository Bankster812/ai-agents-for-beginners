# MiroFish Lab — Knowledge Bus Protocol

Goal: find out whether a MiroFish-style "crowd-reaction" approach (as advertised in the
msbintel / Hanako Instagram post) produces POSITIVE, ROBUST, OUT-OF-SAMPLE returns on real
prediction-market (Polymarket) data after realistic costs — and, if not, what does.

## Shared assumptions (all agents use these unless a brief overrides them with evidence)
- Base bankroll: **$10,800 USDC (~€10,000)**. Sensitivity runs: $2,000 and $50,000.
- Max risk per position: 5% of equity (base). No leverage (Polymarket is fully collateralised).
- Time split (walk-forward): TRAIN = markets resolved before 2025-07-01; TEST = resolved on/after 2025-07-01.
  Nothing tuned on TEST. TEST is touched once per final strategy.
- Today is 2026-11-24.

## Directory layout (absolute root: /home/user/ai-agents-for-beginners/mirofish-lab)
- knowledge/   — one brief per specialist: `NN_<role>.md`  (READ ALL before you start; WRITE yours when done)
- data/        — processed datasets (data/raw/ is gitignored)
- src/         — reusable code (importable modules, no notebooks)
- results/     — backtest outputs, tables, charts (CSV / MD / PNG)

## Brief template (every specialist MUST use it)
```
# <NN> <Role> — brief
## Delivered (files + one-line description each)
## Key facts other specialists MUST use   (numbers, schemas, function signatures)
## Assumptions I made (and how sensitive results are to them)
## Findings / evidence
## Warnings & pitfalls for downstream roles
## Open questions → addressed to <role>
```

## Roles
01 Data Engineer · 02 MiroFish Code Analyst · 03 Prediction-Market Microstructure Researcher ·
04 Quant Backtester · 05 Swarm Modeler (MiroFish-faithful ABM) · 06 Risk Manager / Adversarial Validator

## EXTENSION (added 2026-11-24): multi-asset "real markets" track
User request: also test on real markets — equities (incl. penny stocks), futures, options, commodities,
currencies, crypto — and evaluate the analysis style of HKCM (Stuttgart) plus classic TA (e.g. Bollinger Bands).
- Bankroll unchanged: €10,000 base (≈ $10,800); sensitivity €2,000 / €50,000. Investor is a retail trader in the DACH region.
- Broker cost baseline (retail, e.g. Interactive Brokers-like): stocks/ETFs 0.05% per side (min $1), +half-spread 0.02% liquid ETFs /
  0.10% large caps / 1–3% penny stocks; futures ~$2.50 per contract per side + 1 tick slippage; FX 0.2–0.5 pip + commissions;
  crypto 0.10% taker; options $0.65/contract + half the bid-ask (assume 3–5% of premium for liquid index options).
  Micro futures (MES, MNQ, MGC, MCL, M6E …) are the realistic size for €10k — use their multipliers.
- Multi-asset time split: TRAIN ≤ 2018-12-31, VALIDATION 2019-01-01..2021-12-31, TEST 2022-01-01..latest. Tune on TRAIN, select on VALIDATION, TEST once.
- Taxes (DE, informational only): Abgeltungsteuer 25% + Soli (+KiSt) on net gains; results reported PRE-tax, with one post-tax line.
- Roles: 08 Multi-Asset Data Engineer · 09 Technical-Analysis & HKCM Specialist · 10 Systematic Multi-Asset & Options Strategist.
- Data dir: data/multiasset/  ·  results prefix: results/08_*, 09_*, 10_*.
