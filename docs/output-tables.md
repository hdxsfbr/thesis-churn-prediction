# CHURN_OPS tables

Columns below are the ones created by `sql/migrations`. A later migration only adds columns. The sender never reads `CHURN_PRIVATE`.

## RUNS

One row per cycle. `run_id`, `kind`, `as_of`, `status`, `model_version`, `active_in_source`, `scored`, `started_at`, `ended_at`, `duration_s`, `error_msg`, `config_snapshot_id`, `error_class`, `at_risk`, `decided`, `dispatched`, `queued`.

`status` is `RUNNING`, `SUCCEEDED`, `DEGRADED`, or `FAILED`. `at_risk` counts every at-risk subscriber, including holdout.

## RISK_SCORES

Score history. One row per `(run_id, subscriber_key)`. `run_id`, `subscriber_key`, `as_of`, `churn_prob`, `model_version`, `scored_at`, `tier`, `archetype`, `archetype_conf`, `archetype_probs`, `archetype_low_conf`, `top_factors`.

`top_factors` is a JSON array of feature, label, direction, contribution, and value.

## MODEL_REGISTRY

`model_version`, `artefact_uri`, `sha256`, `train_start`, `train_end`, `metrics_json`, `status`, `reason`, `created_at`.

`status` is `candidate`, `promoted`, `rejected`, or `retired`.

## AGENT_CONFIG

Runtime knobs, edited without a deploy. `key`, `value_json`, `updated_by`, `updated_at`.

The keys are `tiers`, `gate`, `guardrails`, `holdout`, `policy`, and `outcomes`.

## CONFIG_SNAPSHOTS

The config a run actually used. `run_id`, `config_json`, `valid`, `errors`, `gate_mode`, `agent_halt`, `created_at`.

## EVAL_REPORTS

`report_id`, `model_version`, `holdout_start`, `holdout_end`, `label_window_days`, `n_rows`, `n_positives`, `metrics_json`, `targets_json`, `archetype_json`, `calibration_json`, `report_md`, `created_at`.

## INTERVENTION_LOG

The audit row for one decision. `decision_id`, `subscriber_key`, `run_id`, `risk`, `tier`, `archetype`, `archetype_conf`, `top_factors`, `intervention_type`, `params`, `content_ref`, `content_source`, `rationale`, `policy_scores`, `guardrail_events`, `gate_reason`, `approval_status`, `execution_status`, `outbox_id`, `response`, `outcome`, `decided_at`, `approved_at`, `dispatched_at`, `outcome_at`, `payload_hash`, `decided_by`, `approval_note`, `approval_expires_at`, `approval_grace_until`.

`response` and `outcome` stay `PENDING` until the outcome window closes.

## OUTBOX

What a sender is allowed to send. `outbox_id`, `channel`, `intervention_type`, `subject`, `body`, `offer_code`, `discount_pct`, `escalation_reason`, `subscriber_key`, `created_at`, `status`, `cancelled_reason`.

`status` is `READY` or `CANCELLED`. `outbox_id` equals `decision_id`. The body may contain `{{first_name}}`. It does not contain an email address or a phone number.

### Sender contract

A sender reads only `READY` rows. The columns it may use are `outbox_id`, `channel`, `intervention_type`, `subject`, `body`, `offer_code`, `discount_pct`, `escalation_reason`, `subscriber_key`, and `created_at`. When migration `V013` is applied, `CHURN_OPS.OUTBOX_FOR_SENDER` is that projection. The sender does not update `OUTBOX` and does not read `APPROVALS`.

## APPROVALS

The Metabase edit surface. `decision_id`, `run_id`, `subscriber_key`, `tier`, `risk`, `archetype`, `summary`, `gate_reason`, `channel`, `intervention_type`, `subject`, `body`, `offer_code`, `discount_pct`, `escalation_reason`, `cost_usd`, `payload_hash`, `created_at`, `expires_at`, `decision`, `decided_by`, `decided_at`, `note`, `processed_at`, `processed_status`, `processed_decision`, `last_seen_decision`.

An approver may set `decision`, `decided_by`, `decided_at`, and `note`. Any other edit is ignored or logged as tamper.

## DATA_PROFILE

`profiled_at`, `section`, `item`, `value`, `scale`, `seed`.

## POLICY_STATS

`archetype`, `intervention_type`, `context_bucket`, `alpha`, `beta`, `n`, `last_updated_run`.

The stored alpha and beta include the configured prior.

## COVERAGE_GAPS

At-risk subscribers with no action. `run_id`, `subscriber_key`, `reason`, `guardrail_events`, `created_at`.

## RUN_METRICS

`run_id`, `metric`, `value`, `recorded_at`.

Metrics include `duration_s`, `coverage`, `autonomy`, `audit_completeness`, `llm_calls`, and `llm_fallbacks`.

## HOLDOUT_ASSIGNMENTS

`subscriber_key`, `arm_group`, `salt_version`, `assigned_at`.

`arm_group` is `control` or `treatment`. The assignment sticks.

## OUTCOMES

`decision_id`, `response`, `outcome`, `measured_at`, `window_days`.

## RUN_STEPS

Checkpoints. `run_id`, `step`, `shard`, `status`, `rows_written`, `attempt`, `error_class`, `error_msg`, `updated_at`.

## RUN_LOCKS

`lock_name`, `holder`, `heartbeat_at`, `ttl_s`.

The cycle lock name is `cycle`.

## Metabase examples

Score history for one subscriber:

```sql
SELECT as_of, churn_prob, tier, archetype, archetype_conf
FROM CHURN_OPS.RISK_SCORES
WHERE subscriber_key = {{subscriber_key}}
ORDER BY as_of;
```

Pending approvals:

```sql
SELECT decision_id, subscriber_key, tier, risk, intervention_type, cost_usd, expires_at
FROM CHURN_OPS.APPROVALS
WHERE decision = 'PENDING'
ORDER BY risk DESC;
```

Ready outbox rows:

```sql
SELECT outbox_id, channel, intervention_type, subject, offer_code, discount_pct, subscriber_key
FROM CHURN_OPS.OUTBOX
WHERE status = 'READY'
ORDER BY created_at;
```
