"""Performance and multiple-testing statistics for BacktestResult objects.

* summarize(result)            -> dict of headline metrics (trade-level + daily-equity-level)
* edge_tstat_clustered(trades) -> per-$ edge (sum pnl / sum cost) with event_id-clustered SE (ratio estimator)
* psr / deflated_sharpe        -> Bailey & Lopez de Prado (2012, 2014); SR in per-period (daily) units
* expected_max_sharpe          -> E[max SR] under the null for N trials with cross-trial SR variance V
* benjamini_hochberg, bonferroni
* white_reality_check          -> stationary-bootstrap RC / SPA-style p-value for the best of K return series
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

N01 = NormalDist()
EULER = 0.5772156649015329


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def sharpe(r: np.ndarray, rf_daily: float = 0.0, periods: int = 365) -> float:
    r = np.asarray(r, float) - rf_daily
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def edge_tstat_clustered(trades: pd.DataFrame, pnl_col="pnl", cost_col="entry_cost", cluster="event_id",
                         weight_col=None):
    """Per-$ edge e = sum(w*pnl)/sum(w*cost); SE by cluster-robust delta method; returns (e, se, t, G)."""
    if trades.empty:
        return float("nan"), float("nan"), float("nan"), 0
    w = trades[weight_col].to_numpy(float) if weight_col else np.ones(len(trades))
    g = trades.assign(_p=trades[pnl_col] * w, _c=trades[cost_col] * w).groupby(cluster)[["_p", "_c"]].sum()
    P, C = g["_p"].to_numpy(), g["_c"].to_numpy()
    e = P.sum() / C.sum()
    G = len(g)
    if G < 2:
        return float(e), float("nan"), float("nan"), G
    u = P - e * C
    var = G / (G - 1) * (u ** 2).sum() / C.sum() ** 2
    se = math.sqrt(var)
    return float(e), float(se), float(e / se) if se > 0 else float("nan"), G


def psr(sr: float, T: int, skew: float = 0.0, kurt: float = 3.0, sr0: float = 0.0) -> float:
    """Probabilistic Sharpe ratio; sr, sr0 in per-period units, kurt = raw (non-excess) kurtosis."""
    if T < 3 or not np.isfinite(sr):
        return float("nan")
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0:
        den = 1e-12
    return float(N01.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max of N iid N(0, var_sr) Sharpe estimates] (False Strategy Theorem)."""
    if n_trials <= 1:
        return 0.0
    a = N01.inv_cdf(1 - 1.0 / n_trials)
    b = N01.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(max(var_sr, 0.0)) * ((1 - EULER) * a + EULER * b))


def deflated_sharpe(r: np.ndarray, n_trials: int, var_sr_trials: float, rf_daily: float = 0.0) -> dict:
    """DSR of a daily excess-return series given N trials and the variance (across trials) of their daily SR."""
    r = np.asarray(r, float) - rf_daily
    T = len(r)
    if T < 3 or r.std(ddof=1) == 0:
        return dict(sr_daily=float("nan"), sr0_daily=float("nan"), dsr=float("nan"), psr0=float("nan"), T=T)
    sr = r.mean() / r.std(ddof=1)
    z = (r - r.mean()) / r.std(ddof=0)
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())
    sr0 = expected_max_sharpe(n_trials, var_sr_trials)
    return dict(sr_daily=float(sr), sr0_daily=sr0, dsr=psr(sr, T, skew, kurt, sr0), psr0=psr(sr, T, skew, kurt, 0.0),
                T=T, skew=skew, kurt=kurt)


def benjamini_hochberg(pvals, q=0.05):
    p = np.asarray(pvals, float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(adj, 1.0)
    return out, out <= q


def bonferroni(pvals, alpha=0.05):
    p = np.minimum(np.asarray(pvals, float) * len(pvals), 1.0)
    return p, p <= alpha


def _stationary_bootstrap_idx(T, mean_block, rng):
    idx = np.empty(T, dtype=np.int64)
    p = 1.0 / mean_block
    idx[0] = rng.integers(T)
    jumps = rng.random(T) < p
    starts = rng.integers(T, size=T)
    for t in range(1, T):
        idx[t] = starts[t] if jumps[t] else (idx[t - 1] + 1) % T
    return idx


def white_reality_check(R: np.ndarray, n_boot: int = 1000, mean_block: float = 10.0, seed: int = 0,
                        studentize: bool = True) -> dict:
    """R: T x K matrix of daily excess returns (vs benchmark 0) for K strategy variants.
    H0: no variant has positive expected return. Returns p-value for the best variant (White 2000 RC;
    with studentize=True and recentring only variants with non-negative mean -> Hansen-SPA flavour)."""
    R = np.asarray(R, float)
    T, K = R.shape
    mu = R.mean(0)
    sd = R.std(0, ddof=1)
    sd[sd == 0] = np.inf
    stat = np.max(np.sqrt(T) * mu / (sd if studentize else 1.0))
    rng = np.random.default_rng(seed)
    # SPA recentring: variants with very negative means are not recentred to zero
    thresh = -np.sqrt(2 * np.log(np.log(max(T, 3)))) * sd / np.sqrt(T)
    mu_c = np.where(mu >= thresh, mu, 0.0) if studentize else mu
    cnt = 0
    for _ in range(n_boot):
        ii = _stationary_bootstrap_idx(T, mean_block, rng)
        mb = R[ii].mean(0)
        sb = np.max(np.sqrt(T) * (mb - mu_c) / (sd if studentize else 1.0))
        cnt += sb >= stat
    return dict(stat=float(stat), p=float((cnt + 1) / (n_boot + 1)), best=int(np.argmax(mu / sd)), K=K, T=T)


def summarize(res, rf: float | None = None) -> dict:
    cfg = res.config
    rf = cfg.cash_yield if rf is None else rf
    rf_d = (1 + rf) ** (1 / 365) - 1
    t = res.trades
    eq = res.equity
    out = dict(label=cfg.label, cost_model=res.cost_model_name, bankroll=cfg.bankroll)
    n = len(t)
    out["n_trades"] = n
    out["n_events"] = int(t.event_id.nunique()) if n else 0
    out["n_skipped"] = len(res.skipped)
    if n:
        out["hit_rate"] = float((t.pnl > 0).mean())
        e, se, tt, G = edge_tstat_clustered(t)
        out.update(edge_per_usd=e, edge_se=se, edge_t=tt)
        eg, _, tg, _ = edge_tstat_clustered(t, pnl_col="gross_pnl")
        out.update(gross_edge_per_usd=eg, gross_edge_t=tg)
        ew, sew, tw, _ = edge_tstat_clustered(t, weight_col="sample_weight")
        out.update(edge_per_usd_popw=ew, edge_t_popw=tw)
        out["cost_share_of_gross"] = float(1 - e / eg) if eg and eg > 0 else float("nan")
        out["avg_hold_days"] = float((t.hold_days * t.entry_cost).sum() / t.entry_cost.sum())
        out["avg_entry_mid"] = float(t.entry_mid.mean())
        out["total_cost_usd"] = float(t.entry_cost.sum())
        out["net_pnl_usd"] = float(t.pnl.sum())
        out["gross_pnl_usd"] = float(t.gross_pnl.sum())
    if len(eq) > 1:
        years = (eq.index[-1] - eq.index[0]).days / 365.0
        r = daily_returns(eq).to_numpy()
        tot = eq.iloc[-1] / cfg.bankroll - 1
        out["total_return"] = float(tot)
        out["years"] = years
        out["cagr"] = float((eq.iloc[-1] / cfg.bankroll) ** (1 / years) - 1) if years >= 0.5 and eq.iloc[-1] > 0 else float("nan")
        out["cash_benchmark_return"] = float((1 + rf) ** years - 1)
        out["excess_vs_cash_usd"] = float(eq.iloc[-1] - cfg.bankroll * (1 + rf) ** years)
        out["max_dd"] = max_drawdown(eq)
        out["sharpe"] = sharpe(r, rf_d)
        out["sharpe_raw"] = sharpe(r, 0.0)
        out["psr0"] = deflated_sharpe(r, 1, 0.0, rf_d)["psr0"]
        if n:
            out["turnover_x_per_year"] = float(t.entry_cost.sum() / eq.mean() / max(years, 1e-9))
            # capital utilisation: average share of equity deployed (cost basis)
            day_ends = (eq.index + pd.Timedelta(days=1)).astype("int64") // 10 ** 9
            dep = np.zeros(len(eq))
            fi = np.searchsorted(day_ends, t.fill_ts.to_numpy(), side="right")
            ci = np.searchsorted(day_ends, t.close_ts.to_numpy(), side="right")
            for a, b, c in zip(fi, ci, t.entry_cost.to_numpy()):
                dep[a:b] += c
            out["avg_utilisation"] = float(np.mean(dep / eq.to_numpy()))
    return out
