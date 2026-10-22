-- Approval queue (R5.1-R5.4, ARCHITECTURE 6.5). Key is logical: APPROVALS(decision_id).
-- The approver edits decision / decided_by / decided_at / note (CHURN_APPROVER has UPDATE on this table only).
-- Everything else is context for the approver; the agent re-checks it against the hash kept in INTERVENTION_LOG.
CREATE TABLE IF NOT EXISTS CHURN_OPS.APPROVALS (
    decision_id        VARCHAR,
    run_id             VARCHAR,
    subscriber_key     VARCHAR,
    tier               VARCHAR,
    risk               DOUBLE,
    archetype          VARCHAR,
    summary            VARCHAR,
    gate_reason        VARCHAR,
    channel            VARCHAR,
    intervention_type  VARCHAR,
    subject            VARCHAR,
    body               VARCHAR,
    offer_code         VARCHAR,
    discount_pct       DOUBLE,
    escalation_reason  VARCHAR,
    cost_usd           DOUBLE,
    payload_hash       VARCHAR,
    created_at         TIMESTAMP_NTZ,
    expires_at         TIMESTAMP_NTZ,
    decision           VARCHAR,
    decided_by         VARCHAR,
    decided_at         TIMESTAMP_NTZ,
    note               VARCHAR,
    processed_at       TIMESTAMP_NTZ,
    processed_status   VARCHAR,
    processed_decision VARCHAR,
    last_seen_decision VARCHAR
);

-- The audit log keeps the hash of the proposed action and who decided it.
ALTER TABLE CHURN_OPS.INTERVENTION_LOG ADD COLUMN IF NOT EXISTS payload_hash VARCHAR;
ALTER TABLE CHURN_OPS.INTERVENTION_LOG ADD COLUMN IF NOT EXISTS decided_by VARCHAR;
ALTER TABLE CHURN_OPS.INTERVENTION_LOG ADD COLUMN IF NOT EXISTS approval_note VARCHAR;
-- Trusted copy of the approval deadline (APPROVALS.expires_at is approver-editable and ignored by the agent).
ALTER TABLE CHURN_OPS.INTERVENTION_LOG ADD COLUMN IF NOT EXISTS approval_expires_at TIMESTAMP_NTZ;
-- Trusted end of the grace period (deadline + guardrails.approval_grace_h), pinned at dispatch.
ALTER TABLE CHURN_OPS.INTERVENTION_LOG ADD COLUMN IF NOT EXISTS approval_grace_until TIMESTAMP_NTZ;

-- Decisions sent to approval in this run, next to dispatched (rows written to the outbox).
ALTER TABLE CHURN_OPS.RUNS ADD COLUMN IF NOT EXISTS queued BIGINT;
