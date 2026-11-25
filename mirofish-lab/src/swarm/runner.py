"""Parallel, seeded batch runner for the swarm (4 worker processes, fixed seeds per chunk)."""
from __future__ import annotations

import os
from multiprocessing import get_context

import numpy as np
import pandas as pd

from .model import SwarmConfig, simulate, tick_round

INP_COLS = dict(p_pre="p_pre", p_post="p_post", lag_h="lag_h", hour="hour_et", act="act_7d", vol="pre_vol_24h")


def shock_inputs(df: pd.DataFrame, reps: int) -> dict:
    """Repeat each shock `reps` times (replica-major within shock): row i*reps + r."""
    return {k: np.repeat(df[c].to_numpy(), reps) for k, c in INP_COLS.items()}


def _work(a):
    inp, params, seed, rounds = a
    cfg = SwarmConfig(rounds=rounds)
    r = simulate(inp, params, cfg, seed=seed)
    return tick_round(r["price"]).astype(np.float32), r["mean_x"].astype(np.float32)


def run_batch(inp: dict, params: dict, rounds: int = 72, seed: int = 2026, chunk: int = 2500,
              procs: int = None, pool=None):
    B = len(inp["p_pre"])
    starts = list(range(0, B, chunk))
    jobs = [({k: v[s:s + chunk] for k, v in inp.items()}, params, seed * 100003 + j, rounds)
            for j, s in enumerate(starts)]
    if pool is not None:
        res = pool.map(_work, jobs)
    elif (procs or os.cpu_count()) > 1 and len(jobs) > 1:
        with get_context("fork").Pool(procs or os.cpu_count()) as p:
            res = p.map(_work, jobs)
    else:
        res = [_work(j) for j in jobs]
    return np.concatenate([r[0] for r in res]), np.concatenate([r[1] for r in res])
