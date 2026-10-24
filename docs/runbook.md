# Runbook

Operate the cycle from `churn cycle`, `churn worker`, or `POST /runs`. Read `CHURN_OPS.RUNS` for status and `error_class`. The matching playbook is below.

## Daily operation

1. Confirm `GET /ready` is 200 and a model is promoted.
2. Let the worker run at 06:00 UTC, or start `churn cycle --as-of` for a manual day.
3. Read `CHURN_OPS.RUN_METRICS` for coverage, autonomy, and audit completeness.
4. Leave `READY` outbox rows for the sender. Do not edit them in Metabase.

## Metabase approvals

Pending rows are `CHURN_OPS.APPROVALS` where `decision = 'PENDING'`.

Set `decision` to `APPROVED` or `REJECTED`. Fill `decided_by` and `note`. The next cycle reads the row. An approved row is sent only if the subscriber is still active and the payload hash still matches. Editing the subject, body, offer, or discount is `TAMPERED` and is not sent. An unknown `decision` value stays pending and is logged once. A later edit is `LATE_EDIT` and is ignored. Unanswered rows expire after `guardrails.approval_expiry_h`.

## Source unavailable

The cycle retried the source read and failed. Nothing past that checkpoint is written. Restore the source views, then rerun the same `run_id`. The run resumes from `RUN_STEPS`.

## Contract error

`contract.validate` rejected a mapped object or column. Fix `config/source_contract.yaml` or the warehouse object. Do not edit feature SQL to point at a physical table. Rerun after the contract matches.

## Config error

`AGENT_CONFIG` is missing or invalid. The run is `DEGRADED`. An invalid gate approves everything. Invalid tiers or guardrails halt the agent. Correct the JSON in `AGENT_CONFIG` and start the next run. The pinned snapshot on this run does not change.

## Warehouse write error

A shard failed mid-write and is not marked done. Rerun the same `run_id`. Completed shards are skipped. Merge keys stop duplicate rows.

## LLM error

The model call failed and the template was used, or a shard stopped before dispatch. Rerun the same `run_id`. Stored subject and body are reused. Confirm `content_source` on `INTERVENTION_LOG`.

## Guardrail blocked

No arm was left, so the subscriber is in `COVERAGE_GAPS` with the reason. This is not a failed run. Widen a cap or the catalogue only by editing `AGENT_CONFIG`.

## Model error

No promoted model, or the artefact does not match its sha256. Run `churn train`, confirm `MODEL_REGISTRY.status = promoted`, then rerun the cycle.
