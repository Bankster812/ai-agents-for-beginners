#!/usr/bin/env python3
"""data_quality.py — data-quality + base-rate report for the Polymarket dataset.
Writes tables to results/01_*.csv and prints a markdown summary (used in knowledge/01_data_engineer.md)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_loader import load_markets, load_prices, load_shocks, ROOT  # noqa: E402

RES = ROOT / "results"
RES.mkdir(exist_ok=True)
BUCKETS = [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, 1.0001]


def md(df: pd.DataFrame, floatfmt: str = "{:.3f}") -> str:
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else floatfmt.format(x))
    cols = [str(c) for c in d.columns]
    idx = d.index.name or ""
    lines = ["| " + " | ".join([idx] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, r in d.iterrows():
        lines.append("| " + " | ".join([str(i)] + [str(x) for x in r.values]) + " |")
    return "\n".join(lines)


def snapshot(m: pd.DataFrame, p: pd.DataFrame, ref_col: str, offset_s: int) -> pd.DataFrame:
    """As-of price at m[ref_col] - offset_s; dropped if before first price or at/after resolved_ts."""
    t = (m[ref_col].astype("float") - offset_s).rename("t_snap")
    q = pd.DataFrame({"market_id": m.market_id, "t_snap": t}).dropna().sort_values("t_snap")
    q["t_snap"] = q.t_snap.astype("int64")
    pp = p[["market_id", "ts", "p_yes"]].sort_values("ts")
    j = pd.merge_asof(q, pp, left_on="t_snap", right_on="ts", by="market_id", direction="backward",
                      tolerance=48 * 3600)
    j = j.merge(m[["market_id", "yes_won", "resolved_ts", "sample_weight", "split", "category"]], on="market_id")
    j = j[j.p_yes.notna() & (j.t_snap < j.resolved_ts.astype("float"))]
    return j


def calib(j: pd.DataFrame) -> pd.DataFrame:
    j = j.copy()
    j["bucket"] = pd.cut(j.p_yes, BUCKETS, right=False, labels=[f"{a:.2f}-{min(b,1):.2f}" for a, b in zip(BUCKETS, BUCKETS[1:])])
    g = j.groupby("bucket", observed=True)
    out = pd.DataFrame({
        "n": g.size(),
        "mean_p": g.p_yes.mean(),
        "yes_rate": g.yes_won.mean(),
        "yes_rate_w": g.apply(lambda x: np.average(x.yes_won, weights=x.sample_weight), include_groups=False),
    })
    out["se"] = np.sqrt(out.yes_rate * (1 - out.yes_rate) / out.n)
    out["edge_yes"] = out.yes_rate - out.mean_p
    return out


def main():
    m = load_markets()
    p = load_prices()
    s = load_shocks()
    qa = json.loads((ROOT / "data/raw/build_qa.json").read_text())
    slog = json.loads((ROOT / "data/raw/sample_log.json").read_text())
    out = []
    main_m = m[m.is_short_auto == 0]

    out.append("### Universe funnel\n```\n" + json.dumps({k: v for k, v in slog.items() if not isinstance(v, dict) or k in ("by_outcome_kind", "resolution_flags")}, indent=1) + "\n```")
    pop = pd.DataFrame(slog["population_main_by_period_cat"]).fillna(0).astype(int)
    pop.index.name = "period"
    out.append("### Population (clean, regular, vol>=$50k) by period x category\n" + md(pop))
    ct = main_m.pivot_table(index="period", columns="category", values="market_id", aggfunc="count", fill_value=0)
    ct["TOTAL"] = ct.sum(axis=1)
    ct.to_csv(RES / "01_sample_counts_period_category.csv")
    out.append("### Sample (regular markets) by period x category\n" + md(ct))
    sa = m[m.is_short_auto == 1].groupby(["period", "split"]).size().unstack(fill_value=0)
    out.append("### Short-auto sample by period\n" + md(sa))
    out.append("### Split\n" + md(m.groupby(["split", "is_short_auto"]).size().to_frame("n")))

    # coverage
    cov = pd.DataFrame({
        "n_price_points": m.n_price_points.describe(percentiles=[.05, .25, .5, .75, .95]),
    })
    out.append("### Hourly price points per market\n" + md(cov.T, "{:.0f}"))
    life = (m.resolved_ts - m.start_ts).astype(float)
    cover = ((m.last_price_ts - m.first_price_ts).astype(float) / life).clip(0, 2)
    gaps = []
    for mid, g in p.groupby("market_id"):
        d = np.diff(g.ts.to_numpy())
        gaps.append((mid, d.max() / 3600 if len(d) else np.nan))
    gaps = pd.DataFrame(gaps, columns=["market_id", "max_gap_h"])
    mm = m.merge(gaps, on="market_id", how="left")
    covd = {
        "markets": len(m),
        "no_price_history": int((m.n_price_points == 0).sum()),
        "lt_24_points": int((m.n_price_points < 24).sum()),
        "fidelity_min_median": float(m.fidelity_min.median()),
        "fidelity_not_60(+-1min)": int(((m.fidelity_min - 60).abs() > 1).sum()),
        "coverage_ratio_median(price span / market life)": float(cover.median()),
        "markets_max_gap_gt_24h": int((mm.max_gap_h > 24).sum()),
        "markets_max_gap_gt_72h": int((mm.max_gap_h > 72).sum()),
        "first_price_minus_start_h_median": float(((m.first_price_ts - m.start_ts).astype(float) / 3600).median()),
    }
    out.append("### Coverage\n```\n" + json.dumps(covd, indent=1) + "\n```")
    out.append("### Build QA\n```\n" + json.dumps(qa, indent=1) + "\n```")

    # timestamps & leakage
    mr = main_m[main_m.n_price_points > 0]
    lag_last = (mr.last_raw_price_ts - mr.resolved_ts).astype(float) / 3600
    early = (mr.resolved_ts - mr.end_ts).astype(float) / 3600
    lead = (mr.resolved_ts - mr.known_ts).astype(float) / 3600
    tsd = {
        "last_raw_price_minus_resolved_h_quantiles(5,50,95)": [float(x) for x in lag_last.quantile([.05, .5, .95])],
        "share_markets_with_points_after_resolved_ts": float((mr.n_points_after_resolution_dropped > 0).mean()),
        "resolved_minus_end_h_quantiles(5,25,50,75,95)": [float(x) for x in early.quantile([.05, .25, .5, .75, .95])],
        "share_resolved_more_than_24h_BEFORE_endDate": float((early < -24).mean()),
        "share_resolved_more_than_24h_AFTER_endDate": float((early > 24).mean()),
        "share_never_pinned_on_winner": float(mr.known_ts.isna().mean()),
        "pinned_lead_h_quantiles(25,50,75,90)": [float(x) for x in lead.quantile([.25, .5, .75, .9])],
        "share_pinned_>=24h_before_resolution": float((lead >= 24).mean()),
        "share_pinned_>=7d_before_resolution": float((lead >= 168).mean()),
        "share_pinned_>=24h_before_endDate": float(((mr.end_ts - mr.known_ts).astype(float) >= 24 * 3600).mean()),
        "last_price_wrong_side(last_p>0.5 != yes_won)": float(((mr.last_p_yes > 0.5).astype(int) != mr.yes_won).mean()),
        "yes_base_rate_regular": float(mr.yes_won.mean()),
        "yes_base_rate_regular_weighted": float(np.average(mr.yes_won, weights=mr.sample_weight)),
        "yes_base_rate_short_auto(Up)": float(m[m.is_short_auto == 1].yes_won.mean()),
        "neg_risk_share": float(mr.neg_risk.mean()),
    }
    out.append("### Timestamps / resolution leakage (regular markets)\n```\n" + json.dumps(tsd, indent=1) + "\n```")
    lead_cat = mr.assign(lead=lead).groupby("category").agg(
        n=("market_id", "size"), yes_rate=("yes_won", "mean"),
        pinned_ge24h=("lead", lambda x: (x >= 24).mean()), pinned_ge7d=("lead", lambda x: (x >= 168).mean()),
        median_points=("n_price_points", "median"), median_vol=("volume_usd", "median"))
    out.append("### By category\n" + md(lead_cat))

    # calibration
    for ref, off, name in (("end_ts", 7 * 86400, "7d_before_endDate"),
                           ("resolved_ts", 7 * 86400, "7d_before_resolution"),
                           ("resolved_ts", 86400, "24h_before_resolution")):
        j = snapshot(mr, p, ref, off)
        c = calib(j)
        c.to_csv(RES / f"01_calibration_{name}.csv")
        brier = float(np.mean((j.p_yes - j.yes_won) ** 2))
        out.append(f"### Calibration: p_yes {name} (regular markets, n={len(j)}, Brier={brier:.4f})\n" + md(c))
        if name == "7d_before_endDate":
            for sp in ("train", "test"):
                cs = calib(j[j.split == sp])
                cs.to_csv(RES / f"01_calibration_{name}_{sp}.csv")
                out.append(f"#### {name} — {sp} only (n={int((j.split == sp).sum())})\n" + md(cs[["n", "mean_p", "yes_rate", "se"]]))

    # shocks
    sh = s.copy()
    sh["clean"] = (sh.post_pinned == 0) & (sh.after_known == 0)
    shd = {"shocks_total": len(sh), "clean(not pinned, before known)": int(sh.clean.sum()),
           "markets_with_shock": int(sh.market_id.nunique()),
           "by_split": sh.groupby("split").size().to_dict(),
           "by_direction(clean)": sh[sh.clean].direction.value_counts().to_dict()}
    out.append("### Shocks\n```\n" + json.dumps(shd, indent=1) + "\n```")
    c = sh[sh.clean].copy()
    rows = {}
    for h in ("next_bar", "6h", "24h", "72h", "7d", "final"):
        col = "p_next_bar" if h == "next_bar" else f"p_{h}"
        x = c.direction * (c[col] - c.p_post)
        rows[h] = {"n": int(x.notna().sum()), "mean_continuation": x.mean(), "median": x.median(),
                   "share_continue>0": (x > 0).mean(), "share_revert<0": (x < 0).mean()}
    cont = pd.DataFrame(rows).T
    cont.to_csv(RES / "01_shock_continuation.csv")
    out.append("### Shock follow-through (clean shocks): direction*(p_h - p_post), mid-price, NO costs\n" + md(cont))
    c2 = c[(c.spike_revert_next_bar == 0) & (c.volume_usd >= 250_000) & c.p_next_bar.notna()]
    rows = {}
    for h in ("6h", "24h", "72h", "7d", "final"):
        x = c2.direction * (c2[f"p_{h}"] - c2.p_next_bar)
        rows[h] = {"n": int(x.notna().sum()), "mean_continuation": x.mean(), "median": x.median(),
                   "share_continue>0": (x > 0).mean(), "share_revert<0": (x < 0).mean()}
    cont2 = pd.DataFrame(rows).T
    cont2.to_csv(RES / "01_shock_continuation_filtered.csv")
    out.append("### Shock follow-through, filtered (clean, not a 1-bar spike, vol>=$250k), measured from NEXT bar "
               "(realistic entry), mid-price, NO costs\n" + md(cont2))
    bycat = c.assign(cont24=c.direction * (c.p_24h - c.p_post), contF=c.direction * (c.p_final - c.p_post)).groupby(
        "category").agg(n=("market_id", "size"), mean_abs_dp=("dp", lambda x: x.abs().mean()),
                        cont_24h=("cont24", "mean"), cont_final=("contF", "mean"))
    out.append("### Shock follow-through by category\n" + md(bycat))
    txt = "\n\n".join(out)
    (RES / "01_data_quality_report.md").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
