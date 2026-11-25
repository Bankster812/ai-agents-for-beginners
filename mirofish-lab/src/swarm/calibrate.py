"""Method of simulated moments on TRAIN shocks only (05).

Moments (by p_pre bucket x horizon), all measured from the NEXT bar (tradeable entry), direction-adjusted:
  for h in {6h, 24h, 72h}: mean, P(cont>0), P(cont<0), q10, q90       -> 5 buckets x 3 x 5 = 75
  plus mean next-bar bounce dir*(p_next_bar - p_post) per bucket       -> 5
Weights: diagonal, 1/se^2 with se from an event_id-clustered bootstrap (200 reps) of the data moments.
Simulated moments use the same shocks (1,200 TRAIN shocks x 4 replicas, common random numbers) and the same
NaN mask as the data.  Optimiser: 24-point Latin-hypercube screen + Nelder-Mead in logistic-transformed space.
Usage: python3 -m swarm.calibrate   (from src/)
"""
from __future__ import annotations

import json
import sys
import time
from multiprocessing import get_context
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swarm.features import build_features, ROOT  # noqa: E402
from swarm.model import PARAM_SPACE, default_params  # noqa: E402
from swarm.runner import shock_inputs, run_batch  # noqa: E402

BINS = np.array([0, 0.2, 0.4, 0.6, 0.8, 1.0001])
HZ = (6, 24, 72)
NAMES = list(PARAM_SPACE)
LO = np.array([PARAM_SPACE[k][1] for k in NAMES]); HI = np.array([PARAM_SPACE[k][2] for k in NAMES])


def moments(cont: dict, bounce, bucket, w=None):
    """cont: {h: array (n,) with NaN}, bounce: (n,), bucket: (n,) int. Returns vector of 80 moments."""
    out = []
    for b in range(5):
        m = bucket == b
        for h in HZ:
            c = cont[h][m]; c = c[~np.isnan(c)]
            out += [c.mean(), (c > 0).mean(), (c < 0).mean(), np.quantile(c, 0.1), np.quantile(c, 0.9)]
        out.append(np.nanmean(bounce[m]))
    return np.array(out)


def moment_labels():
    lab = []
    for b in range(5):
        for h in HZ:
            lab += [f"b{b}_{h}h_{s}" for s in ("mean", "pos", "neg", "q10", "q90")]
        lab.append(f"b{b}_bounce")
    return lab


def sim_moments(price, df, reps):
    d = np.repeat(df.direction.to_numpy(), reps)
    cont = {}
    for h in HZ:
        c = d * (price[:, h] - price[:, 1])
        c[np.repeat(df[f"cont_{h}h"].isna().to_numpy(), reps)] = np.nan
        cont[h] = c
    bounce = d * (price[:, 1] - price[:, 0])
    bucket = np.repeat(np.digitize(df.p_pre, BINS) - 1, reps)
    return moments(cont, bounce, bucket)


def data_moments(df):
    cont = {h: df[f"cont_{h}h"].to_numpy() for h in HZ}
    return moments(cont, df.cont_nb_from_post.to_numpy(), np.digitize(df.p_pre, BINS) - 1)


def theta_from_u(u):
    return LO + (HI - LO) / (1 + np.exp(-u))


def u_from_theta(th):
    z = np.clip((np.asarray(th) - LO) / (HI - LO), 1e-4, 1 - 1e-4)
    return np.log(z / (1 - z))


def nelder_mead(f, x0, step=0.6, iters=60, tol=1e-3):
    n = len(x0); pts = [x0] + [x0 + step * np.eye(n)[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for it in range(iters):
        o = np.argsort(vals); pts = [pts[i] for i in o]; vals = [vals[i] for i in o]
        if abs(vals[-1] - vals[0]) < tol * (1 + abs(vals[0])):
            break
        c = np.mean(pts[:-1], 0)
        xr = c + (c - pts[-1]); fr = f(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1]); fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c); fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    pts[i] = pts[0] + 0.5 * (pts[i] - pts[0]); vals[i] = f(pts[i])
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def main(n_cal=1200, reps=4, seed=7):
    t0 = time.time()
    f = build_features()
    tr = f[f.split5 == "train"].reset_index(drop=True)
    rng = np.random.default_rng(seed)
    cal = tr.iloc[np.sort(rng.choice(len(tr), n_cal, replace=False))].reset_index(drop=True)
    m_data = data_moments(tr)
    # event-clustered bootstrap se on the FULL train set
    ev = tr.event_id.to_numpy(); uev = np.unique(ev); gi = {e: np.flatnonzero(ev == e) for e in uev}
    boots = []
    for _ in range(200):
        pick = rng.choice(uev, len(uev)); idx = np.concatenate([gi[e] for e in pick])
        boots.append(data_moments(tr.iloc[idx]))
    se = np.std(boots, 0)
    # the calibration subsample is smaller than full train: add its sampling noise (sim side) to the weights
    se_tot = np.sqrt(se ** 2 * (1 + len(tr) / (n_cal * reps)))
    W = 1 / np.maximum(se_tot, 1e-3) ** 2
    inp = shock_inputs(cal, reps)
    pool = get_context("fork").Pool(4)
    hist = []

    def J(u):
        th = theta_from_u(u); p = dict(zip(NAMES, th))
        price, _ = run_batch(inp, p, rounds=72, seed=seed, chunk=1200, pool=pool)
        ms = sim_moments(price, cal, reps)
        j = float(np.sum(W * (ms - m_data) ** 2))
        hist.append(dict(J=j, **{k: float(v) for k, v in p.items()}))
        print(f"[{time.time()-t0:6.0f}s] J={j:9.1f} " + " ".join(f"{k}={v:.3f}" for k, v in p.items()), flush=True)
        return j

    # null model (no behavioural agents: only noise) and default, for reference
    j_def = J(u_from_theta([default_params()[k] for k in NAMES]))
    lhs = (rng.permuted(np.tile(np.arange(24), (6, 1)), axis=1).T + rng.random((24, 6))) / 24
    cands = [u_from_theta(LO + (HI - LO) * z) for z in lhs]
    vals = [J(u) for u in cands]
    order = np.argsort(vals)
    u0 = cands[order[0]] if vals[order[0]] < j_def else u_from_theta([default_params()[k] for k in NAMES])
    ub, jb = nelder_mead(J, u0, iters=45)
    th = dict(zip(NAMES, map(float, theta_from_u(ub))))
    price, _ = run_batch(inp, th, rounds=72, seed=seed + 1, chunk=1200, pool=pool)   # fresh seed = honest re-check
    ms = sim_moments(price, cal, reps)
    j_fresh = float(np.sum(W * (ms - m_data) ** 2))
    null = dict(th); null.update(c_inst=0.0, rho=0.0, delta=0.0, lam=0.002)
    price0, _ = run_batch(inp, null, rounds=72, seed=seed, chunk=1200, pool=pool)
    m0 = sim_moments(price0, cal, reps); j_null = float(np.sum(W * (m0 - m_data) ** 2))
    pool.close()
    lab = moment_labels()
    tbl = [dict(moment=l, data=float(a), sim=float(b), se=float(s), z=float((b - a) / st))
           for l, a, b, s, st in zip(lab, m_data, ms, se, se_tot)]
    out = dict(params=th, J_best=jb, J_fresh_seed=j_fresh, J_default=j_def, J_null_noise_only=j_null,
               n_moments=len(lab), n_params=len(NAMES), n_train_shocks=len(tr), n_cal_shocks=n_cal, reps=reps,
               share_moments_within_2se=float(np.mean(np.abs([t["z"] for t in tbl]) < 2)),
               runtime_s=time.time() - t0, moments=tbl, history=hist)
    (ROOT / "results" / "05_calibration.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("moments", "history")}, indent=1))


if __name__ == "__main__":
    main()
