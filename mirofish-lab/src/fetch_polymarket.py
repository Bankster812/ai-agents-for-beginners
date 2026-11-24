#!/usr/bin/env python3
"""
fetch_polymarket.py — reproducible Polymarket dataset builder for the MiroFish lab.

Stages (run in order; every network response is cached under data/raw/, so reruns are free):

    python src/fetch_polymarket.py universe   # Gamma /markets/keyset  -> data/raw/gamma/markets_closed_v25k.jsonl.gz
    python src/fetch_polymarket.py sample     # filter + classify + stratified sample -> data/raw/sample_manifest.csv
    python src/fetch_polymarket.py prices     # CLOB /prices-history (fidelity=60, + fidelity=1 for short-auto)
    python src/fetch_polymarket.py build      # data/markets.csv.gz, data/prices_hourly.csv.gz, data/shocks.csv.gz, ...
    python src/fetch_polymarket.py all

API behaviour discovered (Nov 2026):
  * Gamma /markets: limit is capped at 100 and offset at ~2,000 ("offset too large, use /markets/keyset").
    /markets/keyset paginates with `after_cursor=<next_cursor>`; supports closed, volume_num_min,
    end_date_min/max and include_tag=true (adds `tags`).
  * CLOB /prices-history:
      - `interval=max` returns an EMPTY history for resolved markets -> useless for backtests.
      - `startTs`+`endTs` is rejected if the window is > ~14 days ("interval is too long").
      - `startTs` ALONE (no endTs) returns the whole remaining history at the requested fidelity
        in one call (verified: 7,357 hourly points for the 10-month Trump-2024 market; fidelity=1 works too).
      - timestamps are sample times (e.g. hh:00:02 or hh:20:02), not exact hour boundaries.
      - the first point is frequently a 0.5 placeholder (empty book at listing).
      - `p` is Polymarket's displayed price (midpoint, or last trade if spread > 0.10), NOT an executable quote.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
RESULTS = ROOT / "results"
GAMMA_FILE = RAW / "gamma" / "markets_closed_v25k.jsonl.gz"
MANIFEST = RAW / "sample_manifest.csv"
CLOB_DIR = RAW / "clob"

GAMMA = "https://gamma-api.polymarket.com/markets/keyset"
CLOB = "https://clob.polymarket.com/prices-history"

END_MIN = "2023-01-01T00:00:00Z"
END_MAX = "2026-11-20T23:59:59Z"
VOL_FETCH_MIN = 25_000      # universe fetched at $25k so the $50k rule can be relaxed without refetching
VOL_MIN = 50_000            # dataset rule
SEED = 42
N_MAIN = 3600               # target regular (non-short-auto) markets
N_SHORT = 400               # target short-auto markets (separate stratum)
CONCURRENCY = 4

# ---------------------------------------------------------------------------- http helpers
_tls = threading.local()


def _session() -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = "research-dataset-builder/1.0"
        _tls.s = s
    return s


def get_json(url: str, params: dict, tries: int = 7):
    for a in range(tries):
        try:
            r = _session().get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 404):
                return {"_error": r.status_code, "_text": r.text[:300]}
        except requests.RequestException:
            pass
        time.sleep(min(60, 1.5 * 2 ** a) + random.random())
    raise RuntimeError(f"failed after {tries} tries: {url} {params}")


# ---------------------------------------------------------------------------- stage 1: universe
def stage_universe():
    if GAMMA_FILE.exists():
        print(f"[universe] cached: {GAMMA_FILE}")
        return
    GAMMA_FILE.parent.mkdir(parents=True, exist_ok=True)
    params = dict(closed="true", limit=100, volume_num_min=VOL_FETCH_MIN,
                  end_date_min=END_MIN, end_date_max=END_MAX, include_tag="true")
    cur, n, pages = None, 0, 0
    tmp = GAMMA_FILE.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        while True:
            p = dict(params)
            if cur:
                p["after_cursor"] = cur
            d = get_json(GAMMA, p)
            ms = d.get("markets", [])
            for m in ms:
                f.write(json.dumps(m) + "\n")
            n += len(ms)
            pages += 1
            cur = d.get("next_cursor")
            if pages % 50 == 0:
                print(f"[universe] pages={pages} markets={n}", flush=True)
            if not ms or not cur:
                break
    tmp.rename(GAMMA_FILE)
    print(f"[universe] done: {n} markets")


# ---------------------------------------------------------------------------- classification
CAT_RULES = [
    # (category, tag labels (lowercase, exact), question/slug regex)
    ("sports", {"sports", "games", "soccer", "nba", "nfl", "mlb", "nhl", "ufc", "tennis", "golf", "f1",
                "formula 1", "esports", "basketball", "football", "baseball", "hockey", "cricket", "boxing",
                "epl", "premier league", "la liga", "ucl", "bundesliga", "serie a", "ligue 1", "mls", "ncaa",
                "cfb", "pga", "atp", "wta", "super bowl", "olympics", "chess", "mma", "nfl playoffs",
                "champions league", "fifa world cup", "world cup", "2026 winter games"},
     r"\b(vs\.?|nba|nfl|mlb|nhl|ufc|fifa|premier league|super bowl|grand prix|world series|stanley cup|"
     r"wimbledon|olympic|champions league|playoffs?|touchdown|quarterback)\b"),
    ("crypto", {"crypto", "bitcoin", "ethereum", "crypto prices", "solana", "xrp", "ripple", "memecoins",
                "airdrops", "dogecoin", "stablecoins", "microstrategy", "defi", "nft"},
     r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|doge(coin)?|crypto|token|airdrop|memecoin|fdv|binance|"
     r"coinbase|usdt|usdc|stablecoin)\b"),
    ("geopolitics", {"geopolitics", "ukraine", "russia", "israel", "iran", "middle east", "gaza", "china",
                     "u.s. x iran", "iran ceasefire", "israel x iran", "lebanon", "hezbollah", "yemen",
                     "military actions", "military strikes", "peace deal", "strait of hormuz", "hormuz",
                     "ukraine map", "foreign policy", "venezuela", "world affairs", "putin", "khamenei",
                     "ukraine & russia", "taiwan", "north korea", "nato"},
     r"\b(ukraine|russia|putin|zelensky|israel|gaza|hamas|hezbollah|iran|ceasefire|invade|invasion|"
     r"missile|airstrike|strike on|nato|taiwan|north korea|houthi|war)\b"),
    ("politics", {"politics", "elections", "us election", "trump presidency", "global elections",
                  "world elections", "u.s. politics", "us politics", "midterms", "senate", "congress",
                  "primaries", "election", "trump cabinet", "biden", "kamala", "polling", "nyc mayor",
                  "house elections", "main election", "deprec usa election", "gov shutdown",
                  "us-current-affairs", "global politics", "trump", "vance", "primary elections",
                  "canadian election", "epstein", "supreme court", "scotus"},
     r"\b(election|elected|president|presidential|senate|congress|governor|mayor|democrat|republican|"
     r"gop|trump|biden|harris|kamala|vance|prime minister|parliament|cabinet|impeach|primary|nominee|"
     r"poll|electoral|supreme court)\b"),
    ("macro", {"economy", "finance", "business", "fed rates", "fed", "inflation", "economic policy",
               "macro indicators", "commodities", "oil", "gold", "equities", "stocks", "global rates",
               "trade war", "jerome powell", "pre-market", "finance updown", "tariffs", "gdp",
               "recession", "interest rates", "earnings", "ipos", "cpi", "jobs report", "s&p 500"},
     r"\b(fed|fomc|interest rate|rate cut|rate hike|bps|inflation|cpi|gdp|recession|unemployment|jobs report|"
     r"nonfarm|tariff|s&p|nasdaq|dow|stock|shares|earnings|ipo|oil|gold|treasury|yield|powell)\b"),
    ("culture", {"culture", "pop culture", "pop-culture", "movies", "music", "awards", "oscars", "celebrities",
                 "youtube", "mrbeast", "tweet markets", "elon tweets", "twitter", "mentions", "box office",
                 "eurovision", "grammys", "tv", "netflix", "spotify", "taylor swift", "best of 2025", "kanye"},
     r"\b(oscar|grammy|emmy|movie|film|album|song|spotify|netflix|box office|tweet|tweets|youtube|mrbeast|"
     r"taylor swift|kanye|drake|celebrity|eurovision|time person of the year|say .+ during)\b"),
]


def classify(m: dict) -> str:
    tags = {(t.get("label") or "").strip().lower() for t in (m.get("tags") or [])}
    cat_field = (m.get("category") or "").strip().lower()
    if cat_field:
        tags.add(cat_field)
    for ev in m.get("events") or []:
        for t in ev.get("tags") or []:
            tags.add((t.get("label") or "").strip().lower())
    for cat, tagset, _ in CAT_RULES:
        if tags & tagset:
            return cat
    text = f"{m.get('question') or ''} {m.get('slug') or ''}".lower().replace("-", " ")
    for cat, _, rx in CAT_RULES:
        if re.search(rx, text):
            return cat
    return "other"


def _ts(s) -> float:
    if not s:
        return np.nan
    s = str(s).strip()
    if s.endswith("+00"):
        s += ":00"
    try:
        return pd.Timestamp(s).timestamp()  # naive strings are treated as UTC
    except Exception:
        return np.nan


def parse_market(m: dict) -> dict:
    out = {}
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
    except Exception:
        outcomes = []
    try:
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except Exception:
        prices = []
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
    except Exception:
        toks = []
    tags = [(t.get("label") or "").strip() for t in (m.get("tags") or [])]
    tagl = {t.lower() for t in tags}
    q = m.get("question") or ""
    slug = m.get("slug") or ""
    kind = "yesno" if outcomes == ["Yes", "No"] else ("updown" if outcomes == ["Up", "Down"] else "other")
    start = _ts(m.get("startDate"))
    created = _ts(m.get("createdAt"))
    end = _ts(m.get("endDate"))
    closed = _ts(m.get("closedTime"))
    uma_end = _ts(m.get("umaEndDate"))
    resolved = closed if not np.isnan(closed) else uma_end
    st = np.nanmin([start, created]) if not (np.isnan(start) and np.isnan(created)) else np.nan
    is_recurring = "recurring" in tagl
    is_updown = kind == "updown" or bool(re.search(r"up or down|updown", (q + " " + slug).lower()))
    dur_h = (end - (start if not np.isnan(start) else created)) / 3600 if not np.isnan(end) else np.nan
    is_short_auto = bool(is_updown or (is_recurring and not np.isnan(dur_h) and dur_h <= 48))
    # resolution
    status = m.get("umaResolutionStatus")
    if len(prices) == 2 and max(prices) >= 0.99 and min(prices) <= 0.01:
        yes_won = 1 if prices[0] > prices[1] else 0
        res_flag = "ok"
    elif len(prices) == 2 and abs(prices[0] - 0.5) < 1e-9 and abs(prices[1] - 0.5) < 1e-9:
        yes_won, res_flag = np.nan, "fifty_fifty"
    else:
        yes_won, res_flag = np.nan, "ambiguous_prices"
    if res_flag == "ok" and status not in ("resolved",):
        res_flag = f"uma_status_{status}"
        yes_won = np.nan
    events = m.get("events") or []
    ev = events[0] if events else {}
    out.update(
        market_id=int(m["id"]), condition_id=m.get("conditionId"), question=q, slug=slug,
        event_id=int(ev["id"]) if ev.get("id") else -1, event_slug=ev.get("slug"),
        outcome_kind=kind, category=classify(m), is_short_auto=int(is_short_auto), is_recurring=int(is_recurring),
        start_ts=st, end_ts=end, resolved_ts=resolved, created_ts=created,
        volume_usd=float(m.get("volumeNum") or m.get("volume") or 0),
        liquidity_usd=float(m.get("liquidityNum") or m.get("liquidity") or 0),
        neg_risk=int(bool(m.get("negRisk"))), uma_status=status, res_flag=res_flag, yes_won=yes_won,
        yes_token_id=str(toks[0]) if toks else None, no_token_id=str(toks[1]) if len(toks) > 1 else None,
        tags="|".join(tags[:12]),
    )
    return out


# ---------------------------------------------------------------------------- stage 2: sample
def half_year(ts: float) -> str:
    d = pd.Timestamp(ts, unit="s")
    return f"{d.year}H{1 if d.month <= 6 else 2}"


def waterfill(sizes: dict, total: int) -> dict:
    """Allocate `total` draws across cells as evenly as possible, capped by cell size."""
    alloc = {k: 0 for k in sizes}
    remaining = total
    open_cells = {k for k, v in sizes.items() if v > 0}
    while remaining > 0 and open_cells:
        share = max(1, remaining // len(open_cells))
        for k in sorted(open_cells):
            take = min(share, sizes[k] - alloc[k], remaining)
            alloc[k] += take
            remaining -= take
            if alloc[k] >= sizes[k]:
                open_cells.discard(k)
            if remaining <= 0:
                break
    return alloc


def stage_sample(vol_min: float = VOL_MIN):
    rows = []
    with gzip.open(GAMMA_FILE, "rt") as f:
        for line in f:
            rows.append(parse_market(json.loads(line)))
    u = pd.DataFrame(rows).drop_duplicates("market_id")
    u["period"] = u["end_ts"].map(half_year)
    log = {"universe_fetched_vol_ge_25k": len(u)}
    u = u[u.volume_usd >= vol_min]
    log[f"vol_ge_{int(vol_min)}"] = len(u)
    log["by_outcome_kind"] = u.outcome_kind.value_counts().to_dict()
    binary = u[(u.outcome_kind == "yesno") | ((u.outcome_kind == "updown") & (u.is_short_auto == 1))].copy()
    log["binary_yesno_or_updown"] = len(binary)
    log["excluded_non_yesno_two_outcome(team names/over-under etc.)"] = int((u.outcome_kind == "other").sum())
    log["resolution_flags"] = binary.res_flag.value_counts().to_dict()
    binary = binary[binary.res_flag == "ok"]
    binary = binary[binary.yes_token_id.notna()]
    log["clean_resolved"] = len(binary)
    log["clean_by_period_shortauto"] = binary.groupby(["period", "is_short_auto"]).size().unstack(fill_value=0).to_dict()
    pop_main = binary[binary.is_short_auto == 0]
    pop_short = binary[binary.is_short_auto == 1]
    log["population_main_by_period_cat"] = pop_main.groupby(["period", "category"]).size().unstack(fill_value=0).to_dict()

    rng = np.random.default_rng(SEED)
    picks = []
    for pop, n_target, name in ((pop_main, N_MAIN, "main"), (pop_short, N_SHORT, "short")):
        keys = ["period", "category"] if name == "main" else ["period"]
        cells = {k: g for k, g in pop.groupby(keys)}
        alloc = waterfill({k: len(g) for k, g in cells.items()}, n_target)
        for k, g in cells.items():
            n = alloc[k]
            if n == 0:
                continue
            idx = rng.choice(len(g), size=n, replace=False)
            s = g.iloc[np.sort(idx)].copy()
            s["stratum"] = f"{name}:" + ("/".join(k) if isinstance(k, tuple) else str(k))
            s["stratum_pop"] = len(g)
            s["sample_weight"] = len(g) / n
            picks.append(s)
    sample = pd.concat(picks, ignore_index=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(MANIFEST, index=False)
    binary.drop(columns=["question"]).to_csv(RAW / "population_clean.csv.gz", index=False)
    log["sampled_main"] = int((sample.is_short_auto == 0).sum())
    log["sampled_short"] = int((sample.is_short_auto == 1).sum())
    (RAW / "sample_log.json").write_text(json.dumps(log, indent=1, default=str))
    print(json.dumps({k: v for k, v in log.items() if not isinstance(v, dict) or len(str(v)) < 400}, indent=1, default=str))


# ---------------------------------------------------------------------------- stage 3: prices
def _cache_path(token: str, fid: int) -> Path:
    return CLOB_DIR / f"f{fid}" / f"{token[-24:]}.json.gz"


def fetch_history(token: str, start_ts: float, fid: int) -> int:
    p = _cache_path(token, fid)
    if p.exists():
        return -1
    p.parent.mkdir(parents=True, exist_ok=True)
    st = int(max(1_600_000_000, (start_ts if not np.isnan(start_ts) else 1_640_000_000) - 2 * 86400))
    d = get_json(CLOB, {"market": token, "startTs": st, "fidelity": fid})
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump({"token": token, "startTs": st, "fidelity": fid, "resp": d}, f)
    tmp.rename(p)
    return len(d.get("history", []) or [])


def stage_prices():
    s = pd.read_csv(MANIFEST, dtype={"yes_token_id": str, "no_token_id": str})
    jobs = [(r.yes_token_id, r.start_ts, 60) for r in s.itertuples()]
    jobs += [(r.yes_token_id, r.start_ts, 1) for r in s[s.is_short_auto == 1].itertuples()]
    todo = [j for j in jobs if not _cache_path(j[0], j[2]).exists()]
    print(f"[prices] {len(jobs)} jobs, {len(todo)} uncached", flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        futs = [ex.submit(fetch_history, *j) for j in todo]
        for fu in as_completed(futs):
            fu.result()
            done += 1
            if done % 200 == 0:
                el = time.time() - t0
                print(f"[prices] {done}/{len(todo)}  {el:.0f}s  eta {el / done * (len(todo) - done):.0f}s", flush=True)
    print(f"[prices] done in {time.time() - t0:.0f}s")


def load_history(token: str, fid: int) -> pd.DataFrame:
    p = _cache_path(token, fid)
    if not p.exists():
        return pd.DataFrame(columns=["t", "p"])
    with gzip.open(p, "rt") as f:
        d = json.load(f)
    h = (d.get("resp") or {}).get("history") or []
    return pd.DataFrame(h, columns=["t", "p"]) if h else pd.DataFrame(columns=["t", "p"])


# ---------------------------------------------------------------------------- stage 4: build
HORIZONS = {"6h": 6 * 3600, "24h": 24 * 3600, "72h": 72 * 3600, "7d": 7 * 86400}
SHOCK_DP = 0.10
SHOCK_WIN = 6 * 3600
PIN_HI, PIN_LO = 0.99, 0.01


def known_time(ts: np.ndarray, p: np.ndarray, yes_won: int) -> float:
    """Earliest ts after which the YES price stays pinned on the winning side (>=0.99 if YES won, <=0.01 if NO)."""
    ok = p >= PIN_HI if yes_won == 1 else p <= PIN_LO
    if len(ok) == 0 or not ok[-1]:
        return np.nan
    bad = np.where(~ok)[0]
    first = 0 if len(bad) == 0 else bad[-1] + 1
    return float(ts[first])


def detect_shocks(ts: np.ndarray, p: np.ndarray, skip_until: float) -> list[tuple[int, int]]:
    """Return (pre_idx, detect_idx) pairs. At each bar j we look back <=6h; a shock fires at the first j where
    |p_j - p_i| >= 0.10 for some i in the window (pre = the extreme i). After a detection the scan resumes at the
    detection bar (so the next shock must be a NEW 10pt move measured from bars at/after this detection)."""
    out = []
    n = len(ts)
    lo = 0
    j = 1
    while j < n:
        while ts[j] - ts[lo] > SHOCK_WIN:
            lo += 1
        if ts[j] >= skip_until and j > lo:
            w = p[lo:j]
            i_min = lo + int(np.argmin(w))
            i_max = lo + int(np.argmax(w))
            up = p[j] - p[i_min]
            dn = p[i_max] - p[j]
            if up >= SHOCK_DP or dn >= SHOCK_DP:
                i = i_min if up >= dn else i_max
                if ts[i] >= skip_until:
                    out.append((i, j))
                    lo = j
        j += 1
    return out


def asof(ts: np.ndarray, p: np.ndarray, t: float) -> float:
    k = np.searchsorted(ts, t, side="right") - 1
    return float(p[k]) if k >= 0 else np.nan


def stage_build():
    s = pd.read_csv(MANIFEST, dtype={"yes_token_id": str, "no_token_id": str})
    mk_rows, px_parts, sh_rows, sa_parts = [], [], [], []
    qa = {"post_resolution_points_dropped": 0, "placeholder_first_points_dropped": 0, "markets_no_history": 0}
    for r in s.itertuples():
        h = load_history(r.yes_token_id, 60)
        h = h.dropna().astype({"t": "int64", "p": "float64"}).sort_values("t").drop_duplicates("t", keep="last")
        n_raw = len(h)
        last_raw = h.t.iloc[-1] if n_raw else np.nan
        if n_raw > 1 and h.p.iloc[0] == 0.5 and abs(h.p.iloc[1] - 0.5) >= 0.05:
            h = h.iloc[1:]
            qa["placeholder_first_points_dropped"] += 1
        res_ts = r.resolved_ts if not np.isnan(r.resolved_ts) else r.end_ts
        n_after = int((h.t > res_ts).sum())
        qa["post_resolution_points_dropped"] += n_after
        h = h[h.t <= res_ts]
        ts, p = h.t.to_numpy(), h.p.to_numpy()
        if len(ts) == 0:
            qa["markets_no_history"] += 1
        fid = float(np.median(np.diff(ts)) / 60) if len(ts) > 2 else np.nan
        k_ts = known_time(ts, p, int(r.yes_won)) if len(ts) else np.nan
        last_p = p[-1] if len(p) else np.nan
        mk_rows.append(dict(
            market_id=r.market_id, condition_id=r.condition_id, question=r.question, category=r.category,
            is_short_auto=r.is_short_auto, start_ts=r.start_ts, end_ts=r.end_ts, resolved_ts=r.resolved_ts,
            volume_usd=r.volume_usd, liquidity_usd=r.liquidity_usd, neg_risk=r.neg_risk, yes_won=int(r.yes_won),
            yes_token_id=r.yes_token_id, n_price_points=len(ts), fidelity_min=fid,
            # extras
            slug=r.slug, event_id=r.event_id, is_recurring=r.is_recurring, outcome_kind=r.outcome_kind,
            first_price_ts=ts[0] if len(ts) else np.nan, last_price_ts=ts[-1] if len(ts) else np.nan,
            last_raw_price_ts=last_raw, n_points_after_resolution_dropped=n_after,
            last_p_yes=last_p, known_ts=k_ts,
            frac_hours_changed=float(np.mean(np.diff(p) != 0)) if len(p) > 2 else np.nan,
            stratum=r.stratum, stratum_pop=r.stratum_pop, sample_weight=r.sample_weight, tags=r.tags,
        ))
        if len(ts):
            after_known = (ts >= k_ts).astype("int8") if not np.isnan(k_ts) else np.zeros(len(ts), "int8")
            px_parts.append(pd.DataFrame({"market_id": r.market_id, "ts": ts, "p_yes": p, "after_known": after_known}))
        # shocks: hourly series, regular markets only, skip first 6h of a market's price life
        if r.is_short_auto == 0 and len(ts) > 3:
            for i, j in detect_shocks(ts, p, ts[0] + SHOCK_WIN):
                t_det = ts[j]
                row = dict(market_id=r.market_id, category=r.category, pre_ts=int(ts[i]), detect_ts=int(t_det),
                           p_pre=p[i], p_post=p[j], dp=p[j] - p[i], direction=int(np.sign(p[j] - p[i])),
                           lag_h=(t_det - ts[i]) / 3600,
                           p_next_bar=float(p[j + 1]) if j + 1 < len(p) else np.nan,
                           next_bar_ts=int(ts[j + 1]) if j + 1 < len(ts) else -1)
                for name, hsec in HORIZONS.items():
                    tgt = t_det + hsec
                    row[f"p_{name}"] = asof(ts, p, tgt) if tgt <= ts[-1] else np.nan
                w0 = np.searchsorted(ts, ts[i] - 24 * 3600)
                dpre = np.diff(p[w0:i + 1])
                row["pre_vol_24h"] = float(np.std(dpre)) if len(dpre) > 2 else np.nan
                row["spike_revert_next_bar"] = int(j + 1 < len(p) and abs(p[j + 1] - p[i]) < 0.03)
                row.update(yes_won=int(r.yes_won), p_final=float(r.yes_won),
                           hours_to_end=(r.end_ts - t_det) / 3600, hours_to_resolved=(res_ts - t_det) / 3600,
                           post_pinned=int(p[j] >= PIN_HI or p[j] <= PIN_LO),
                           after_known=int(not np.isnan(k_ts) and t_det >= k_ts),
                           volume_usd=r.volume_usd, neg_risk=r.neg_risk, event_id=r.event_id)
                sh_rows.append(row)
        if r.is_short_auto == 1:
            h1 = load_history(r.yes_token_id, 1)
            if len(h1):
                h1 = h1.dropna().astype({"t": "int64", "p": "float64"}).sort_values("t").drop_duplicates("t")
                h1 = h1[h1.t <= res_ts]
                sa_parts.append(pd.DataFrame({"market_id": r.market_id, "ts": h1.t.to_numpy(), "p_yes": h1.p.to_numpy()}))
    markets = pd.DataFrame(mk_rows)
    prices = pd.concat(px_parts, ignore_index=True)
    shocks = pd.DataFrame(sh_rows)
    shocks["n_shocks_market"] = shocks.groupby("market_id").market_id.transform("size")
    for c in ("start_ts", "end_ts", "resolved_ts", "first_price_ts", "last_price_ts", "last_raw_price_ts", "known_ts"):
        markets[c] = markets[c].round().astype("Int64")
    markets.to_csv(DATA / "markets.csv.gz", index=False)
    prices.to_csv(DATA / "prices_hourly.csv.gz", index=False, float_format="%.6g")
    shocks.to_csv(DATA / "shocks.csv.gz", index=False, float_format="%.6g")
    if sa_parts:
        pd.concat(sa_parts, ignore_index=True).to_csv(DATA / "prices_short_auto_1min.csv.gz", index=False,
                                                      float_format="%.6g")
    (RAW / "build_qa.json").write_text(json.dumps(qa, indent=1))
    print(json.dumps(qa, indent=1))
    print(f"[build] markets={len(markets)} price_rows={len(prices)} shocks={len(shocks)}")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("universe", "all"):
        stage_universe()
    if stage in ("sample", "all"):
        stage_sample()
    if stage in ("prices", "all"):
        stage_prices()
    if stage in ("build", "all"):
        stage_build()
