# 09 Technical-Analysis & HKCM Methodology Specialist — brief
(Written in stages. Status: HKCM facts DONE. Backtest section appended below when it finishes.)

## Delivered (files + one-line description each)
- `knowledge/09_ta_hkcm.md`: this brief.
- `src/ta/`: causal zigzag/Elliott and Bollinger rules, engine, statistics, runner (see the Backtest section).
- `results/09_*`: backtest tables (see the Backtest section).

## Key facts other specialists MUST use: what HKCM actually is (verified 2026-11-24/25, public pages only)
**Domain trap:** `hkcm.de` is **HKCM Engineering e.K., a magnet supplier** (https://www.hkcm.de/). It is unrelated to the trading firm.
The analysis firm is at **https://www.hkcm.com/**.

| Item | Verified fact | Source |
|---|---|---|
| Entity | Hopf-Klinkmüller Capital Management GmbH & Co. KG, Hasenbergsteige 5, 70178 Stuttgart. HRA 725037. General partner: HK Verwaltungs GmbH. Managing director: Philip Klinkmüller. Co-founder: Philip Hopf. | https://www.hkcm.com/impressum, https://www.hkcm.com/ueber-uns |
| Method | **Elliott-Wave analysis + Fibonacci projections/levels**, "structured chart analysis", plus unnamed "special indicators". "Über uns" also lists macro, market cycles, sentiment and risk management. **No public page mentions Bollinger Bands.** The user probably remembered this wrong. | https://www.hkcm.com/, https://www.hkcm.com/faq, https://www.hkcm.com/ueber-uns |
| Output | Written analyses, plus optional video. Scenarios with **"Zielzonen" (target zones) and "Einstiegs-/Tradingbereiche" (entry/trading zones)**, support and resistance, and a primary plus alternative scenarios. A customer quote mentions "%-probabilities". Daily updates. An analysis cycle runs 5–10 working days. The FAQ promises "real-time signals and short messages on entry/exit opportunities". **Stop-losses are not mentioned.** Covers 150+ stocks, indices, crypto and commodities (">200 markets"). Free daily YouTube content. App. | https://www.hkcm.com/faq, https://www.hkcm.com/produkte |
| Prices (Nov 2026) | Per package per month (1-month → 12-month term): index (DAX, S&P, Nasdaq…) and single commodity €24.95 → €21.21. BTC/ETH €29.95 → €25.46. Altcoins and precious metals €59.95 → €50.96. "Hype-Aktien"/Minen €69.95 → €59.46. KI/TECH33/Euro-/US-/Asia-Titans €99.95 → €84.96. Video costs extra. **Subscriptions auto-renew** (AGB §3). Cancel ≥5 days before renewal. | https://www.hkcm.com/produkte, https://www.hkcm.com/agb, https://www.hkcm.com/faq |
| Track-record claims | Home and products pages claim "**über 70 % Trefferquote**", defined only as "*In über 70 % der Fälle trifft der Markt unsere definierten Handelsspannen*" (the market **reaches** our defined ranges). "Über uns" shows "89 % Trefferquote" and an unlabelled "74 %". **No sample, period, definition of a hit, or audited P&L is published.** One testimonial claims "+1323 % since 2024" (anecdote). "1.3 m active customers" is most likely social-media reach (unverified). BlackBull "awards" and Reuters / S&P Global "in Zusammenarbeit mit" appear as **logos only, with no text**. | https://www.hkcm.com/, https://www.hkcm.com/ueber-uns |
| Regulatory status | The **BaFin company database has no entry** for "HKCM", "Hopf" or "Klinkmüller" (queried 2026-11-24, https://portal.mvp.bafin.de/database/InstInfo/). The Impressum and AGB mention no BaFin licence, no §34f GewO, no §86 WpHG notification and no press-code status. AGB §11(7): "keine Anlageberatung". FAQ: analyses are "keine Empfehlungen, Assets zu kaufen, zu halten oder zu verkaufen". **It is a subscription publisher of general analyses, not a licensed adviser or asset manager.** No conflict-of-interest disclosure (positions held by analysts) appears in the AGB. | links above |
| Independent reviews | Weak evidence base. Trustpilot hkcm.com: 3.2/5, only 1 review in the last 12 months, unclaimed profile (https://www.trustpilot.com/review/hkcm.com). Review blogs (kagels-trading.de, finwiss.de) are SEO or affiliate pages, and **Kagels is a competing signal seller**. Their recurring criticisms: the hit rate cannot be verified, and alternative scenarios mean a forecast is "never wrong". **No independent audit of HKCM forecasts exists.** | https://www.kagels-trading.de/hkcm-erfahrungen/ |

**Why "70 % of ranges reached" is not evidence of skill:** a zone whose distance from price is a few ATR gets touched by a driftless random walk with high probability over a 5–10-day cycle. If there are two zones (primary and alternative, one above and one below), "one of them reached" is close to certain. The metric says nothing about P&L, which depends on the path, the stop and the timing. A simulated base rate appears in the Backtest section (`results/09_zone_hit_baserate.csv`).

## Assumptions I made
- I formalised "HKCM-style" as **mechanical** Elliott-zigzag + Fibonacci-zone limit entries. Real HKCM labelling is discretionary and cannot be backtested as-is, because no timestamped public forecast archive exists. My rules are an approximation of the published method, not their actual calls.
