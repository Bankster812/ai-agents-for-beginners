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
