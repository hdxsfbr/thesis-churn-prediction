# Demo script

About eight minutes. Andre records it from a clean checkout of main. The warehouse is synthetic. Do not open Metabase, do not load a live customer extract, and do not say the image was deployed.

## 1. What the cycle does (about one minute)

Show the README, then `docs/decisions.md`. Say the cycle scores active subscribers and writes an outbox. It does not send mail. The 90-day churn reduction is not measured. The Snowflake smoke passed on 2026-10-25: tiny synthetic data, one cycle, SUCCEEDED. This demo still uses DuckDB.

## 2. Score a tiny population (about two minutes)

From the repository root, with `libgomp1` installed:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/churn bootstrap
.venv/bin/churn synth --scale tiny
.venv/bin/churn train --as-of 2025-05-31
.venv/bin/churn cycle --as-of 2025-06-30
```

Read the printed scored count aloud. It is the line `scheduled-20250630: scored N of M active subscribers`.

## 3. One score (about one minute)

```bash
.venv/bin/python <<'PY'
import duckdb
con = duckdb.connect("artefacts/local.duckdb")
print(con.sql("""
SELECT subscriber_key, churn_prob, tier, archetype, top_factors
FROM CHURN_OPS.RISK_SCORES
WHERE run_id = 'scheduled-20250630'
LIMIT 1
"""))
PY
```

Point at the probability, the tier, the archetype, and the top factors.

## 4. One ready email (about one minute)

```bash
.venv/bin/python <<'PY'
import duckdb
con = duckdb.connect("artefacts/local.duckdb")
print(con.sql("""
SELECT outbox_id, status, subject, body
FROM CHURN_OPS.OUTBOX
WHERE status = 'READY'
LIMIT 1
"""))
print(con.sql("""
SELECT subscriber_key, params
FROM CHURN_OPS.INTERVENTION_LOG
WHERE run_id = 'scheduled-20250630' AND intervention_type = 'formulation'
LIMIT 1
"""))
PY
```

Show `{{first_name}}` still in the body. If a formulation row is present, show `data_source` (`quiz` or `purchase_history`) and the goal or recommended SKU in `params`.

## 5. One approval (about one and a half minutes)

```bash
.venv/bin/python <<'PY'
import duckdb
con = duckdb.connect("artefacts/local.duckdb")
print(con.sql("""
SELECT decision_id, decision
FROM CHURN_OPS.APPROVALS
WHERE decision = 'PENDING'
LIMIT 1
"""))
PY
```

Approve that one row. Replace the id below, then run the cycle again.

```bash
.venv/bin/python <<'PY'
import duckdb
con = duckdb.connect("artefacts/local.duckdb")
con.execute("""
UPDATE CHURN_OPS.APPROVALS
SET decision = 'APPROVED', decided_by = 'demo'
WHERE decision_id = 'PASTE-THE-ID'
""")
PY
.venv/bin/churn cycle --as-of 2025-06-30
```

Show the one new outbox row for that decision. A pending row had no outbox row before this rerun.

## 6. Holdout, coverage, autonomy (about one minute)

```bash
.venv/bin/python <<'PY'
import duckdb
con = duckdb.connect("artefacts/local.duckdb")
print(con.sql("""
SELECT l.subscriber_key, l.intervention_type
FROM CHURN_OPS.INTERVENTION_LOG l
LEFT JOIN CHURN_OPS.OUTBOX o ON o.outbox_id = l.decision_id
WHERE l.run_id = 'scheduled-20250630'
  AND l.intervention_type = 'HOLDOUT'
  AND o.outbox_id IS NULL
LIMIT 1
"""))
print(con.sql("""
SELECT metric, value
FROM CHURN_OPS.RUN_METRICS
WHERE run_id = 'scheduled-20250630'
  AND metric IN ('coverage', 'autonomy_rate')
"""))
PY
```

Say the holdout row has no outbox row. Read the coverage and autonomy values. Autonomy leaves escalations out of both numbers.

## 7. Close (about thirty seconds)

Point at `docs/deployment.md`. Say the clean-machine checklist is the deploy path. The image `churn-submission:submit` was built and was not started as a cycle. The Snowflake smoke passed on 2026-10-25 from the local checkout, not from that image. The 90-day reduction is not measured. Stop the recording.
