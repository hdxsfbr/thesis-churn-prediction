# Architecture: Churn Prediction & Intervention Agent

Inputs: `docs/PRD.md` (Thesis brief, source of truth), `docs/plans/churn-prediction/PRD.md` (numbered requirements R1–R9, decisions D1–D3, open questions OQ1–OQ10), and the Scout's gaps and constraints.

The repository is greenfield (README, two PRDs, nothing else), so every path below is new. Wherever a convention is ours to pick, the design uses the most common Python 3.12 one (src layout, `pyproject.toml`, pytest) and says so.

---

## 1. Design in one paragraph

A single Python package, `churn`, runs one **cycle** on a schedule (daily by default). The cycle reads synthetic source views through a configured **source contract**, computes point-in-time features in SQL inside the warehouse, scores every active subscriber with a calibrated gradient-boosted model, attaches exact per-feature contributions (TreeSHAP) and an archetype with a confidence, and writes everything to Snowflake. The **agent** then picks an intervention for each at-risk subscriber. It does this with a contextual Thompson-sampling policy over (archetype × intervention), weighted by reachability, tenure value and cost, and constrained by configurable guardrails. It then routes each decision through a **threshold gate** that is read from a Snowflake config table on every run. Low-stakes decisions go straight to the **outbox** table. High-stakes ones go to an **approval** table that an approver edits in Metabase. Outcomes are read back from the same source views after an outcome window, and they update the policy posteriors and the next model retrain. Every step is checkpointed and keyed deterministically, so a rerun of the same run id resumes without losing or duplicating anything. A **warehouse abstraction** with a Snowflake backend (production) and a DuckDB backend (tests, local, full-scale timing in the sandbox) runs the same SQL, written in the Snowflake dialect and transpiled with `sqlglot`.

```
 source views (THESIS_SYNTH.* or real views; mapped by config/source_contract.yaml)
        │  SQL pushdown, as_of-bounded
        ▼
 features ──► model.score ──► RISK_SCORES (score, tier, top factors, archetype, model_version, run_id)
                                   │
                                   ▼
 agent: context → guardrails → policy (Thompson) → content (LLM / template) → gate
                                   │                                     │
                     below threshold│                       above threshold│
                                   ▼                                     ▼
                              OUTBOX ◄── approved on next cycle ── APPROVALS (edited in Metabase)
                                   │                                     │ expired/rejected → logged
                                   ▼
               INTERVENTION_LOG (full audit) ◄── outcomes after window ◄── source views
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
            POLICY_STATS (posteriors)     retrain + promote gate (MODEL_REGISTRY)
```

## 2. Resolution of open questions and Scout gaps

The PRD left OQ1–OQ10 open. None of them blocks the design, because each one becomes a **configuration value with a stated default**. The architecture fixes the mechanism, and Andre (or Thesis at onboarding) can change the value without touching code. The defaults below are mine. They are recorded as decisions **A1–A12** so TASKS/WAVES can cite them, and they are listed in `docs/decisions.md` for Andre to confirm.

| # | Open point | Decision (default, configurable) | Why |
|---|---|---|---|
| A1 (OQ1) | Churn label & window | Positive = **voluntary** cancellation with effective date in `(as_of, as_of + 30d]`. Involuntary cancellations (payment failure / dunning end) are excluded from the positive label and reported separately. Skips and pauses are features, not churn. Evaluation = time-based holdout on the **last 3 months** of snapshots. Keys: `label.horizon_days=30`, `label.include_involuntary=false`, `eval.holdout_months=3`. | Monthly churn is the brief's business metric. Interventions cannot rescue involuntary churn. |
| A2 (OQ2) | Active subscriber | Scored population = status in `{ACTIVE, PAUSED, PAST_DUE}` (`scoring.active_statuses`). Paused and past-due add a status feature and are often high-risk. | Paused subscribers are the ones most worth reaching. Excluding them would hide churn. |
| A3 (OQ3) | Archetype ground truth | **Weak labels** from structured signals present in real Thesis data: cancellation reason codes plus support ticket categories (mapping in `config/archetype_labels.yaml`). A multinomial classifier trained on features of churned subscribers predicts the archetype for currently at-risk subscribers. On synthetic data it is judged against the generator's **latent archetype** (per-archetype precision/recall plus a confusion matrix). On real data it is judged against the weak labels of held-out churners. LLM labelling of free-text reasons is left as an optional extension (§11). | Reason codes and ticket categories exist in Shopify/Zendesk syncs. This is cheap, deterministic and testable. |
| A4 (OQ4) | Cadence & deployment | **Daily** cycle (`schedule.cron="0 6 * * *"` UTC). Deployment = **Docker Compose** with `api` and `worker` services on any container host. The cloud target stays open. | Daily matches the data's update rhythm (warehouse syncs). One image runs on any cloud. |
| A5 (OQ5) | Scale | Generator default **200,000 active subscribers** plus churned history (about 350k subscribers in total over 24 months), **≥7.5M quiz rows**. `synth.scale` is a parameter. The throughput test runs at the default. | Puts quiz volume above the brief's 7.3M. The scale parameter lets Thesis's real number be used once it is known. |
| A6 (OQ6) | Guardrails & thresholds | Approval if `risk ≥ 0.85` **or** `cost_usd ≥ 15` **or** `discount_pct ≥ 20`. Contact cap: 1 per 14 days and 3 per 90 days. Offer catalogue: `FREE_SHIPPING`, `PCT_10_NEXT`, `PCT_20_NEXT`, `PCT_25_NEXT_2`, `FREE_SAMPLE_BLEND`. Max discount 25%. Approval expiry 72 h. Tiers: low < 0.20 ≤ medium < 0.45 ≤ high < 0.70 ≤ critical; at-risk = medium and above. | Sized so that more than 70% of dispatched actions are autonomous (R5.5) while costly offers still get reviewed. Checked in the evaluation run. |
| A7 (OQ7) | Outcome window, exploration, holdout | Outcome window **30 days**, response window 7 days. Exploration **allowed** via Thompson sampling with informative priors and `policy.max_explore_share`. Holdout control **10%** of at-risk subscribers, assigned by deterministic hash. | Bandits learn per archetype × intervention (R6.4) without a separate experiment platform. The holdout measures uplift (R6.6). |
| A8 (OQ8) | LLM provider & PII | **Anthropic Claude** (Haiku-class by default, `llm.model`) behind an `LLMClient` protocol, with an OpenAI adapter alongside. **No identifying data is sent**: prompts carry archetype, factor text, tenure bucket, blend names and quiz goal categories only. Greetings use the `{{first_name}}` merge token, which the downstream sender fills. | Satisfies R7.2 without waiting on a policy decision. The provider can be swapped in config. |
| A9 (OQ9) | Submission deliverables | The build produces source, technical docs, deployment guide, test-results report (`docs/test-results.md`, generated), an AI usage log (`docs/ai-usage-log.md`) and a **demo script** (`docs/demo-script.md`). **Andre records the demo video.** | An agent cannot record a video. Everything else falls to the build. |
| A10 (OQ10) | Snowflake environment | Credentials come from env vars only (`SNOWFLAKE_ACCOUNT`, `_USER`, `_PRIVATE_KEY_PATH` or `_PASSWORD`, `_ROLE`, `_WAREHOUSE`, `_DATABASE`). Andre provides the account. `churn bootstrap` creates the schemas, roles and grants. All tests run without Snowflake, on DuckDB plus fakes. Snowflake integration tests are marked `snowflake` and skip unless credentials are set. | The sandbox has no Snowflake access (Scout). Acceptance criteria that need a live account (R8.7, live R1.3 swap) are verified by Andre with the deployment checklist. |
| A11 (Scout) | Orchestration | **Plain Python step runner with warehouse checkpoints**, the "equivalent" the brief allows, instead of LangGraph. | The agent's per-subscriber work is a fixed pipeline whose decision step is the policy. Graph orchestration adds no capability here and makes idempotent replay harder. |
| A12 (Scout) | Config mechanism | Two layers. (1) **Static files** in `config/`: source contract, schema names, model hyperparameters, intervention registry. They change at deploy time and are validated at startup. (2) **Runtime config table** `CHURN_OPS.AGENT_CONFIG`: tiers, thresholds, guardrails, holdout, budgets. It is re-read and validated (pydantic) at the start of every run and snapshotted per run. | R5.2 needs thresholds editable without redeploy, and Metabase is already the edit surface (D3). The contract and registry are deploy-time concerns. |

Other Scout gaps:

- **Greenfield**: §3 defines the whole layout. Wave 1 creates `pyproject.toml`, the test harness and CI config (`.gitlab-ci.yml`, since the repo is on GitLab).
- **No Docker in the sandbox**: the Dockerfile and compose file are written and linted, but R8.7 is verified by Andre using the deployment guide. TASKS must mark it "operator-verified".
- **Synthetic circularity (brief §11 vs D1)**: the generator is a **latent-state simulator** (§5.2). Churn hazard and archetype are latent variables that *emit* events. The model sees only the emitted events through the same contract as the real views, never the latent parameters. The generator has no dependency on `churn.features`, and an import-boundary test enforces this. The evaluation report states plainly that synthetic accuracy is not real-world accuracy, and that the 90-day impact target can only be checked after go-live.
- **Metabase approval safety**: §6.5.

## 3. Repository layout and components

```
pyproject.toml                  # deps, ruff, pytest config (markers: slow, snowflake, scale)
.gitlab-ci.yml                  # lint + pytest (non-snowflake) + test-results artefact
.gitignore                      # .venv, __pycache__, .pytest_cache, artefacts/, *.duckdb
config/
  settings.yaml                 # backend, schema names, model params, schedule, llm
  source_contract.yaml          # logical view → physical name + column mapping (R1.3)
  archetype_labels.yaml         # reason code / ticket category → archetype (A3)
  interventions.yaml            # enabled intervention types (dotted paths) (R8.5)
  runtime_defaults.yaml         # seed values for AGENT_CONFIG (A6/A7)
sql/
  migrations/V001__ops_schema.sql ... # CHURN_OPS DDL, idempotent
  synth/V001__synth_schema.sql        # THESIS_SYNTH source tables + contract views
  features/*.sql                      # one file per domain, Snowflake dialect, :as_of bound
src/churn/
  settings.py          # pydantic-settings: files + env (secrets only from env)
  runtime_config.py    # AGENT_CONFIG → validated RuntimeConfig, CONFIG_SNAPSHOTS pinning, tier assignment (§4.3)
  obs/                 # logging.py (structlog JSON, run_id/component bound), errors.py (taxonomy)
  warehouse/           # base.py Warehouse protocol; snowflake.py; duckdb.py; dialect.py (sqlglot);
                       # migrate.py; bulk.py (stage-then-MERGE writer)
  contract/            # loader.py, validate.py (columns/types present), views.py (renders mapped SQL)
  synth/               # generator.py, latent.py, edge_cases.py, responder.py, profile.py
  features/            # registry.py (feature catalogue + docs), builder.py (runs SQL, returns frame)
  model/               # dataset.py (point-in-time snapshots + labels), train.py, score.py,
                       # explain.py (TreeSHAP → factor text), archetype.py, evaluate.py, registry.py
  agent/               # context.py, guardrails.py, policy.py (Thompson), gate.py, content.py,
                       # rationale.py, cycle.py (agent step orchestration)
  interventions/       # base.py (InterventionType protocol, registry), checkin_email.py,
                       # formulation.py, retention_offer.py, escalation.py
  execution/           # outbox.py, approvals.py
  feedback/            # outcomes.py, policy_stats.py, retrain.py, metrics.py, holdout.py
  llm/                 # client.py (protocol), anthropic.py, openai.py, fake.py, budget.py
  orchestration/       # runner.py (run lifecycle, steps, checkpoints), lock.py, scheduler.py
  api/app.py           # FastAPI
  cli.py               # typer: bootstrap, migrate, synth, train, cycle, evaluate, serve, worker
tests/
  unit/ integration/ (DuckDB end-to-end) scale/ (marker scale) snowflake/ (marker snowflake)
  fixtures/            # hand-built subscribers, dummy_intervention.py
docker/Dockerfile, docker-compose.yml
docs/                  # data-contract.md, model-card.md, agent-logic.md, output-tables.md,
                       # runbook.md, deployment.md, extending.md, decisions.md,
                       # ai-usage-log.md, demo-script.md, test-results.md (generated)
```

Dependencies: `snowflake-connector-python[pandas]`, `duckdb`, `sqlglot`, `pandas`, `pyarrow`, `numpy`, `scikit-learn`, `lightgbm`, `pydantic`, `pydantic-settings`, `pyyaml`, `structlog`, `tenacity`, `fastapi`, `uvicorn`, `typer`, `anthropic`, `openai` (optional extra), `apscheduler`; dev: `pytest`, `httpx`, `ruff`.
LightGBM is chosen because `predict(pred_contrib=True)` gives exact TreeSHAP contributions without the heavy `shap` package. Scikit-learn provides calibration (`CalibratedClassifierCV`, isotonic), the archetype classifier (`LogisticRegression`, multinomial) and the metrics.

### 3.1 Module boundaries (R8.5)

Imports flow one way only: `orchestration → {features, model, agent, execution, feedback} → {warehouse, contract, interventions, llm, obs}`. Specifically:
- `model` (prediction pipeline) never imports `agent` or `execution`.
- `agent` (decision logic) only reads scores and returns `Decision` objects. It never writes to the outbox.
- `execution` (outbox and approvals) knows nothing about how a decision was made.
- `synth` imports only `warehouse` and `contract`.
An `import-linter`-style test (`tests/unit/test_boundaries.py`, AST-based) enforces these rules.

## 4. Warehouse, configuration and the source contract

### 4.1 Warehouse abstraction
`Warehouse` protocol: `query(sql, params) -> DataFrame`, `execute(sql, params)`, `merge_frame(table, df, keys, insert_only=False)`, `transaction()`. Both backends render SQL through `dialect.render(sql, target)`: identity for Snowflake, `sqlglot.transpile(read="snowflake", write="duckdb")` for DuckDB.
- **Snowflake backend**: `snowflake-connector-python`. Writes go through `write_pandas` into a session-scoped temp table followed by `MERGE ... ON keys`, so a write is atomic per batch and idempotent.
- **DuckDB backend**: a file DB (`artefacts/local.duckdb`) or in-memory for tests, with the same MERGE semantics (DuckDB ≥1.1 `INSERT ... ON CONFLICT`, emitted by `bulk.py`).
- Choice: DuckDB, rather than mocks alone, so the real feature SQL, migrations and idempotency are exercised in tests and a full-scale timed cycle can run in the sandbox. A `FaultyWarehouse` wrapper injects failures for R8.1.
- SQL is restricted to constructs that both engines support and that sqlglot transpiles cleanly. CI runs every `sql/` file through the transpiler, and the integration suite executes them.

### 4.2 Source contract (R1.1, R1.3, R1.4)
`config/source_contract.yaml` declares seven **logical views** with the required columns, types, primary/event keys and timestamp column. It also gives the physical object each one maps to (`{database}.{schema}.{view}`) and per-column renames:

| Logical view | Grain | Key columns (abbreviated) |
|---|---|---|
| `subscriptions` | subscription | customer_id, subscription_id, status, started_at, cancelled_at, cancel_type (voluntary/involuntary), cancel_reason_code, price_usd, interval_days |
| `subscription_events` | event | subscription_id, event_id, event_type (skip, pause, resume, modify, blend_switch, reactivate), event_ts |
| `orders` | order line | order_id, customer_id, ordered_at, blend_sku, amount_usd, discount_usd, refund_usd, refunded_at |
| `products` | sku | blend_sku, blend_name, goal_category, price_usd |
| `email_events` | event | customer_id, event_id, event_type (send, open, click, unsubscribe), campaign_id, flow_id, event_ts |
| `support_tickets` | ticket | customer_id, ticket_id, created_at, category, escalated, resolution_hours, sentiment_score |
| `quiz_responses` | answer | customer_id, quiz_session_id, question_key, answer_value, answered_at, recommended_sku |
| `customers_pii` | customer | customer_id, first_name, email, phone, address (**declared PII, never selected by pipeline code**) |

`contract.validate` runs at the start of every cycle and checks that each mapped object exists and has the columns with compatible types. If it does not, the run fails as `ContractError` before anything is written. `contract.views` renders a CTE per logical view (with renames, UTC normalisation and dedup `QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY loaded_at DESC)=1`), and all feature SQL selects from these CTEs, never from physical names. A swap to real views therefore edits only YAML (R1.3). `docs/data-contract.md` lists every item, and the "Switch to production views" section of `docs/deployment.md` walks the mapping steps (R1.4).

**Pseudonymous key (R7.1):** the contract CTEs emit `subscriber_key = SHA2(:pepper || customer_id, 256)`, with the pepper taken from env `CHURN_KEY_PEPPER`. All `CHURN_OPS` tables carry `subscriber_key` only. A single table, `CHURN_PRIVATE.KEY_MAP(subscriber_key, customer_id)`, sits in a separate schema readable only by `CHURN_SENDER` and `CHURN_APP`. The downstream sender joins the outbox through the view `CHURN_PRIVATE.OUTBOX_FOR_SENDER`. Analysts and approvers never need the raw id. The approval view shows enough non-PII context to decide.

### 4.3 Runtime config (R3.4, R4.6, R5.2, fail-safe)
`CHURN_OPS.AGENT_CONFIG(key, value_json, updated_by, updated_at)`, seeded from `runtime_defaults.yaml` by `churn bootstrap` and editable in Metabase. At run start, `churn.runtime_config.load_pinned()` (as built in T5; earlier drafts named it `settings.runtime.load()`) parses every row into a pydantic `RuntimeConfig` and writes the effective config to `CONFIG_SNAPSHOTS(run_id, config_json, valid, errors, gate_mode, agent_halt, created_at)`. `RUNS.config_snapshot_id` points at it. The config has five sections (`gate`, `tiers`, `guardrails`, `holdout`, `outcomes`). Validation never raises: a section that is missing, malformed JSON or out of range becomes `None` and is named in `errors`. Failure rules:
- **Gate thresholds missing or invalid** → `gate.mode = "approve_all"`. Every decision goes to approval, none to the outbox, and the run is marked `DEGRADED` with error class `ConfigError`.
- **Tier boundaries or guardrails invalid** → the agent step **halts** (scores are still written). Without guardrails no safe action exists. As built, `holdout` and `outcomes` sections that are missing or invalid also halt (PRD §7: never silently default). A halt also marks the run `DEGRADED` with error class `ConfigError`. Invalid tiers leave `RISK_SCORES.tier` NULL rather than inventing a tier.
- **Valid** → the snapshot is pinned to the run, and a rerun of the same run id reuses the snapshot, not the current table (R8.2). As built, an invalid snapshot is pinned the same way, so a rerun of a degraded run stays fail-safe.
- `churn bootstrap` inserts only missing keys and keeps edited rows. `churn bootstrap --reset` overwrites them with the defaults.

## 5. Data layer

### 5.1 Schemas
- `THESIS_SYNTH`: synthetic source tables and contract-shaped views (D1).
- `CHURN_OPS`: all outputs, queryable in Metabase (R1.5).
- `CHURN_PRIVATE`: key map and sender view.
- Roles: `CHURN_APP` (read source, write ops), `CHURN_ANALYST` (read ops), `CHURN_APPROVER` (read ops, UPDATE on `APPROVALS` only), `CHURN_SENDER` (read outbox and private).

### 5.2 Synthetic generator (`churn.synth`, R1.1, R1.2)
A latent-state simulator over a 24-month daily timeline, vectorised in numpy and written in chunks:
1. Each customer gets latent traits: price sensitivity, efficacy response, fatigue rate, life-event hazard, and engagement propensity.
2. A daily hazard per archetype process drives behaviour. Price-sensitive customers redeem discounts, file "billing/price" tickets and take refunds. Efficacy-dissatisfied customers switch blends, retake the quiz and file "product not working" tickets. Fatigued customers skip more and their email engagement decays. Life-change customers show abrupt pauses and address-change tickets. Once a customer's latent churn state crosses the threshold, a cancellation is emitted with a reason code drawn noisily from the true archetype (about 25% noise or "other"). Involuntary churn is emitted from an independent payment-failure process.
3. Edge cases are injected at configured rates: no quiz (about 15%), no email history (about 5%), no tickets (most), single order, tenure < 30 days, nulls in non-key columns, about 0.5% duplicate events, and about 1% late-arriving rows (`loaded_at` > `event_ts` + days). Some timestamps are emitted with offsets and normalised to UTC by the contract CTEs.
4. `customers_pii` uses Faker-style fake names, emails and phones, so the PII scan has something real to catch.
5. `synth.profile` writes `docs/data-profile.md` and `CHURN_OPS.DATA_PROFILE`: row counts per view, edge-case shares and the churn-event timeline (R1.2 AC).
6. **Responder** (`synth.responder`, simulation mode only): it reads new outbox rows and, based on the latent archetype and an effect matrix (archetype × intervention → retention uplift, open/click/redeem probabilities), appends email events, orders/redemptions and cancellations to `THESIS_SYNTH`. Outcomes are then measured through the same contract views that real data will use (R6.2). It is a separate command and never runs in production.
7. Simulation clock: `--as-of` sets the cycle date. `churn simulate --days N` advances day by day (responder → cycle), which produces the consecutive runs and the feedback-shift evidence.

The generator is deterministic for a given `synth.seed`, and the generator code does not import `churn.features` or `churn.model` (§2).

### 5.3 Output tables (`CHURN_OPS`, R3.5, R6.1)
All tables are created by `sql/migrations/V00x__*.sql`, applied by `churn migrate` (tracked in `SCHEMA_MIGRATIONS(version, checksum, applied_at)`, `CREATE ... IF NOT EXISTS`, additive only). Primary keys are logical (enforced by MERGE keys, because Snowflake does not enforce PKs).

| Table | Key | Purpose |
|---|---|---|
| `RUNS` | run_id | kind (scheduled/manual/retrain/simulate), as_of, status (RUNNING/SUCCEEDED/DEGRADED/FAILED), model_version, config_snapshot_id, counts (active_in_source, scored, at_risk, decided, dispatched, queued), started/ended, duration_s |
| `RUN_STEPS` | run_id, step, shard | checkpoint: status, rows_written, attempt, error_class, error_msg, updated_at |
| `RUN_LOCKS` | lock_name | holder, heartbeat_at, ttl_s |
| `CONFIG_SNAPSHOTS` | run_id | effective runtime config + validity |
| `FEATURES` | run_id, subscriber_key | feature vector (VARIANT/JSON + typed core columns), retained N days |
| `RISK_SCORES` | run_id, subscriber_key | churn_prob, tier, top_factors (array of {feature, label, direction, contribution, value}, ranked by \|contribution\|; stored as JSON array text in a `VARCHAR` column, added by `V014__risk_scores_top_factors.sql`, so readers `json.loads` / `PARSE_JSON` it), archetype, archetype_conf, archetype_probs (JSON object text, archetype → probability), archetype_low_conf (boolean; the four archetype columns are added by `V007__risk_scores_archetype.sql`), model_version, scored_at; history kept |
| `MODEL_REGISTRY` | model_version | artefact URI, train window, metrics json, status (candidate/promoted/rejected/retired), reason |
| `HOLDOUT_ASSIGNMENTS` | subscriber_key | group (control/treatment), salt_version, assigned_at |
| `INTERVENTION_LOG` | decision_id | full audit (R6.1): subscriber_key, run_id, risk, tier, archetype(+conf), top_factors, intervention_type, params (offer code etc.), content_ref, content_source (llm/template), rationale (structured json + text), policy_scores, guardrail_events, gate_reason, approval_status, execution_status, outbox_id, response, outcome, timestamps (decided/approved/dispatched/outcome_at) |
| `APPROVALS` | decision_id | approver-facing: non-PII context, proposed action and cost, payload_hash, DECISION (PENDING/APPROVED/REJECTED), DECIDED_BY, DECIDED_AT, NOTE, expires_at, processed_at, processed_status |
| `OUTBOX` | outbox_id (= decision_id) | channel, intervention_type, subject, body (with merge tokens), offer_code, discount_pct, escalation_reason, subscriber_key, created_at, status (READY/CANCELLED), cancelled_reason |
| `COVERAGE_GAPS` | run_id, subscriber_key | at-risk but not acted on, with reason (cap, unsubscribed+no channel, holdout, halted, budget) |
| `OUTCOMES` | decision_id | response (opened/clicked/redeemed/none), outcome (retained/churned/unknown), measured_at, window_days |
| `POLICY_STATS` | archetype, intervention_type, context_bucket | alpha, beta (Beta posterior), n, last_updated_run |
| `RUN_METRICS` | run_id, metric | coverage, autonomy_rate, audit_completeness, at-risk count, LLM calls, fallbacks, duration, treated-vs-control churn |
| `EVAL_REPORTS` | report_id | model and archetype evaluation results |

## 6. Interfaces and processing

### 6.1 Run lifecycle (`orchestration.runner`)
`run_id` is deterministic: `"{kind}-{as_of:%Y%m%d}"` for scheduled runs, or caller-supplied for manual runs. Steps:

1. `acquire_lock`. Takes the row in `RUN_LOCKS` via an atomic MERGE with a heartbeat. Stale locks (heartbeat older than TTL) are taken over. A second concurrent cycle exits as "skipped".
2. `load_config` and `validate_contract`.
3. `features`: one SQL per domain, bounded by `event_ts < :as_of`, plus `loaded_at <= :as_of` for training snapshots. Merged into `FEATURES`.
4. `score`: loads the promoted model from the registry, scores, explains, classifies the archetype and assigns the tier. Merged into `RISK_SCORES`.
5. `process_approvals`: expire, approve to outbox, reject (§6.5).
6. `decide`: agent over at-risk subscribers, sharded by `hash(subscriber_key) % shards`, checkpointed per shard.
7. `dispatch`: gate, then outbox or approvals.
8. `outcomes`: closes decisions whose window has elapsed.
9. `policy_update`: posteriors from newly closed outcomes.
10. `metrics`, then `finish`.

Each step records `RUN_STEPS`. On rerun, completed steps or shards are skipped, and incomplete ones redo their work. Because every write is a MERGE on a deterministic key, redoing work cannot duplicate rows (R8.1, R8.2). Pins (model version, config snapshot, policy posteriors as of step 6) are stored at first execution and reused.

**Determinism** (R8.2):
- `decision_id = sha256(run_id | subscriber_key)`.
- Thompson draws are seeded by `hash(run_id, subscriber_key)`.
- Holdout uses `hash(subscriber_key, salt)`.
- LLM output is the only nondeterministic part, so it is generated once and persisted in `INTERVENTION_LOG` *before* dispatch. On rerun the stored content is reused, and decisions already present are not regenerated (`merge_frame(insert_only=True)`).

**Retrain** is a separate run kind (`retrain-YYYYMMDD`). It is triggered weekly (`schedule.retrain_cron`) or when more than `retrain.min_new_outcomes` outcomes have accumulated since the last model, and can also be started through the CLI or API.

### 6.2 Features (R2.1–R2.4)
`features/registry.py` holds the catalogue (name, domain, windows, description, missing policy). It generates `docs/features.md`, so docs cannot drift. The five required leading indicators:
- `email_open_rate_delta_30_90` (open rate over the last 30 days minus the open rate over days 31–90) and `email_click_slope_90` (least-squares slope of weekly clicks).
- `skip_rate_60_vs_prior_60` and `skip_count_trend` (skips per shipment in the recent 60 days vs the prior 60).
- `ticket_escalation_score`: tickets in 30 vs 90 days, escalated count, and sentiment delta.
- `days_since_last_quiz`, plus `has_quiz` and `quiz_retake_count_365`.
- `blend_switch_count_90` / `_365` and `blend_switch_rate_change`.

Other features: tenure_days, orders_90/365, days_since_last_order, refund_count/amount_365, discount_share_365, price change, pause_events_180, status flags, unsubscribed flag, `n_interventions_90d`, `last_intervention_type`, and `days_since_last_intervention` (which feeds the intervention history back into scoring, R6.3). Each domain has at least one multi-window or slope feature (R2.2).

Missing data: counts default to 0. Rates and recencies stay NULL, with explicit `has_<domain>` indicators. LightGBM handles NULLs natively, so no subscriber errors out (R2.4). Brand-new subscribers get tenure-truncated windows normalised by `min(tenure, window)`.

Leakage (R2.3): all SQL is parameterised on `:as_of`, and the test appends post-`as_of` events and asserts equality. Status flags (operator decision, 2026-10-21, T4): `status_paused` comes from the latest pause/resume event before `as_of`. `status_past_due` reads today's `subscriptions.status` only in the live run (`build_features(..., current_status=True)` from `orchestration.runner`), because the source has no status history. It is NULL in training snapshots. Price change is `price_change_90`: the average order amount in the last 90 days minus the average in days 91–180.

### 6.3 Model (R3.1–R3.6, R6.3)
- **Dataset**: monthly point-in-time snapshots over the history, using the same feature SQL with `as_of = snapshot date` and the A1 label. Subscribers active at the snapshot only. Intervention-history features are included.
- **Churn model**: a LightGBM binary classifier, then isotonic calibration on a time-ordered validation fold. Hyperparameters are in `settings.yaml`, with no tuning loop beyond a small grid (NG5).
- **Explanations**: `pred_contrib=True` gives per-feature log-odds contributions plus a bias term. The top-k (k=5, minimum 3 when enough features are non-missing) by |contribution| are rendered to text through registry templates ("Email opens down 42% vs prior 60 days ↑ risk"). Test: contributions + bias equals the raw margin to within 1e-6, and the ranked factors' signs match their direction labels (R3.2).
- **Archetype**: multinomial logistic regression over standardised features. It is trained on churned snapshots that have weak labels (A3) and applied to at-risk subscribers. It outputs probabilities, the argmax and its confidence. `low_confidence = max_prob < archetype.min_conf (0.40) or top1 - top2 < archetype.min_margin (0.10)`, and the agent handles that case (§6.4). As built (T7): the classifier lives in `model/archetype.py` and is stored in the model bundle as `bundle["archetype"]` by `churn train`. It is a pipeline of median imputation, `StandardScaler` and `LogisticRegression` (class-balanced, seeded). The thresholds are the constants `MIN_CONF` and `MIN_MARGIN` in that module, not keys in `settings.yaml`. A weak label is the mapped cancellation reason code of a voluntary churner; when the code is missing or unmapped, it is the most frequent mapped support-ticket category (ties are broken in archetype order). Anything else has no label and is left out of training. Scoring classifies every scored row, which includes every at-risk row, and writes `archetype`, `archetype_conf` (max probability), `archetype_probs` and `archetype_low_conf`. If there are fewer than 20 labelled churners, or only one class, no classifier is fitted and the four columns are NULL.
- **Tiers** come from the runtime config boundaries (R3.4).
- **Evaluation** (`model.evaluate`, `churn evaluate`) runs on the time holdout (A1) and writes `docs/model-card.md`, `docs/eval-report.md` and `EVAL_REPORTS`. Contents: top-decile precision, recall of at-risk tiers among churners (pre-cancellation recall = churners whose last score before cancelling was ≥ the at-risk boundary), PR-AUC and ROC-AUC, calibration, per-archetype precision/recall and confusion matrix, and a check of every target against R3.6 that prints **MISS with a stated reason** when a target is not met.
- **Registry and promotion** (R6.3): artefacts are stored as a joblib file under `@CHURN_OPS.MODEL_STAGE` (Snowflake) or `artefacts/models/` (local), with SHA-256 recorded. A candidate is promoted only if top-decile precision and PR-AUC on the same holdout are ≥ the current model's minus `retrain.tolerance` (0.01). Otherwise it is rejected with the metrics recorded. The first model is promoted if it trains successfully.

### 6.4 Agent (R4.1–R4.6, R6.4)
Per at-risk subscriber, `agent.cycle` builds an `AgentContext` from the score row, features (engagement and tenure), intervention history, holdout group and config, then:

1. **Holdout**: control-group members get a `HOLDOUT` decision row (logged, no action) and are excluded from the coverage denominator (R6.6).
2. **Eligibility and guardrails** (`guardrails.py`, R4.6): each registered intervention type declares `channel`, `cost(context)` and `eligible(context)`. Global guardrails remove arms:
   - contact cap (1/14d, 3/90d, counted from `INTERVENTION_LOG` dispatched and pending rows),
   - email unsubscribed (blocks email-channel arms; escalation stays available),
   - an active offer already outstanding (blocks offers),
   - discount above `max_discount_pct` (offer variant clipped or blocked),
   - an existing pending approval (blocks everything and is logged as `DUPLICATE_PENDING`).
   Every removal writes a `guardrail_events` entry with its reason. If no arm remains, a `COVERAGE_GAPS` row is written with the reason.
3. **Policy** (`policy.py`, contextual Thompson sampling, not a threshold rule, NG6). For each eligible arm `a`:
   - `uplift ~ Beta(α, β)` drawn from `POLICY_STATS[archetype, a, bucket]`, where bucket = tier × tenure band (`<90d`, `90–365d`, `>365d`). With low archetype confidence, the draw is the archetype-probability-weighted mixture over the top 2 archetypes.
   - `utility(a) = P(reach | channel, engagement) × uplift × churn_prob × value(tenure) − cost(a)`, where `P(reach)` comes from the subscriber's own open rate (email arms) or 1.0 (escalation), and `value(tenure)` = expected remaining margin from config by tenure band.
   - Priors come from `runtime_defaults.yaml` and encode domain knowledge: price → offer, efficacy → formulation, fatigue → check-in (skip/cadence suggestion), life change → check-in, or escalation at high value.
   - The highest-utility arm wins. If the archetype has low confidence **and** the tier is critical, escalation is forced (an explicit safe path for the PRD edge case).
   - Exploration is bounded: if the sampled winner differs from the posterior-mean winner and the run's exploration share exceeds `policy.max_explore_share` (0.2), the mean winner is used.
   - Same score with different tenure or archetype gives different utilities, and so different actions (R4.2 AC).
4. **Parameters**: an intervention type returns a concrete action from the context. `formulation.py` uses quiz goals and the recommended SKU when `has_quiz`, otherwise the purchase history (most-reordered blend's goal category plus engagement with goal-specific emails), and records `data_source` = `quiz` / `purchase_history` (R4.3). `retention_offer.py` picks the cheapest catalogue offer whose expected uplift clears the next one's. `escalation.py` sets the reason and a priority.
5. **Content** (`content.py`, R4.4, R7.2): for email-channel arms it builds a prompt from a whitelisted `PromptContext` dataclass (archetype, factor labels, tenure band, blend names, goal categories, offer terms). The dataclass has no free-form fields, and `customer_id`, `subscriber_key` and anything from `customers_pii` are structurally impossible to include. Claude returns subject and body containing `{{first_name}}`. Output passes a validator (length, merge tokens, no email/phone patterns, offer terms match params). On LLM error, timeout, rate limit, validation failure, or `llm.max_calls_per_run` exhausted, the **template fallback** for (intervention, archetype) is used and `content_source = template` is logged. LLM calls run in a bounded thread pool (`llm.concurrency` 8) with `tenacity` retries (3, exponential, honouring `retry-after`).
6. **Rationale** (`rationale.py`): structured JSON (top factors, archetype and confidence, eligible arms with their utility terms, removed arms with reasons, the chosen arm, exploration flag, gate result), plus a one-line deterministic text summary built from that JSON. An LLM is not used for rationale, so it is reproducible and cheap.

**Adding an intervention type** (R8.5): implement `InterventionType` in a new module, list it in `config/interventions.yaml`, and add a prior row in runtime defaults. The test `tests/integration/test_extensibility.py` loads `tests/fixtures/dummy_intervention.py` through config only and shows it chosen and dispatched.

### 6.5 Gate, approvals, outbox (R5.1–R5.4, R4.5, D2, D3)
- **Gate** (`gate.py`): `needs_approval = risk ≥ T_risk or cost ≥ T_cost or discount_pct ≥ T_discount or type in always_approve`. The thresholds are read from the run's config snapshot. In `approve_all` mode (invalid config), everything goes to approval. Escalation is itself a human hand-off, so it goes to the outbox with `channel = human_queue` unless configured otherwise.
- **Autonomous** → `OUTBOX` row (`outbox_id = decision_id`, MERGE insert-only), `INTERVENTION_LOG.execution_status = DISPATCHED`.
- **Needs approval** → `APPROVALS` row (`DECISION = PENDING`, `expires_at = decided_at + expiry_hours`, `payload_hash` = sha256 of the proposed action) and log `approval_status = PENDING`, no outbox row (R5.1 AC).
- **`process_approvals`** (start of each agent step):
  - Normalise `UPPER(TRIM(DECISION))`.
  - `APPROVED` → re-check eligibility at execution time (subscriber still active per the source, still under cap, not unsubscribed for email; if not, `CANCELLED_STALE` with a reason), then MERGE into the outbox and set `processed_at` / `processed_status`.
  - `REJECTED` → log only.
  - Past `expires_at` while `PENDING` → `EXPIRED`, no outbox row (R5.4).
  - Any other value → `INVALID_VALUE` logged in `guardrail_events`, left pending until expiry.
  - Rows with `processed_at` set are final. Later edits are ignored and logged as `LATE_EDIT`.
  - If the context columns were edited (the payload hash of the stored log row no longer matches), the row is treated as rejected and logged as `TAMPERED`.
  - `DECIDED_BY` / `DECIDED_AT` are copied into the log when present, otherwise `unknown` / the time of processing (R5.3 "if available").
- **Permissions**: `CHURN_APPROVER` has SELECT and UPDATE on `APPROVALS` only. Snowflake has no column-level UPDATE grants, which is why the hash check and the agent's ignoring of everything except `DECISION`, `DECIDED_BY` and `NOTE` provide the safety. `docs/runbook.md` gives the Metabase steps (a model or action on `APPROVALS` filtered to `PENDING`).
- **Cancel or reactivate between scoring and dispatch**: the dispatch step re-reads current status from the contract `subscriptions` view for the batch. Subscribers no longer in the active statuses get `CANCELLED_STALE` and no outbox row.
- **Outbox contract** for the downstream sender: the sender reads `status = READY` rows newer than its watermark through `OUTBOX_FOR_SENDER`. The build never deletes outbox rows, only cancels them (`status = CANCELLED`). This contract is documented in `docs/output-tables.md`.

### 6.6 Feedback (R6.2–R6.6)
- `feedback.outcomes`: for log rows with `dispatched_at + outcome_window ≤ as_of`:
  - **response** = email open/click after dispatch within 7 days (from `email_events`), or offer redemption (an order with the offer's discount);
  - **outcome** = `churned` if a voluntary cancellation happened inside the window, `retained` if the subscription is still active at window end, otherwise `unknown`.
  The same is computed for holdout rows, so treated and control can be compared. Outcomes are merged into `OUTCOMES` and copied into `INTERVENTION_LOG.response/outcome`, which stay `PENDING` until then (R6.1, R6.2).
- `feedback.policy_stats`: for each newly closed treated outcome, `α += retained`, `β += churned` per (archetype, type, bucket), optionally with exponential decay `policy.decay`. Uplift is approximated as retention among treated. The control churn rate per archetype × bucket is subtracted in reporting, not in the posterior, to keep the update simple and auditable. Test (R6.4): seed outcomes where `formulation` retains 80% for efficacy against 20% for check-in, and the selection share shifts toward formulation over subsequent runs.
- `feedback.retrain`: intervention and outcome data enter training through the intervention-history features, and treated rows carry their observed label (R6.3).
- `feedback.metrics` per run, written to `RUN_METRICS`:
  - coverage = (dispatched + queued) / (at-risk − holdout), with gaps itemised in `COVERAGE_GAPS` (R6.5);
  - autonomy = dispatched autonomously / (autonomous + approved-and-dispatched) (R5.5);
  - audit completeness = share of decisions with every R6.1 field non-null or `PENDING` (R6.1);
  - treated-vs-control churn rate, with `sql/reports/impact.sql` for Metabase (R6.6).

## 7. Service and deployment (R8.3, R8.6, R8.7, R3.7)
- **FastAPI** (`api/app.py`), auth by `X-API-Key` (env `CHURN_API_KEY`):
  - `GET /health` (process up), `GET /ready` (warehouse reachable, migrations current, promoted model present);
  - `POST /runs {kind, as_of?}` starts the run in a background worker thread, guarded by the run lock, and returns the run_id;
  - `GET /runs`, `GET /runs/{id}` (row plus steps), `GET /runs/{id}/metrics`, `GET /config/runtime` (effective validated config and errors).
  The API does not offer approval endpoints (NG3).
- **Scheduler**: the `worker` service runs `churn worker`, an APScheduler `BlockingScheduler` with the cycle and retrain crons from settings. Overlap is prevented by the warehouse run lock, so several replicas are safe. A missed run (worker down) is caught up once on start if today's scheduled run_id is missing or unfinished. Two consecutive days run without manual action, and the agent consumes the same run's scores inside the same cycle (R3.7).
- **Docker**: one `python:3.12-slim` image with a non-root user, used by both services (`api`: uvicorn; `worker`: scheduler). `docker-compose.yml` reads `.env` (and `.env.example` documents every variable). Snowflake credentials are supplied via env, preferably as a key-pair file mounted as a secret. The local profile uses `CHURN_BACKEND=duckdb` with a volume.
- **Logging**: structlog JSON to stdout with `ts, level, component, run_id, step, event, error_class`.
  - The error taxonomy in `obs/errors.py` is `SourceUnavailable`, `ContractError`, `ConfigError`, `WarehouseWriteError`, `LLMError`, `GuardrailBlocked`, `ModelError`.
  - A logging processor redacts values matching email or phone patterns as a backstop, and only `subscriber_key` is ever bound.

## 8. Failure and edge cases

| Case | Behaviour | Req |
|---|---|---|
| Source view unavailable / contract mismatch at start | `tenacity` retry (3×, backoff), then the run is `FAILED` with `SourceUnavailable`/`ContractError`. Nothing written past the checkpoint. The next trigger resumes. | R8.1 |
| Warehouse write fails mid-batch | The batch MERGE is atomic (temp table then MERGE), so the shard is not marked done. Rerun redoes the shard, and MERGE keys prevent duplicates. | R8.1, R8.2 |
| Process killed | Stale lock taken over after TTL, and the runner resumes from `RUN_STEPS`. Pinned model, config and posteriors are reused. | R8.1 |
| LLM error / timeout / rate limit / budget exhausted | Retry, then template fallback (logged `content_source=template`). The run never blocks on the LLM. | R8.1 |
| Threshold config missing or invalid | `approve_all` mode, run `DEGRADED`. Tier or guardrail config invalid → agent halts. | fail-safe |
| Subscriber with no quiz / email / tickets / single order / new | Null + indicator features. Scored. Formulation falls back. Email arms are down-weighted by `P(reach)`. | R2.4, R4.3 |
| Unsubscribed, active offer, over cap, pending approval | The arm is removed with a reason. If no arm is left, a coverage gap row is written. | R4.6, R6.5 |
| Cancelled/reactivated between scoring and dispatch | Status re-checked at dispatch and approval execution → `CANCELLED_STALE`. A reactivated subscriber simply re-enters scoring next run. | §7 PRD |
| Approval invalid value / edited twice / never answered / tampered | `INVALID_VALUE` (stays pending) / the first value processed is final, later edits `LATE_EDIT` / `EXPIRED` / `TAMPERED` (rejected). | R5.3, R5.4 |
| Duplicate / late-arriving / timezone-skewed rows | Dedup by event id in the contract CTE, UTC normalisation, and an `as_of` bound. Late rows affect only future runs (features are snapshotted per run). | R1.2, R2.3 |
| Retrained model worse | Rejected, with metrics in `MODEL_REGISTRY`. The current model stays. | R6.3 |
| Low or tied archetype confidence | Mixture posterior across the top 2. If the tier is critical, escalation is forced. | §7 PRD |
| Zero active subscribers / model missing | The run succeeds with zero counts and a warning / the run fails as `ModelError` with a runbook pointer (`churn train`). | R3.1 |

## 9. Security, privacy and cost

- **PII**:
  - `customers_pii` is declared in the contract only so the key map can be built. No feature, prompt or log code reads it. A test greps the SQL and code paths and checks the `PromptContext` fields.
  - The automated PII scan (`tests/integration/test_pii_scan.py`) runs a full simulated cycle, then regex-scans captured logs, `FEATURES`, the training frame, `RISK_SCORES`, `INTERVENTION_LOG`, `OUTBOX`, `APPROVALS` and the model artefact bytes. It looks for emails, phones, street-address patterns and every first/last name in the synthetic `customers_pii`, and must find none (R4.4, R7.1, R7.2).
  - Pepper and keys live in env or secrets, never in the repo.
- **Access**: least-privilege roles (§5.1). The API key protects run triggering. Secrets come only from env (pydantic-settings rejects secrets in YAML).
- **Cost**:
  - One daily XS-warehouse cycle with auto-suspend. Features are pushed down into SQL, so only per-subscriber feature rows (about 200k × ~60 columns) leave Snowflake.
  - LLM: Haiku-class model, only for email-channel decisions after guardrails. The expected volume is a few thousand calls a day at the A5 scale, hard-capped by `llm.max_calls_per_run`, with template fallback beyond the cap. Content is cached per (intervention, archetype, factor-set, offer) hash within a run.
  - `FEATURES` retention is configurable (default 35 days). Score history is kept.

## 10. Requirement coverage

| Req | Met by |
|---|---|
| R1.1 | `sql/synth/V001`, contract views for all domains, `docs/data-contract.md` |
| R1.2 | `synth.generator` (A5 scale, edge-case injection, historical churn), `synth.profile` → data profile report |
| R1.3 | `config/source_contract.yaml` + `contract.views`. Integration test points the pipeline at a second DuckDB schema by config only. |
| R1.4 | `docs/data-contract.md` + "Switch to production views" in `docs/deployment.md` |
| R1.5 | Warehouse-only IO. A test asserts no HTTP clients other than the LLM SDKs are imported. All outputs are `CHURN_OPS` tables. |
| R2.1–R2.4 | `features/` SQL + registry, unit tests on fixtures, leakage test, edge-case scoring test |
| R3.1 | Runner records `active_in_source` vs `scored` and fails the step if they differ |
| R3.2 | `model.explain` (TreeSHAP contributions), consistency test |
| R3.3 | `model.archetype` (A3), archetype section of the eval report |
| R3.4 | Tier boundaries in `AGENT_CONFIG`, test changes the config and re-runs |
| R3.5 | `RISK_SCORES` keyed by run, with history. An example query is in `docs/output-tables.md`. |
| R3.6 | `model.evaluate` → `docs/eval-report.md` (honest MISS reporting) |
| R3.7 | `worker` scheduler + `churn simulate` consecutive-run test |
| R4.1–R4.3 | `interventions/*`, `agent.policy`, routing-scenario tests, formulation fallback tests |
| R4.4, R7.1, R7.2 | `agent.content` whitelisted prompt, key pseudonymisation, PII scan tests |
| R4.5 | `execution.outbox` MERGE on `decision_id`, replay test |
| R4.6 | `agent.guardrails`, guardrail tests |
| R5.1–R5.4 | `agent.gate`, `execution.approvals`, runtime config, approval lifecycle tests |
| R5.5, R6.5 | `feedback.metrics` on the simulated evaluation run, reported in test results |
| R6.1 | `INTERVENTION_LOG` schema + completeness metric/test |
| R6.2 | `feedback.outcomes` + `synth.responder` |
| R6.3 | `feedback.retrain` + `model.registry` promote gate |
| R6.4 | `POLICY_STATS` Thompson posteriors + shift test |
| R6.6 | `feedback.holdout`, `sql/reports/impact.sql` |
| R8.1–R8.2 | Runner checkpoints, MERGE keys, lock, `FaultyWarehouse` fault-injection tests |
| R8.3 | `obs.logging`, error taxonomy, log-parse test |
| R8.4 | `tests/scale/test_full_cycle_timed.py` at A5 scale on DuckDB (time recorded in test results). Snowflake timing is operator-run via `churn cycle --timed`. |
| R8.5 | Module boundaries test, intervention registry, `docs/extending.md`, dummy intervention test |
| R8.6 | `api/app.py` + API tests (httpx TestClient) |
| R8.7 | Dockerfile/compose + `docs/deployment.md`. Operator-verified (no Docker in the sandbox). |
| R9.1 | pytest suite + `churn report-tests` → `docs/test-results.md` (from JUnit XML) |
| R9.2 | `docs/*` listed in §3 |
| R9.3 | A9: everything except the video recording, plus `docs/demo-script.md` |

## 11. Deliberately left out

- Sending email or offers, Klaviyo/Shopify/Zendesk connectors, an approval UI, Slack (NG1, NG3, NG4; D2, D3).
- LangGraph (A11), LLM-based archetype labelling of free text (possible later behind `archetype.label_source: llm`), LLM fine-tuning or deep models (NG5).
- Causal uplift modelling beyond the holdout comparison, and per-subscriber real-time (streaming) scoring. Daily batch meets the brief's "scheduled" option.
- A cloud-specific deployment (Terraform etc.). The cloud target is TBD (brief §10), and the image is cloud-neutral.
- Connecting to Thesis's real views (NG8). Only the mapping procedure is delivered.

## 12. Testing strategy

All tests run with `python -m pytest` from the repo root, with no network or credentials. Test layers:
- **Unit** (`tests/unit`): feature functions on hand-built fixtures (each of the five indicators against a known value), leakage, explain consistency, tier assignment from config, guardrails, gate (threshold, `approve_all` fallback), policy (same score with different tenure or archetype gives different actions, low-confidence escalation, seeded determinism), formulation fallback, prompt whitelist, content validator and template fallback, config validation, error taxonomy and log format, module boundaries.
- **Integration** (`tests/integration`, DuckDB with a small seeded synth, about 2k subscribers): end-to-end cycle, contract swap to a second schema, approval lifecycle (approve, reject, expire, invalid, late edit, tamper), outbox replay idempotency (cycle run twice gives identical keys and counts), fault injection (source unavailable, write failure at shard k, LLM timeout, simulated kill between steps, then rerun), outcomes after the window, policy shift over simulated days, retrain promote/reject, PII scan, extensibility via the dummy intervention, and FastAPI endpoints. LLM = `FakeLLM` (scriptable failures).
- **Evaluation** (`-m slow`): a medium synth (about 30k subscribers) trains the model, runs `churn simulate --days 45`, and asserts on coverage, autonomy and audit completeness. Precision and recall are reported, and the targets are asserted with an explicit xfail-with-reason if missed, so a miss is visible and not hidden.
- **Scale** (`-m scale`): A5 full-scale generation and a timed cycle on DuckDB. Asserts duration < cycle interval and records it.
- **Snowflake** (`-m snowflake`): migrations, MERGE semantics and a small cycle against a real account, skipped without `SNOWFLAKE_*` env. Run by Andre.
- **CI** (`.gitlab-ci.yml`): ruff, then the unit and integration tests, then a JUnit artefact, then `docs/test-results.md`.
