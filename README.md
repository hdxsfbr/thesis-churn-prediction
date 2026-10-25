# thesis-churn-prediction

Churn prediction and intervention agent for subscribers of [Thesis](https://takethesis.com). It scores every
active subscriber every day, explains each score, classifies the likely reason for leaving, and decides on a
retention action. The goal, scope and requirements are in [`docs/PRD.md`](docs/PRD.md).

What the finished system does (per the PRD):

- **Daily scoring** of every active subscriber with a calibrated LightGBM model.
- **Top explaining factors** stored with each score.
- **Churn archetype classifier**: the reason behind the risk, with a confidence and a low-confidence flag.
- **Risk tiers** driven by a runtime config table (`CHURN_OPS.AGENT_CONFIG`), editable without a deploy.
- **Intervention agent** that only writes to a Snowflake outbox table. Nothing is sent directly; a separate
  sender reads the outbox.
- **Human approval for high-stakes interventions**, through a Snowflake approval table.
- Every decision is logged with its full context for audit.

## Current status

`main` scores every active subscriber, writes an outbox, and queues high-stakes actions for approval. The enabled interventions are check-in email, formulation, retention offer, and escalation. `churn serve` and `churn worker` run the API and the daily schedule. Start with [`docs/deployment.md`](docs/deployment.md), [`docs/decisions.md`](docs/decisions.md), and [`docs/demo-script.md`](docs/demo-script.md).

The Snowflake backend is implemented. DuckDB is the local default. A tiny live cycle passed on a Snowflake trial on 2026-10-25. The demo video is recorded from the script; it is not in this repo.

The data in this repository is synthetic (`THESIS_SYNTH`). Thesis's real views are a source-contract edit (see [Configuration](#configuration)).

The plan history is [`docs/plans/churn-prediction/WAVES.md`](docs/plans/churn-prediction/WAVES.md). The table below is the early ticket log, not the whole system.

| Ticket | What exists |
|---|---|
| T1 | Skeleton: synth, train, score into `CHURN_OPS.RISK_SCORES`, `RUNS` row, warehouse protocol, MERGE writer, migrations |
| T3 | Source contract: validation, dedup, UTC normalisation, pseudonymous keys, swap by config |
| T4 | Feature catalogue in SQL (leading indicators, multi-window trends, no leakage, missing-domain handling) |
| T5 | Runtime config (`AGENT_CONFIG`) driving risk tiers, with fail-safe validation |
| T6 | Top explaining factors (TreeSHAP) on every score |
| T7 | Archetype, confidence and low-confidence flag on scores |
| T10 | Agent thin path: at-risk subscribers get a check-in email row in `OUTBOX` and `INTERVENTION_LOG` |
| T14 | Threshold gate and approval table: decisions above the risk/cost/discount thresholds go to `CHURN_OPS.APPROVALS` instead of the outbox. Approver edits are processed at the start of each cycle. Subscribers who cancelled since scoring are not sent anything |


## Architecture overview

Package layout under `src/churn/`:

- `settings.py`: settings from `config/settings.yaml` plus environment overrides.
- `runtime_config.py`: seeds and reads `CHURN_OPS.AGENT_CONFIG` (tiers and other runtime values).
- `contract/`: loads the source contract, creates one normalised view per logical source view (rename, UTC,
  dedup, pseudonymous key) and validates the source before each cycle.
- `features/`: feature registry and SQL builder. The SQL is under `sql/features/`.
- `synth/`: synthetic subscribers, orders and other source domains, with latent archetypes.
- `model/`: dataset, training and calibration, scoring, explanations (`explain.py`), archetype classifier
  (`archetype.py`) and model registry.
- `agent/`, `interventions/`, `execution/`: decision step (returns decisions, never writes), the intervention
  registry loaded from `config/interventions.yaml`, the threshold gate (`agent/gate.py`), the outbox writer and
  the approval lifecycle (`execution/approvals.py`).
- `orchestration/runner.py`: the daily cycle.
- `warehouse/`: warehouse protocol, DuckDB implementation, bulk and MERGE writes, and the migration runner.
- `cli.py`: the `churn` command.

Daily cycle (`churn cycle`):

1. Validate the source contract.
2. Build features in SQL as of a cutoff date (`as_of`), so no data after the cutoff is used.
3. Score every active subscriber with the promoted model, adding top factors, archetype and risk tier from the
   pinned runtime config.
4. Write `CHURN_OPS.RISK_SCORES` and a `RUNS` row, and fill the `CHURN_PRIVATE.KEY_MAP` table.
5. Let the agent decide for at-risk subscribers and write `OUTBOX` and `INTERVENTION_LOG` in one transaction.
   Rows are keyed deterministically, so replaying a run creates no duplicates.

If the runtime config is invalid, the cycle fails safe: an invalid gate config means nothing is dispatched
(the run is marked `DEGRADED`), and other invalid config halts the agent.

Warehouse targets: DuckDB locally (the default, file `artefacts/local.duckdb`), or Snowflake when
`CHURN_BACKEND=snowflake`. SQL is written to be portable between the two.

Migrations are `sql/migrations/V###__name.sql`. Each is applied once and its SHA-256 checksum is recorded in
`CHURN_OPS.SCHEMA_MIGRATIONS`. Editing a shipped migration makes the next run fail with a `MigrationError`, so
schema changes always go in a new migration with the next version number. Migrations are additive.

## Getting started

Requirements:

- Python 3.12 or newer.
- The system library `libgomp1` (needed by LightGBM). On Debian or Ubuntu: `apt-get install libgomp1`.

Install, from the repository root:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # ".[dev]" adds pytest and ruff; use -e . for runtime only
```

This installs the `churn` command (`churn = churn.cli:app`).

### CLI commands

| Command | What it does |
|---|---|
| `churn migrate` | Apply pending schema migrations |
| `churn bootstrap [--reset]` | Migrate, then seed `CHURN_OPS.AGENT_CONFIG` from `config/runtime_defaults.yaml`. Edited rows are kept unless `--reset` is given |
| `churn synth [--scale tiny\|small\|default]` | Write synthetic subscriptions and orders (and the other source domains) into `THESIS_SYNTH` |
| `churn train [--as-of YYYY-MM-DD]` | Fit and register a calibrated model on point-in-time snapshots |
| `churn cycle --as-of YYYY-MM-DD [--run-id ID]` | Score every active subscriber into `CHURN_OPS.RISK_SCORES` and run the agent. The run id defaults to `scheduled-YYYYMMDD` |
| `churn serve` | Serve the run API on `127.0.0.1:8000`. `CHURN_API_KEY` is required |
| `churn worker` | Catch up today's cycle, then block on the daily and weekly crons |
| `churn report-tests --junit junit.xml` | Write `docs/test-results.md` from a pytest JUnit file |

Every command except `migrate` itself also applies pending migrations first. Run `churn --help` for the list.

### Local end-to-end example (DuckDB)

```bash
.venv/bin/churn bootstrap                       # migrate and seed AGENT_CONFIG
.venv/bin/churn synth --scale tiny              # 1,500 synthetic subscriptions
.venv/bin/churn train --as-of 2025-05-31        # fit, calibrate and promote a model
.venv/bin/churn cycle --as-of 2025-06-30        # score and write to CHURN_OPS
```

The cycle prints `scheduled-20250630: scored N of M active subscribers`. The results are in the DuckDB file
`artefacts/local.duckdb` (for example `CHURN_OPS.RISK_SCORES`, `CHURN_OPS.RUNS`, `CHURN_OPS.OUTBOX`).
The synthetic data ends on 2025-06-30, so a training date earlier than that leaves room for labels.

## Configuration

Settings live in `config/settings.yaml` (backend, DuckDB path, schema names, scoring statuses, label horizon,
synth scales, model and LightGBM parameters). Environment variables:

| Variable | Effect |
|---|---|
| `CHURN_CONFIG_DIR` | Directory holding the config files (default: `config/` in the repo) |
| `CHURN_DUCKDB_PATH` | DuckDB file path (overrides `warehouse.path`) |
| `CHURN_CONTRACT_PATH` | Source contract file (overrides `contract_path`) |
| `CHURN_KEY_PEPPER` | Secret pepper for the pseudonymous key. Env only, never in the repo. Defaults to a non-secret dev value, so **set it for any real data** |

**Source contract**: `config/source_contract.yaml` maps each logical view (subscriptions, orders and the other
domains) to a physical object and its columns, with a dedup key and `loaded_at` column. Pointing the pipeline at
Thesis's real views means editing this file only. Format, validation and logical columns:
[`docs/data-contract.md`](docs/data-contract.md).

**Runtime defaults / AGENT_CONFIG**: `config/runtime_defaults.yaml` holds the seed values for
`CHURN_OPS.AGENT_CONFIG`: risk tier boundaries (medium 0.20, high 0.45, critical 0.70) and which tiers count as
at-risk, plus approval gate, guardrail, holdout and outcome keys. Today the tier keys, the `gate` thresholds
(`t_risk`, `t_cost_usd`, `t_discount_pct`, `always_approve`) and `guardrails.approval_expiry_h` drive the cycle.
The holdout, the policy priors, and the outcome windows also drive the cycle. If the `gate` section is missing or invalid, every decision goes to
approval. After `churn bootstrap` the table is the source of truth. Each run pins a
snapshot of it, so a change takes effect on the next run.

**Approving interventions**: pending rows in `CHURN_OPS.APPROVALS` have `decision = 'PENDING'`. To answer one, set
`decision` to `APPROVED` or `REJECTED`, and optionally fill in `decided_by`, `decided_at` and `note`. The next cycle acts on it.
An approved row gets exactly one `OUTBOX` row, after the agent checks that the subscriber is still active. The
agent ignores edits to any other column, and a changed proposal (subject, body, offer, discount) counts as
`TAMPERED` and is rejected. An invalid value is logged and the row stays pending. Edits after processing are
logged as `LATE_EDIT` and ignored. Unanswered rows expire after `approval_expiry_h` (72 h by default). The outcome
is copied into `INTERVENTION_LOG` (`approval_status`, `decided_by`, `approval_note`, `guardrail_events`).

Other config files: `config/archetype_labels.yaml` (weak labels for the archetype classifier) and
`config/interventions.yaml` (check-in email, formulation, retention offer, escalation).

## Testing and CI

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest
```

Tests are under `tests/unit` and `tests/integration` and run on DuckDB with synthetic data. By default pytest
skips tests marked `slow`, `scale` and `snowflake` (the last needs a live Snowflake account). LightGBM and
TreeSHAP tests need `libgomp1`.

`.gitlab-ci.yml` uses `python:3.12-slim`, installs `libgomp1` and `pip install -e ".[dev]"`, then runs two stages:

1. `lint`: `ruff check src tests`
2. `test`: `python -m pytest --junitxml=junit.xml`, then `churn report-tests` publishes `docs/test-results.md`

## Data and privacy

- **Synthetic data only for now.** No real Thesis data is used by this repository.
- Subscribers are identified by a **pseudonymous key**: `subscriber_key = SHA2(pepper || customer_id, 256)`.
- The raw `customer_id` is **hidden by default**: the contract views do not expose it, and no `CHURN_OPS` table
  has a `customer_id` column.
- The only table with the raw id is `CHURN_PRIVATE.KEY_MAP(subscriber_key, customer_id)`. In Snowflake, grant that
  schema to the sender and app roles only.
- Outbox rows carry merge tokens such as `{{first_name}}`, not names or email addresses. The sender fills them
  in.

## Documentation

- [Architecture](docs/plans/churn-prediction/ARCHITECTURE.md) and the confirmation list in [decisions](docs/decisions.md)
- [Source data contract](docs/data-contract.md)
- [Model card](docs/model-card.md)
- [Agent logic](docs/agent-logic.md)
- [Output tables](docs/output-tables.md)
- [Runbook](docs/runbook.md)
- [Deployment](docs/deployment.md)
- [AI usage log](docs/ai-usage-log.md)
- [Demo script](docs/demo-script.md)
- [Test results](docs/test-results.md)
- [Product requirements (`docs/PRD.md`)](docs/PRD.md)
- [Feature catalogue](docs/features.md)
- [`CLAUDE.md`](CLAUDE.md): lessons for contributors and agents working in this repository
