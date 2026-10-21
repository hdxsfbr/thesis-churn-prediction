-- Agent output tables (R4.5, R6.1). Keys are logical: INTERVENTION_LOG(decision_id), OUTBOX(outbox_id = decision_id).
CREATE TABLE IF NOT EXISTS CHURN_OPS.INTERVENTION_LOG (
    decision_id       VARCHAR,
    subscriber_key    VARCHAR,
    run_id            VARCHAR,
    risk              DOUBLE,
    tier              VARCHAR,
    archetype         VARCHAR,
    archetype_conf    DOUBLE,
    top_factors       VARCHAR,
    intervention_type VARCHAR,
    params            VARCHAR,
    content_ref       VARCHAR,
    content_source    VARCHAR,
    rationale         VARCHAR,
    policy_scores     VARCHAR,
    guardrail_events  VARCHAR,
    gate_reason       VARCHAR,
    approval_status   VARCHAR,
    execution_status  VARCHAR,
    outbox_id         VARCHAR,
    response          VARCHAR,
    outcome           VARCHAR,
    decided_at        TIMESTAMP_NTZ,
    approved_at       TIMESTAMP_NTZ,
    dispatched_at     TIMESTAMP_NTZ,
    outcome_at        TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CHURN_OPS.OUTBOX (
    outbox_id         VARCHAR,
    channel           VARCHAR,
    intervention_type VARCHAR,
    subject           VARCHAR,
    body              VARCHAR,
    offer_code        VARCHAR,
    discount_pct      DOUBLE,
    escalation_reason VARCHAR,
    subscriber_key    VARCHAR,
    created_at        TIMESTAMP_NTZ,
    status            VARCHAR,
    cancelled_reason  VARCHAR
);

ALTER TABLE CHURN_OPS.RUNS ADD COLUMN IF NOT EXISTS at_risk BIGINT;
ALTER TABLE CHURN_OPS.RUNS ADD COLUMN IF NOT EXISTS decided BIGINT;
ALTER TABLE CHURN_OPS.RUNS ADD COLUMN IF NOT EXISTS dispatched BIGINT;
