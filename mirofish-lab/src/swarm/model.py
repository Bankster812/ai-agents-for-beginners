"""Deterministic, LLM-free, vectorised "God View" swarm (05 Swarm Modeler).

Structure follows the MECHANICS SPEC in knowledge/02_mirofish_analyst.md (S1-S9).  Every array has a leading
batch axis B = (#shocks x #replicas), so one call simulates many independent worlds with numpy.

Per round (1 round = 60 sim-minutes, spec S2):
  1. activation           exact MiroFish rule S3 (target = floor(U(amin,amax)*m(hour)); candidates = in active
                          hours & U<activity_level); activity_level is boosted by attention to price moves/posts
                          (mission extension, flagged).
  2. feed / exposure      Twitter-world feed S5: top-2 posts by rec score (similarity x recency) + up to 3 posts
                          of followees ranked by likes, plus the market price as one extra item (extension).
  3. belief update        bounded confidence S8 with memory decay S7, persona anchor s_i, LLM-agreeableness
                          drift delta (herding knob).
  4. social action        S6 propensities: post / like / repost / quote / follow / nothing.
  5. order flow           retail+leaders: tanh(gamma_ls*L_i + rho*momentum - L_price)  (herding, recency,
                          longshot preference);  informed: c_inst*tanh(f_i - L_price), inventory-capped.
  6. price impact         Kyle-style: dL = lam/(0.5+act) * net_flow + nu*sigma_noise*eps*Bernoulli(act).

Scenario injection (spec S4, generalised): at round 0 all opinion leaders, a fraction phi of retail and the
informed agents receive an exogenous belief shock (the observed initial move), and leaders publish the initial
posts.  Opinions live in x = 2p-1 in [-1,1] (spec scale; eps=0.6 <=> |p_i-p_j|<0.3); orders/price in logit.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

F = np.float32
RETAIL, LEADER, INST = 0, 1, 2

# calibrated parameter vector (<= 6): name -> (default, lower, upper)
PARAM_SPACE = {
    "kappa": (0.6, 0.0, 1.0),     # share of the shock the informed agents treat as information (fundamental anchor)
    "c_inst": (1.5, 0.0, 6.0),    # informed capacity per activation (fade strength)
    "lam": (0.05, 0.002, 0.4),    # Kyle price impact per unit of net order flow (logit units)
    "rho": (0.5, 0.0, 3.0),       # retail recency / momentum chasing (weight on 6h logit move)
    "delta": (0.05, 0.0, 0.5),    # LLM-agreeableness / herding drift toward the feed mean
    "nu": (0.7, 0.05, 2.0),       # noise-trader volatility scale (x pre-shock realised vol)
}


@dataclass
class SwarmConfig:
    n_agents: int = 56
    share_leader: float = 8 / 56
    share_inst: float = 8 / 56
    rounds: int = 72
    amin: float = 5.0                 # agents_per_hour_min (LLM-example time config, spec S2)
    amax: float = 50.0
    peak_hours: tuple = (19, 20, 21, 22)
    off_hours: tuple = (0, 1, 2, 3, 4, 5)
    peak_mult: float = 1.5
    off_mult: float = 0.05
    n_slots: int = 16                 # post buffer per world (lowest keep-score post is overwritten)
    n_init_posts: int = 5             # round-0 scenario posts (spec S4: 3-8)
    n_pre_posts: int = 5              # pre-shock discourse posts at the pre-shock consensus
    mu: float = 0.3                   # S8
    eps: float = 0.6                  # S8 bounded confidence (x-space)
    beta_follow: float = 1.0          # S8
    gamma_like: float = 0.5           # S6 herding in target choice
    sigma_post: float = 0.1           # S6
    mem_decay: float = 0.95           # S7 (1.0 = faithful no-decay; 0.95 = mild LLM recency)
    stub: tuple = (0.3, 0.5, 0.8)     # S1 persona anchor s_i (retail, leader, inst)
    phi_exposed: float = 0.3          # share of retail directly exposed to the scenario at round 0
    sigma0: float = 0.3               # initial belief dispersion (logit)
    sigma_f: float = 0.15             # informed fundamental-estimate noise (logit)
    gamma_ls: float = 0.9             # retail longshot preference: logit shrink (TK weighting => w(p)/w(1-p))
    attn_price: float = 1.0           # attention boost per unit |6h logit move|
    attn_posts: float = 0.02          # attention boost per new post last round
    inst_cap: float = 4.0             # informed inventory cap (in units of c_inst)
    init_follow_leader: float = 0.6   # P(retail follows a leader) at t=0 (mission: leaders have high follower count;
                                      #  faithful MiroFish starts with an empty graph -> set 0.0)
    init_follow_other: float = 0.03
    # action propensities (Twitter world, spec S6): post, like, repost, quote, follow, nothing
    props: tuple = (0.25, 0.35, 0.15, 0.05, 0.10, 0.10)
    tz_offset: int = -5               # agents' active hours are US-Eastern local time

    def to_dict(self):
        return asdict(self)


def default_params():
    return {k: v[0] for k, v in PARAM_SPACE.items()}


def _logit(p):
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def _sig(L):
    return 1.0 / (1.0 + np.exp(-L))


def make_population(cfg: SwarmConfig, rng: np.random.Generator):
    """Fixed agent roster (shared by all worlds in a batch, like MiroFish's one-profile-per-entity)."""
    N = cfg.n_agents
    n_l = int(round(N * cfg.share_leader)); n_i = int(round(N * cfg.share_inst)); n_r = N - n_l - n_i
    typ = np.array([RETAIL] * n_r + [LEADER] * n_l + [INST] * n_i)
    act = np.where(typ == RETAIL, rng.uniform(0.6, 0.9, N), np.where(typ == LEADER, rng.uniform(0.4, 0.6, N),
                                                                      rng.uniform(0.1, 0.3, N)))
    hours = np.zeros((N, 24), bool)
    for i in range(N):
        if typ[i] == RETAIL:
            hours[i, 18:24] = True
            if rng.random() < 0.5:
                hours[i, 9:14] = True
        elif typ[i] == LEADER:
            hours[i, 8:24] = True
        else:
            hours[i, 9:18] = True
    infl = np.where(typ == RETAIL, rng.uniform(0.8, 1.2, N), np.where(typ == LEADER, rng.uniform(2.0, 2.5, N),
                                                                       rng.uniform(2.5, 3.0, N)))
    stub = np.array(cfg.stub)[typ]
    props = np.tile(np.array(cfg.props, float), (N, 1))
    props[typ == INST, 0] *= 2.0; props[typ == INST, 1] *= 0.3
    props /= props.sum(1, keepdims=True)
    return dict(typ=typ, act=act.astype(F), hours=hours, infl=infl.astype(F), stub=stub.astype(F),
                cprops=np.cumsum(props, 1).astype(F))


def simulate(inp: dict, params: dict, cfg: SwarmConfig = None, seed: int = 0, pop_seed: int = 12345,
             record: int | None = None):
    """Simulate B independent worlds.

    inp: dict of 1-D arrays, length B: p_pre, p_post, lag_h, hour (US-Eastern hour of the shock bar),
         act (share of bars with a price change, 0..1), vol (pre-shock hourly price-change std, prob units).
    Returns dict(price=(B, rounds+1) float32 probabilities, price[:,0]=p_post; mean_x, consensus...).
    If record is an int b, also returns a per-round 'trace' list of agent states of world b (God View dump).
    """
    cfg = cfg or SwarmConfig()
    P = {**default_params(), **(params or {})}
    rng = np.random.default_rng(seed)
    pop = make_population(cfg, np.random.default_rng(pop_seed))
    typ, N, K, T = pop["typ"], cfg.n_agents, cfg.n_slots, cfg.rounds
    is_r = (typ == RETAIL); is_l = (typ == LEADER); is_i = (typ == INST)
    B = len(inp["p_pre"])
    ar = np.arange(B)[:, None]

    Lpre = _logit(np.asarray(inp["p_pre"], F)).astype(F)
    Lpost = _logit(np.asarray(inp["p_post"], F)).astype(F)
    D = (Lpost - Lpre).astype(F)
    lag = np.clip(np.asarray(inp["lag_h"], F), 1, 6)
    hour0 = np.asarray(inp["hour"]).astype(int)
    actm = np.clip(np.asarray(inp["act"], F), 0.02, 1.0)
    pp = np.clip(np.asarray(inp["p_post"], F), 0.03, 0.97)
    sig_n = (np.clip(np.asarray(inp["vol"], F), 0.002, 0.15) / (pp * (1 - pp)) / np.sqrt(actm)).astype(F)
    lam_eff = (P["lam"] / (0.5 + actm)).astype(F)

    # ---------- initial state (pre-shock consensus + scenario injection) ----------
    L = (Lpre[:, None] + cfg.sigma0 * rng.standard_normal((B, N))).astype(F)
    exposed = np.zeros((B, N), bool)
    exposed[:, is_l] = True
    exposed[:, is_r] = rng.random((B, is_r.sum())) < cfg.phi_exposed
    L = np.where(exposed, L + D[:, None], L)
    f_inst = (Lpre[:, None] + P["kappa"] * D[:, None] + cfg.sigma_f * rng.standard_normal((B, N))).astype(F)
    L = np.where(is_i[None, :], f_inst, L)
    x = np.tanh(L / 2).astype(F)                         # opinion in [-1,1] (= 2p-1)
    x0 = x.copy()                                        # persona anchor after the round-0 injection
    mem_s = np.zeros((B, N), F); mem_w = np.zeros((B, N), F)
    inv = np.zeros((B, N), F)                            # informed inventory

    follow = np.zeros((B, N, N), bool)
    fl = rng.random((B, N, N))
    follow[:, :, is_l] = fl[:, :, is_l] < cfg.init_follow_leader
    follow[:, :, ~is_l] = fl[:, :, ~is_l] < cfg.init_follow_other
    follow[:, np.arange(N), np.arange(N)] = False
    del fl

    # post buffer
    px = np.zeros((B, K), F); pauth = np.zeros((B, K), np.int64); plikes = np.zeros((B, K), F)
    pt = np.full((B, K), -999.0, F); pvalid = np.zeros((B, K), bool)
    ridx = np.flatnonzero(is_r); lidx = np.flatnonzero(is_l)
    xpre = np.tanh(Lpre / 2)
    for k in range(cfg.n_pre_posts):                     # pre-shock discourse
        a = ridx[rng.integers(0, len(ridx), B)]
        px[:, k] = np.clip(xpre + cfg.sigma_post * rng.standard_normal(B), -0.99, 0.99)
        pauth[:, k] = a; pt[:, k] = -12.0; pvalid[:, k] = True; plikes[:, k] = rng.integers(0, 4, B)
    for j in range(cfg.n_init_posts):                    # round-0 scenario posts by opinion leaders (cycled)
        k = cfg.n_pre_posts + j
        a = lidx[j % len(lidx)]
        px[:, k] = np.clip(x[:, a] + cfg.sigma_post * rng.standard_normal(B), -0.99, 0.99)
        pauth[:, k] = a; pt[:, k] = 0.0; pvalid[:, k] = True
        plikes[:, k] = np.round(pop["infl"][a] * 2)

    # price history for the momentum term: flat at Lpre, linear ramp over the lag window to Lpost
    H = 6
    hist = np.zeros((B, H + 1), F)                       # hist[:, -1] = current, hist[:, 0] = 6h ago
    for j in range(H + 1):
        tau = (H - j)                                    # hours before t=0
        w = np.clip(1 - tau / lag, 0, 1)
        hist[:, j] = Lpre + w * D
    Lp = Lpost.copy()
    out = np.zeros((B, T + 1), F); out[:, 0] = _sig(Lp)
    mean_x = np.zeros((B, T + 1), F); mean_x[:, 0] = x.mean(1)
    n_new_posts = np.full(B, cfg.n_init_posts, F)

    hours_mask = pop["hours"]; act_lvl = pop["act"]; stub = pop["stub"]; cprops = pop["cprops"]
    infl = pop["infl"]
    trace = [] if record is not None else None
    lg = np.log(np.maximum(271.8 - np.arange(0, 400), 1e-3) / 100.0).astype(F)

    for t in range(1, T + 1):
        hr = (hour0 + t) % 24                            # local hour of this round (B,)
        mult = np.where(np.isin(hr, cfg.peak_hours), cfg.peak_mult,
                        np.where(np.isin(hr, cfg.off_hours), cfg.off_mult, 1.0)).astype(F)
        # ---- 1. activation (spec S3) with attention boost ----
        mom = hist[:, -1] - hist[:, 0]
        attn = 1 + cfg.attn_price * np.minimum(np.abs(mom), 2.0) + cfg.attn_posts * n_new_posts
        lvl = np.minimum(1.0, act_lvl[None, :] * np.where(is_i, 1.0, attn[:, None]))
        cand = hours_mask[:, hr].T & (rng.random((B, N)) < lvl)
        target = np.floor(rng.uniform(cfg.amin, cfg.amax, B) * mult).astype(int)
        pri = np.where(cand, rng.random((B, N)), 2.0)    # random priority = sampling w/o replacement
        kth = np.clip(target, 0, N - 1)
        thr = np.take_along_axis(np.sort(pri, 1), kth[:, None], 1)[:, 0]
        active = cand & ((pri < thr[:, None]) | (target[:, None] >= N))
        active &= (target[:, None] > 0)

        # ---- compaction: only active agents read feeds / act (A = max #active in the batch this round) ----
        A = int(active.sum(1).max())
        a_full = np.full((B, N), -1, np.int64)
        n_new_posts = np.zeros(B, F)
        if A > 0:
            sel = np.argsort(~active, axis=1, kind="stable")[:, :A]          # (B,A) agent ids, active first
            am = np.take_along_axis(active, sel, 1)                         # (B,A) valid
            xa = np.take_along_axis(x, sel, 1)
            # ---- 2. feed (spec S5) ----
            age = np.clip(t - pt, 0, 399).astype(np.int64)
            own = pauth[:, None, :] == sel[:, :, None]                      # (B,A,K)
            rec = (1 - np.abs(xa[:, :, None] - px[:, None, :]) * 0.5
                   + 0.3 * rng.random((B, A, K), dtype=F)) * lg[age][:, None, :]
            rec[own | ~pvalid[:, None, :]] = -np.inf
            i1 = rec.argmax(2)
            np.put_along_axis(rec, i1[:, :, None], -np.inf, 2)
            i2 = rec.argmax(2)
            v2 = np.take_along_axis(rec, i2[:, :, None], 2)[:, :, 0] > -np.inf
            v1 = (pvalid & True).sum(1)[:, None] > 0
            fidx = (ar * (N * N) + sel * N)[:, :, None] + pauth[:, None, :]  # (B,A,K) flat index into follow
            fol = np.take(follow.reshape(-1), fidx) & pvalid[:, None, :] & ~own
            fs = np.where(fol, plikes[:, None, :] + 0.1 * rng.random((B, A, K), dtype=F), -np.inf)
            j1 = fs.argmax(2); np.put_along_axis(fs, j1[:, :, None], -np.inf, 2)
            j2 = fs.argmax(2); np.put_along_axis(fs, j2[:, :, None], -np.inf, 2)
            j3 = fs.argmax(2)
            items = np.stack([i1, i2, j1, j2, j3], 2)                       # (B,A,5) slot ids
            fsel = np.stack([fol.any(2), None if False else (fol.sum(2) >= 2), fol.sum(2) >= 3], 2)
            nown = (pvalid[:, None, :] & ~own).sum(2)
            ivalid = np.concatenate([(nown >= 1)[:, :, None], (nown >= 2)[:, :, None], fsel], 2) & am[:, :, None]
            flat = ar[:, :, None] * K + items
            ix = np.take(px.reshape(-1), flat); il = np.take(plikes.reshape(-1), flat)
            ia = np.take(pauth.reshape(-1), flat)
            ifol = np.take(follow.reshape(-1), (ar * (N * N) + sel * N)[:, :, None] + ia)
            w = (1 + np.log1p(il) + cfg.beta_follow * ifol) * ivalid
            xp = np.tanh(Lp / 2)                                            # market price = extra feed item
            # ---- 3. belief update (S7 memory + S8 bounded confidence + herding drift) ----
            acc = (np.abs(ix - xa[:, :, None]) < cfg.eps) * w
            acc_p = (np.abs(xp[:, None] - xa) < cfg.eps).astype(F)
            ms = np.take_along_axis(mem_s, sel, 1) + (acc * ix).sum(2) + acc_p * xp[:, None]
            mw = np.take_along_axis(mem_w, sel, 1) + acc.sum(2) + acc_p
            tgt = np.where(mw > 0, ms / np.maximum(mw, 1e-6), xa)
            fmean = ((w * ix).sum(2) + xp[:, None]) / (w.sum(2) + 1)
            sa = stub[sel]
            xn = xa + cfg.mu * (tgt - xa) + P["delta"] * (fmean - xa)
            xn = (1 - sa) * xn + sa * np.take_along_axis(x0, sel, 1)
            upd = am & ~is_i[sel]
            xa_new = np.where(upd, np.clip(xn, -0.995, 0.995), xa).astype(F)
            np.put_along_axis(x, sel, np.where(am, xa_new, np.take_along_axis(x, sel, 1)), 1)
            np.put_along_axis(mem_s, sel, np.where(am, ms, np.take_along_axis(mem_s, sel, 1)), 1)
            np.put_along_axis(mem_w, sel, np.where(am, mw, np.take_along_axis(mem_w, sel, 1)), 1)
            xa = xa_new
            # ---- 4. social action (S6) ----
            u = rng.random((B, A), dtype=F)
            a_idx = (u[:, :, None] > cprops[sel]).sum(2)                    # 0 post 1 like 2 repost 3 quote 4 follow 5 nothing
            sc = np.where(ivalid, -np.abs(ix - xa[:, :, None]) + cfg.gamma_like * np.log1p(il), -np.inf)
            best = np.argmax(sc, 2); has = ivalid.any(2)
            bslot = np.take_along_axis(items, best[:, :, None], 2)[:, :, 0]
            bauth = np.take_along_axis(ia, best[:, :, None], 2)[:, :, 0]
            bx = np.take_along_axis(ix, best[:, :, None], 2)[:, :, 0]
            BB = np.broadcast_to(ar, (B, A))
            like_m = am & has & ((a_idx == 1) | (a_idx == 2))
            if like_m.any():
                np.add.at(plikes, (BB[like_m], bslot[like_m]), 1.0)
            rp = am & has & (a_idx == 2)
            if rp.any():
                pt[BB[rp], bslot[rp]] = t                                   # repost refreshes visibility
            fm = am & has & (a_idx == 4)
            if fm.any():
                follow[BB[fm], sel[fm], bauth[fm]] = True
            post_m = am & ((a_idx == 0) | ((a_idx == 3) & has))
            newx = np.where(a_idx == 3, 0.5 * (xa + bx), xa)
            newx = np.clip(newx + cfg.sigma_post * rng.standard_normal((B, A), dtype=F), -0.99, 0.99)
            keep = np.where(pvalid, np.log1p(plikes) - 0.05 * (t - pt), -np.inf)
            order = np.argsort(keep, 1)
            rank = np.cumsum(post_m, 1) - 1
            okp = post_m & (rank < K)
            n_new_posts = okp.sum(1).astype(F)
            if okp.any():
                bb = BB[okp]
                slot = order[bb, rank[okp]]
                px[bb, slot] = newx[okp]; pauth[bb, slot] = sel[okp]
                plikes[bb, slot] = 0; pt[bb, slot] = t; pvalid[bb, slot] = True
            if trace is not None:
                np.put_along_axis(a_full, sel, np.where(am, a_idx, -1), 1)
        mem_s *= cfg.mem_decay; mem_w *= cfg.mem_decay

        # ---- 5. order flow ----
        Li = np.log((1 + x) / (1 - x))
        Leff = cfg.gamma_ls * Li + P["rho"] * mom[:, None]
        Leff = np.where(is_l[None, :], Li + 0.5 * P["rho"] * mom[:, None], Leff)
        d = np.tanh(Leff - Lp[:, None])
        di = P["c_inst"] * np.tanh(f_inst - Lp[:, None])
        cap = cfg.inst_cap * P["c_inst"]
        di = np.clip(di, -cap - inv, cap - inv)                         # capacity-limited
        d = np.where(is_i[None, :], di, d) * active
        inv = inv + np.where(is_i[None, :], d, 0)
        flow = d.sum(1)

        # ---- 6. price impact ----
        trade = rng.random(B) < actm
        Lp = Lp + lam_eff * flow + P["nu"] * sig_n * rng.standard_normal(B, dtype=F) * trade
        Lp = np.clip(Lp, -6.9, 6.9).astype(F)
        hist = np.concatenate([hist[:, 1:], Lp[:, None]], 1)
        out[:, t] = _sig(Lp)
        mean_x[:, t] = x.mean(1)

        if trace is not None:
            b = record
            trace.append(dict(
                round=t, hour_et=int(hr[b]), price=float(out[b, t]), net_flow=float(flow[b]),
                n_active=int(active[b].sum()), n_posts_buffer=int(pvalid[b].sum()),
                agents=[dict(id=int(i), type=["retail", "leader", "informed"][typ[i]], active=bool(active[b, i]),
                             belief_p=round(float((x[b, i] + 1) / 2), 4), anchor_p=round(float((x0[b, i] + 1) / 2), 4),
                             action=(["post", "like", "repost", "quote", "follow", "nothing"][a_full[b, i]]
                                     if a_full[b, i] >= 0 else "inactive"),
                             order=round(float(d[b, i]), 4), followers=int(follow[b, :, i].sum()),
                             exposed=bool(exposed[b, i])) for i in range(N)],
                top_posts=[dict(slot=int(k), author=int(pauth[b, k]), stance_p=round(float((px[b, k] + 1) / 2), 4),
                                likes=int(plikes[b, k]), created_round=int(pt[b, k]))
                           for k in np.argsort(-plikes[b])[:5] if pvalid[b, k]]))

    res = dict(price=out, mean_x=mean_x)
    if trace is not None:
        res["trace"] = trace
        res["population"] = dict(typ=typ.tolist(), activity=pop["act"].tolist(), influence=infl.tolist())
    return res


def tick_round(p):
    """Displayed-price grid: 0.005 in the body, 0.001 in the tails (matches the data: 68% / 81% on these grids)."""
    p = np.asarray(p)
    body = np.round(p * 200) / 200
    tail = np.round(p * 1000) / 1000
    return np.where((p < 0.05) | (p > 0.95), tail, body)
