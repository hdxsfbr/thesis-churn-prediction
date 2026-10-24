# Demo script

Andre records the video. The steps below are the script. They use the synthetic warehouse, not a live customer extract.

## Scene 1 — start

Show the README, then `docs/decisions.md`. Say that the cycle scores active subscribers and writes an outbox rather than sending mail itself.

## Scene 2 — score

From a clean checkout, with `libgomp1` installed:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/churn bootstrap
.venv/bin/churn synth --scale tiny
.venv/bin/churn train --as-of 2025-05-31
.venv/bin/churn cycle --as-of 2025-06-30
```

Show the printed scored count and a few `CHURN_OPS.RISK_SCORES` rows: `churn_prob`, `tier`, `archetype`, and `top_factors`.

## Scene 3 — decide

Show `CHURN_OPS.OUTBOX` rows with `status = READY` and a `{{first_name}}` token. Show one `CHURN_OPS.APPROVALS` row still `PENDING`. In Metabase, or with an `UPDATE`, set `decision` to `APPROVED` and `decided_by`. Run the cycle once more and show the outbox row that approval created.

## Scene 4 — metrics and holdout

Show `CHURN_OPS.RUN_METRICS` for the run and one `HOLDOUT` row in `INTERVENTION_LOG` that has no outbox row.

## Scene 5 — close

Point at `docs/deployment.md` and say the live Snowflake cycle and this recording are the operator checks. Stop the recording.
