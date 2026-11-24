# 02 MiroFish Code Analyst — brief

Repo analysed: `666ghj/MiroFish` @ commit `7657031` (read-only clone in session scratchpad, AGPL-3.0; nothing copied
into the lab except short quotes). Path abbreviations: `svc/` = `backend/app/services/`, `scr/` = `backend/scripts/`,
`oasis/` = installed `camel-oasis==0.2.5`, `camel/` = installed `camel-ai==0.2.78`.

## Delivered (files + one-line description each)
- `knowledge/02_mirofish_analyst.md` — this brief (pipeline spec, test results, cost model, backtest verdict, **MECHANICS SPEC for 05**).
- `src/mirofish_cost_model.py` — standalone re-implementation (not MiroFish code) of MiroFish's per-round agent-activation rule + a token/$ cost model; `python3 -I src/mirofish_cost_model.py` reproduces the cost table below.

## Key facts other specialists MUST use
1. **MiroFish has no market, price, trading, Polymarket, Kalshi, odds, wallet or probability output anywhere** (grep over
   all .py/.vue/.js/.json/.md). The only "probability" strings are the activation probability in the runner and a comment
   on `influence_weight`. It is a **social-media opinion simulator** (OASIS Twitter + Reddit clones) whose final product
   is an **LLM-written Markdown report** (2–5 sections). No numeric forecast is produced.
2. **No "God View terminal", no "56 agents", no retail/institutional/opinion-leader archetypes** exist in code. "God's-eye
   view" (上帝视角) is only (a) marketing copy (`locales/en.json:40`) and (b) wording in the ReportAgent system prompt
   (`svc/report_agent.py:553,604,644`). Agent count = number of typed entities Zep extracts from the uploaded document
   (one agent per entity); entity types are LLM-designed (exactly 10, last two `Person`/`Organization`,
   `svc/ontology_generator.py:113-121`); examples are Student/Professor/MediaOutlet/University/GovernmentAgency.
   The README advertises "thousands"/"million-scale" agents (`locales/en.json:42,51`).
3. **"Scenario injection" = the `simulation_requirement` text + LLM-generated initial posts published at round 0.**
   There is no mid-simulation injection API. `scheduled_events` is hard-coded to `[]` (`svc/simulation_config_generator.py:725`).
   The only post-run interaction is interviewing agents / chatting with the ReportAgent.
4. **Most of the generated "behavioral" config is dead at runtime.** `sentiment_bias`, `stance`, `influence_weight`,
   `posts_per_hour`, `comments_per_hour`, `response_delay_*`, `echo_chamber_strength`, `viral_threshold`,
   `recency/popularity/relevance_weight`, `hot_topics`, `narrative_direction` are written to `simulation_config.json` but
   **never read by the simulation scripts** (grep: only consumer is a count in `api/simulation.py:1287`; `influence_weight`
   is used once to pick a fallback poster for initial posts, `svc/simulation_config_generator.py:799`).
   Runtime only uses: `activity_level`, `active_hours`, `agents_per_hour_min/max`, `peak_hours`, `off_peak_hours`,
   peak/off-peak multipliers, `total_simulation_hours`, `minutes_per_round`, initial posts.
   Twitter profile CSV keeps only `name, username, user_char(=bio+persona), description` (`svc/oasis_profile_generator.py:1139-1147`);
   follower/friend counts, MBTI, age are dropped for Twitter. **An agent's stance/personality lives only in its ~2,000-word
   persona text that the LLM reads.**
5. **One LLM call per active agent per round** (OASIS `SocialAgent.max_iteration=1`, `oasis/social_agent/agent.py:69,112`;
   CAMEL loop breaks after the first tool-call iteration). Agent memory = CAMEL `ChatHistoryMemory`, **unbounded and
   never reset**: every activation's feed prompt + tool call is appended and resent (`camel/agents/chat_agent.py:1981`),
   context limit = model window (qwen-plus 131,072; gpt-4o 128,000 tokens). ⇒ input tokens grow ~quadratically with rounds.
6. **Zep is write-only during the simulation.** Agent actions are batched (5 per episode, DO_NOTHING skipped) into
   Zep graph episodes (`svc/zep_graph_memory_updater.py:228,372,494`); OASIS agents never read Zep. Zep memory feeds
   only the ReportAgent's retrieval tools. Graph-memory update is off by API default (`api/simulation.py:1554`) but the
   frontend turns it on (`frontend/src/components/Step3Simulation.vue:402`).
7. Test suite (as-is, no keys): **backend 130/130 passed in 5.7 s; root `tests/` 28/28 passed** (Python 3.12.x via uv).
   Tests cover plumbing only (JSON repair, Zep paging/retry/lifecycle, profile normalisation, API error paths); **zero tests
   of simulation dynamics or forecast accuracy.**
8. Cost of ONE real 56-agent run, parallel Twitter+Reddit, incl. prep + report (see table): **qwen-plus ≈ $0.6 / $1.7–4.5 /
   $3.7–14 for 10 / 40 / 72 rounds; Claude Sonnet 5.5 ≈ $4 / $9–24 / $20–73; GPT-4o ≈ $4.5 / $11–29 / $24–90.**
   Stochastic ⇒ a usable forecast needs ≥10 replicas ⇒ multiply by 10.
9. **Backtesting MiroFish on past events is invalid by default** (parametric leakage + prompt that asks the LLM to
   "最大程度还原已有现实情况" = reproduce reality as faithfully as possible, `svc/oasis_profile_generator.py:718,734,783`).
   Only forward / paper tests with a pinned, pre-cutoff model snapshot are valid (protocol in §Findings D).

## Assumptions I made (and how sensitive results are to them)
- Synthetic 56-agent population for cost Monte Carlo: 40 individuals (activity U(0.6,0.9); hours 18–23, half also 9–13),
  8 media/KOL (0.4–0.6; hours 8–23), 8 institutions (0.1–0.3; hours 9–17) — taken from the config-generator prompt
  guidance (`svc/simulation_config_generator.py:847-850`). Two time configs: LLM-prompt example `agents_per_hour` 5–50,
  and rule fallback 3–11 (`:566-575`, `:604-605`). Costs differ ~3–4× between these → **the time-config LLM output is the
  single biggest cost driver**.
- Tokens per sim call: persona system prompt 2,800; tool schemas 700 (Twitter)/1,500 (Reddit); feed 900/1,800; output 180;
  each activation adds ~1.2k/2.1k tokens to that agent's memory. ±30% on these moves costs roughly ±30%.
- No prompt caching assumed (MiroFish uses plain OpenAI-compatible calls). Provider auto-caching of the stable history
  prefix could cut sim input cost by 50–90% — treat my numbers as an upper-mid estimate.
- Prices (USD / 1M tokens, input/output): qwen-plus intl ≈ 0.40/1.20 and mainland ≈ 0.11/0.28 (from memory, **verify on
  Alibaba Model Studio**); gpt-4o-mini 0.15/0.60, GPT-4o 2.50/10 (from memory, verify); Claude Sonnet 5.5 2/10, Sonnet 4.6
  3/15, Haiku 5.5 0.10/0.50 (Anthropic price table cached 2026-11-03). MiroFish only speaks the OpenAI chat API, so Claude
  needs an OpenAI-compatible endpoint/gateway.
- ReportAgent: 4 sections, ~5 ReACT iterations each, 1 InsightForge + 1 interview (5 agents × 2 platforms) per section.

## Findings / evidence

### A. Pipeline as actually implemented (file:line)
| Stage | What happens | LLM / Zep usage |
|---|---|---|
| 1 Ontology | User uploads PDF/MD/TXT + natural-language "simulation requirement". LLM designs exactly 10 entity types + edge types (`svc/ontology_generator.py:113,287`), text truncated to 50k chars (`:251`). | 1 LLM call |
| 2 Graph build | Text split into 500-char chunks, 50 overlap (`svc/graph_builder.py:80`, `config.py:41-42`), sent to Zep Cloud as episodes with the ontology; **Zep's own LLM** extracts entities/edges. | Zep credits (no MiroFish LLM) |
| 3 Entity read | Nodes whose labels include a defined type become agents (`svc/zep_entity_reader.py:221-275`). | Zep reads |
| 4 Profiles | Per entity, Zep search for context (≤3,000 chars), then LLM writes `bio` (200字), `persona` (2000字: background, MBTI, posting style, stance, "personal memory of the event"), age, gender, mbti, country, profession, topics (`svc/oasis_profile_generator.py:721-816`), 5 in parallel (`:901`). Follower/friend/karma counts are random ints if LLM omits them (`:324-327`). | N LLM calls (+retries), 2N Zep searches |
| 5 Sim config | LLM: time config (24–168 h, 30–120 min/round, agents/hour bounds ≤0.9N, `:543,580-581`), event config (hot topics, narrative, initial posts with poster_type), agent activity configs in batches of 15 (`:217,836-905`). Rule-based fallback per type (`:910-990`). | 2 + ⌈N/15⌉ LLM calls |
| 6 Simulation | Subprocess `scr/run_parallel_simulation.py` (or twitter/reddit only). Round 0: initial posts as ManualAction CREATE_POST. Rounds 1..R: activation rule (§E.3), each active agent → `LLMAction` (`:1253,1452`), OASIS `env.step` with ≤30 concurrent LLM requests (`:1159`). R = total_hours·60/minutes_per_round (default 72), optionally capped by user `max_rounds` (`:1217`). | 1 LLM call per active agent per round per platform |
| 7 Memory update | Actions → natural-language episodes → Zep `graph.add` (batch 5) (`svc/zep_graph_memory_updater.py`). | Zep writes |
| 8 Report | ReportAgent: outline (2–5 sections, `svc/report_agent.py:572`), each section ReACT with min 3 / max 5 tool calls, 5 iterations (`:882,1326-1327`). Tools: insight_forge (LLM sub-queries + Zep search), panorama_search, quick_search, interview_agents (LLM picks ≤5 agents, LLM writes questions, agents answer via OASIS INTERVIEW on both platforms, LLM summary) (`svc/zep_tools.py:943-1721`). | ~40–60 LLM calls |
| 9 Interaction | Chat with ReportAgent / interview any agent (API `/report/chat`, `/simulation/interview*`). | per request |

**How the "prediction" is produced:** the ReportAgent is told the simulation *is* the future ("你正在观察的不是实验数据，而是未来的预演",
`svc/report_agent.py:556-558`) and writes a narrative report. Output = Markdown text. The only numbers available are
counts (posts, likes, actions per agent: `/simulation/<id>/agent-stats`, `/report/tools/statistics` = node/edge counts).

### B. Agent decision mechanics (OASIS 0.2.5, as wired by MiroFish)
- System prompt: "You're a Twitter user ... Your actions should be consistent with your self-description" + persona
  (`oasis/social_platform/config/user.py`). Reddit adds gender/age/MBTI/country.
- User prompt each activation: the refreshed feed as JSON + "pick one you want to perform action that best reflects your
  current inclination" (`oasis/social_agent/agent_environment.py`). Follower/follow counts are computed but not inserted into the template.
- Feed (Twitter): 2 posts sampled from the rec table (`refresh_rec_post_count=2, max_rec_post_len=2`) + up to 3 posts
  from followees ordered by `num_likes DESC` (`oasis/environment/env.py:81-84`, `oasis/social_platform/platform.py:290`).
  Rec score = cosine(TwHIN-BERT(user bio), TwHIN-BERT(post)) × log((271.8 − Δt)/100), Δt in time steps (`recsys.py:472`);
  recomputed every step (`env.py:152`); time_step +1 per step (Twitter only, `env.py:198`). Requires downloading
  `Twitter/twhin-bert-base` from Hugging Face + torch (venv is 7.1 GB).
- Feed (Reddit): rec table = top 100 by hot score sign(s)·log10(max(|s|,1)) + t/45000, s = likes − dislikes
  (`recsys.py:168-195`, `env.py:95-96`); 5 random of those per refresh.
- No initial follow graph: every agent starts with 0 followers in the platform DB; follows only arise from FOLLOW actions.
- Actions: Twitter {CREATE_POST, LIKE_POST, REPOST, FOLLOW, DO_NOTHING, QUOTE_POST}; Reddit {LIKE/DISLIKE_POST,
  CREATE_POST, CREATE_COMMENT, LIKE/DISLIKE_COMMENT, SEARCH_POSTS, SEARCH_USER, TREND, REFRESH, DO_NOTHING, FOLLOW, MUTE}
  (`scr/run_parallel_simulation.py:178-202`). Chosen by LLM function-calling; a response may contain >1 tool call.
- No random seed anywhere and no temperature set for agent calls ⇒ runs are not reproducible.

### C. Tests (task 2)
- Setup: copy of repo at `<scratchpad>/mirofish-run/`, `uv sync --python 3.12 --group dev` succeeded (exit 0; pulls
  torch 2.9.1, transformers 4.57.3, camel-oasis 0.2.5, camel-ai 0.2.78, zep-cloud 3.25.0). Run with LLM/ZEP/OPENAI/
  ANTHROPIC keys unset.
- `backend/`: **130 passed, 0 failed, 0 skipped** (6 SyntaxWarnings inside zep_cloud). Root `tests/` (star-history
  scripts): **28 passed**.
- Without keys: `Config.validate()` → `['LLM_API_KEY 未配置', 'ZEP_API_KEY 未配置']`; `LLMClient()` raises; every
  pipeline stage except file parsing needs **both** LLM_API_KEY (any OpenAI-compatible endpoint) and ZEP_API_KEY (Zep
  Cloud only; `ZEP_API_URL` explicitly rejected, `config.py:71-72`). Simulation scripts also need Hugging Face access for TwHIN-BERT.
  An end-to-end run was NOT attempted (no keys, by instruction).

### D. Cost per real run, 56 agents, parallel (Twitter+Reddit), prep + sim + report (task 3)
Monte Carlo (200 reps) with `src/mirofish_cost_model.py`. Rounds start at sim hour 00:00, hours 0–5 have multiplier 0.05
⇒ the first 6 rounds are nearly empty (that is why 10 rounds is cheap).

| time config | rounds | sim agent-calls | total LLM calls | input / output tokens | qwen-plus intl | qwen-plus CN | gpt-4o-mini | GPT-4o | Sonnet 5.5 | Sonnet 4.6 | Haiku 5.5 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LLM example 5–50 | 10 | 43 | 188 | 0.91M / 0.22M | $0.63 | $0.16 | $0.27 | $4.50 | $4.05 | $6.07 | $0.20 |
| | 40 | 762 | 907 | 10.3M / 0.35M | $4.54 | $1.23 | $1.76 | $29.3 | $24.1 | $36.2 | $1.21 |
| | 72 | 1,662 | 1,807 | 34.1M / 0.51M | $14.3 | $3.90 | $5.43 | $90.5 | $73.4 | $110 | $3.67 |
| fallback 3–11 | 10 | 20 | 165 | 0.77M / 0.22M | $0.57 | $0.15 | $0.25 | $4.11 | $3.72 | $5.58 | $0.19 |
| | 40 | 309 | 454 | 3.4M / 0.27M | $1.68 | $0.45 | $0.67 | $11.2 | $9.49 | $14.2 | $0.47 |
| | 72 | 651 | 796 | 8.3M / 0.33M | $3.73 | $1.01 | $1.45 | $24.1 | $20.0 | $30.0 | $1.00 |

- Prep (ontology + ~62 profile calls + config) ≈ 0.2M in / 0.2M out tokens, the same for every round count; output-heavy (2000-word personas).
- At ≥40 rounds, >90% of input tokens are the agents' re-sent chat history.
- Zep: the repo only says "free monthly quota is sufficient for simple usage" (`.env.example`, `README.md:126`); no numbers.
  Load per run ≈ (doc chars / 450) build episodes (~110 for a 50k-char doc) + (non-DO_NOTHING actions / 5) memory
  episodes (~100–300) + ~60 profile searches + report searches. Check current Zep Cloud free-tier limits before a campaign.
- "7 days of live trading" in the style of the post (say 5 markets/day × 10 replicas × 40 rounds) ≈ 350 runs ≈
  $600–1,600 qwen-plus intl, or $3.3k–8.4k Sonnet 5.5, before caching.

### E. Methodological verdict: can MiroFish be backtested? (task 4)
**No, not validly, unless the event happens after the LLM's training cutoff.** Leakage channels:
1. *Parametric knowledge*: the agents, persona writer, config writer and ReportAgent are the same LLM. For any event
   before its training cutoff (e.g. how markets reacted to a 2024 Fed cut) the "crowd" already knows the answer.
2. *Explicit instruction to use it*: the persona prompts ask the model to reproduce existing reality as faithfully as
   possible and to write each entity's "memory of its actions in the event" (`svc/oasis_profile_generator.py:718,734,754,783,803`).
3. *Zep's extraction LLM* (Zep-side model, cutoff unknown) writes entity summaries that feed personas.
4. *Hindsight in seed selection*: the analyst picks the seed document and phrases the requirement after the outcome is known.
5. *No numeric output*: turning the report into a probability needs a mapping rule; if it is designed after looking at outcomes, that is look-ahead.

Clean windows (an event must resolve after the model's training cutoff plus a 1–3 month buffer, using a **pinned
snapshot**, never a rolling alias like `qwen-plus`):
- gpt-4o / gpt-4o-mini (OpenAI-documented cutoff Oct 2023): clean for the whole lab TRAIN (2024–mid 2025) and TEST (≥2025-07-01)
  windows, but with stale world knowledge (that is not leakage, but personas will be unrealistic).
- qwen-plus (rolling alias; Qwen2.5 in 2024, Qwen3 from Apr 2025): cutoff not precisely published. Assume contamination up
  to at least the snapshot release date. **Most of the TEST window is probably contaminated** with any 2025+ snapshot.
- Current Claude Sonnet 5.5 / 4.6, or any 2026 frontier model: training data very likely covers most or all of TEST. **Treat it as
  contaminated; use it for forward tests only.** Check the model card's "reliable knowledge cutoff".
- Empirical guard: before using an event, ask the bare model (no tools, temperature 0) for the outcome. If it answers
  correctly more often than chance across paraphrases, drop the event.

**A valid forward / paper-test protocol:**
1. Pre-register in git (commit hash and timestamp before resolution): model snapshot ID, temperature, all prompts, N agents,
   rounds, number of replicas K (≥10), the numeric extraction rule, the trading rule, and the market universe filter.
2. At decision time T0, freeze the seed corpus to documents published before T0 (archived copies with hashes). Record the
   Polymarket best bid/ask, mid and depth at T0 (see 03).
3. Run K replicas. Extract the forecast with a pre-registered rule, e.g. (a) the stance share among CREATE/QUOTE posts in the
   last 24 sim hours, mapped via logistic calibration fitted only on earlier forward data; or (b) a fixed-prompt LLM judge
   that reads the report and outputs P(YES). Report the replica spread.
4. Trade only if |p̂ − ask| exceeds fees + half-spread + a pre-set margin. Fill at the ask (taker), cap size at 5% of equity, count
   slippage against depth.
5. Score with Brier and log-loss against two baselines: the market price at T0 and a bare-LLM direct forecast (same model,
   no simulation). MiroFish only adds value if it beats both.
6. Sample size: per-$ P&L SD is about 1 near p≈0.5, so t≈2 needs n ≈ (2/edge)² bets: ~1,600 independent markets for a 5¢
   edge and ~400 for 10¢. A 7-day, $7,358 claim with an unknown bankroll and selection is statistically meaningless.

### F. Re the Instagram claim
The claim cannot come from MiroFish as it is on GitHub. It would need bespoke code (market feed, forecast-extraction layer,
order execution) that is not in the repo, and the post gives no code, bankroll, trade log or out-of-sample record. The
copytrade referral link is a monetisation incentive. The parts that do match the repo are the "God's-eye view" wording, injecting a
scenario as seed text, and agents with persona and memory.

---

## ===== BEGIN MECHANICS SPEC (for 05 Swarm Modeler) =====
Goal: a deterministic (seeded), LLM-free ABM that is **structurally faithful to what MiroFish runs**. Items marked
[RUNTIME] are what MiroFish actually executes. [GENERATED-ONLY] means the config generator produces it but the runtime
ignores it; use it as an explicit latent parameter, because in MiroFish its effect is only implicit, through the LLM
reading persona text. [ABM-SUB] means a substitute for an LLM decision, with assumed defaults that need calibration and
sensitivity runs.

### S1. Agents and types
- N agents, one per entity (default N=56 for the lab). Three type groups, matching the config-generator guidance
  (`svc/simulation_config_generator.py:847-850`) and the rule fallback (`:910-990`):

| group | share (default) | activity_level [RUNTIME] | active_hours [RUNTIME] | influence_weight [GEN-ONLY] | response delay min [GEN-ONLY] | posts/h [GEN-ONLY] |
|---|---|---|---|---|---|---|
| institutional (University/GovernmentAgency/NGO; "official") | 8/56 | U(0.1,0.3); rule 0.2 | 9–17 | 2.5–3.0; rule 3.0 | 60–240 | 0.1 |
| media / opinion leader (MediaOutlet; expert/KOL) | 8/56 | U(0.4,0.6); rule 0.5 (media), 0.4 (expert) | 8–23 (media 7–23, expert 8–21) | media 2.0–2.5, experts 1.5–2.0; rule 2.5/2.0 | 5–30 / 15–90 | 0.8 / 0.3 |
| individual / retail (Student/Person/Alumni) | 40/56 | U(0.6,0.9); rule 0.7 (person), 0.8 (student) | 18–23; rule person [9–13, 18–23] | 0.8–1.2; rule 1.0 | 1–15 | 0.5–0.6 |

- Per-agent latent state for the ABM: `stance ∈ {supportive, opposing, neutral, observer}` and `sentiment_bias ∈ [−1,1]`
  [GEN-ONLY; rule default 0.0 and neutral, LLM chooses per persona]. Map to an opinion x_i ∈ [−1,1]: x_i(0) = sentiment_bias,
  with sign forced by stance (supportive >0, opposing <0, neutral ≈0, observer ≈0 and low posting propensity).
- Stubbornness / persona anchor s_i ∈ [0,1] [ABM-SUB]: institutions 0.8, media 0.5, retail 0.3 (default).
  MiroFish's persona text pins agents to their identity, so they are strongly anchored.

### S2. Time and schedule [RUNTIME]
- `minutes_per_round` default 60 (allowed 30–120). `total_simulation_hours` default 72 (24–168). Rounds R = hours·60/mpr,
  capped by `max_rounds`. Round 0 = initial posts only.
- Sim clock starts at 00:00: hour(r) = (r·mpr/60) mod 24 for r = 0..R−1 (`scr/run_parallel_simulation.py:1235-1237`).
- Hour multiplier m(h): 1.5 if h ∈ peak {19,20,21,22}; 0.05 if h ∈ off-peak {0..5}; **1.0 otherwise**. The morning (0.4) and work (0.7)
  multipliers are defined but not used by the runner.
- `agents_per_hour_min/max`: LLM picks within [1, 0.9N]; prompt example 5–50; fallback min = max(1, N//15), max = max(5, N//5)
  (N=56: 3–11). Guards: min<max, max≤N (`:604-640`).

### S3. Activation rule per round (exact) [RUNTIME]
```
target = floor( Uniform(amin, amax) * m(hour) )
candidates = [ i : hour ∈ active_hours_i  and  Uniform(0,1) < activity_level_i ]
active = sample_without_replacement(candidates, min(target, |candidates|))
```
(`scr/run_parallel_simulation.py:1040-1090`). Twitter and Reddit run as separate worlds with independent draws. The same
agents exist in both, but the two worlds never exchange information. A reference implementation is in `src/mirofish_cost_model.py:active_counts`.

### S4. Round 0 scenario injection [RUNTIME]
- K_0 initial posts (LLM-written, typically 3–8) about the scenario (e.g. "Fed cuts 50bps"), each assigned to an agent of
  the matching `poster_type`. Assignment cycles through agents of that type; if none match, the post goes to the agent with the
  highest influence_weight (`svc/simulation_config_generator.py:730-808`). Each post carries a stance/sentiment value.
- No later exogenous shocks exist in MiroFish. If 05 adds scheduled shocks, flag them as an extension.

### S5. Feed / exposure model [RUNTIME structure, ABM-SUB similarity]
- Twitter-world feed at each activation: 2 posts drawn from the agent's top-2 rec list + up to 3 posts from followees with the most likes.
  Rec score_ij = sim(i, post j) × log((271.8 − Δt_j)/100), Δt in rounds (score is 1 at Δt=0 and ≤0 once Δt≥171.8).
  sim = cosine(TwHIN-BERT(bio_i), emb(post_j)) in OASIS. ABM substitute: sim = 1 − |x_i − x_post|/2 plus a topic match term,
  or a fixed random persona vector. Rec table is recomputed every round over all posts (random 4,000-post subsample if more).
- Reddit-world feed: global top-100 by hot score sign(s)·log10(max(|s|,1)) + t_created_sec/45000, s = likes − dislikes;
  each refresh shows 5 sampled uniformly from that top-100. Because t/45000 is in real seconds and an episode lasts minutes,
  in practice hot score ≈ log10 of net likes.
- Follow graph: starts empty. Edges are added only by FOLLOW actions and are directed. Follower counts emerge; profile follower
  counts are NOT used.

### S6. Action choice per activation [ABM-SUB; LLM in MiroFish]
- Exactly 1 decision per active agent per round (rarely 2 tool calls). Twitter action set {post, like, repost, quote,
  follow, nothing}; Reddit {post, comment, like/dislike post, like/dislike comment, search, trend, refresh, mute, follow,
  nothing}.
- Suggested default propensities (assumption; calibrate or sensitivity-sweep ±50%). LLM agents in OASIS-style sims are
  known to be strongly "active" and agreeable:
  Twitter: post 0.25, like 0.35, repost 0.15, quote 0.05, follow 0.10, nothing 0.10.
  Reddit: post 0.15, comment 0.30, like 0.25, dislike 0.05, follow 0.05, nothing 0.10, other (search/trend/refresh/mute) 0.10.
  Type modifiers: institutions post ×2 and like ×0.3; observers post ×0.3.
- Target choice: like/repost/quote/follow target = the feed item maximising sim(i, item) + γ·log(1+likes) (herding, γ
  default 0.5, range 0–2). Dislike/mute targets the min-similarity item.
- New post content: stance x_post = x_i + N(0, σ_post), σ_post default 0.1.

### S7. Memory [RUNTIME structure]
- MiroFish: each agent keeps its full chat history (all feeds it has seen, all its actions) up to the model context
  (~128k tokens ≈ 60–100 activations), with **no decay** and no summarisation. The persona stays as the system prompt.
  Zep is NOT agent memory.
- ABM: store the multiset of seen posts with per-item weight w = λ^(rounds since seen), default λ = 1.0 (faithful: no decay);
  sensitivity λ ∈ {0.98, 0.95, 0.9} to mimic LLM recency bias. Truncate memory at M_max = 80 activations.

### S8. Opinion update [ABM-SUB; implicit in MiroFish]
Per activation, after reading feed F_i:
```
x_i ← (1−s_i)·[ x_i + μ · Σ_{j∈F_i, |x_j−x_i|<ε} w_j·(x_j − x_i) / Σ w_j ] + s_i·x_i(0)
```
Defaults: μ = 0.3 (range 0.05–0.6), ε = 0.6 (bounded confidence, range 0.3–2.0, where 2.0 means DeGroot). w_j = 1 + log(1+likes_j)
+ β·[author followed by i], β default 1.0. An optional "LLM agreeableness drift" term adds +δ toward the majority of the feed,
δ default 0.02. This mimics the known conformity / sycophancy bias of LLM agents and is the main herding knob.

### S9. Outputs the ABM should log (MiroFish itself only gives text + counts)
Per round and platform: number of active agents, action counts by type, stance shares among new posts, mean/variance
of x, follower-degree distribution, and top-k posts by likes. For trading use, the crowd signal is e.g. the stance share
of posts in the last 24 sim hours. Any mapping from that to P(YES) for a Polymarket market is **NOT part of MiroFish**;
05/04 must design and pre-register it.

### S10. Run settings for faithfulness
Seeded RNG (MiroFish is unseeded). Run ≥30 replicas per scenario and report the distribution. Default run = 72 rounds × 60
min, parallel Twitter+Reddit worlds, N=56, time config 5–50 (LLM example) with 3–11 as the sensitivity case.
## ===== END MECHANICS SPEC =====

## Warnings & pitfalls for downstream roles
- Do not cite MiroFish as "a prediction-market model". It has no market concepts. Any market link is ours and must be validated separately.
- The README/marketing claims (thousands or millions of agents, God's-eye variable injection, finance predictions "coming
  soon", `README.md:84`) are not backed by the code paths examined. The finance demo does not exist in the repo.
- Runs are stochastic and unseeded. Never compare a single MiroFish run against a market. Use replicas.
- Do not run a historical "backtest" with a current LLM and call it out-of-sample (see §E). If 04 backtests the LLM-free ABM
  from 05, that is fine, because the ABM has no parametric knowledge. But its parameters must be tuned on TRAIN only.
- The 10-round setting is misleading: rounds 1–6 are sim hours 00–05 with near-zero activity.
- License: AGPL-3.0. Do not copy MiroFish/OASIS code into the lab. The cost/activation script is an independent re-implementation.

## Open questions → addressed to <role>
- → 03 Microstructure: what fee, spread and depth to assume for the edge threshold in §E protocol step 4, and which market categories
  resolve fast enough (≤7 days) to accumulate the n ≈ 400–1,600 bets the power calculation needs?
- → 05 Swarm Modeler: please confirm you can implement S3 (exact activation) and S5 (two-world feeds). Choose and document
  the herding (γ, δ) and bounded-confidence (ε, μ) grids. S6 action propensities are assumptions and need a sweep.
- → 04 Quant Backtester: the ABM produces a stance-share time series, not a probability. Define the mapping to P(YES)
  on TRAIN only, and include "market price at T0" and "bare LLM forecast" as baselines.
- → 06 Risk Manager: the Instagram P&L claim has no verifiable trade log. Treat it as an unverified marketing claim (referral incentive).
- → 01 Data Engineer: if a forward test is run, we need timestamped order-book snapshots at the decision time T0 and archived seed documents.
