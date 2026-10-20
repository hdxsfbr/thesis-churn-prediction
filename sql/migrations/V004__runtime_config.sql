-- Runtime config (R3.4, R5.2): editable table, per-run pinned snapshot, tier on scores.
CREATE TABLE IF NOT EXISTS CHURN_OPS.AGENT_CONFIG (
    key        VARCHAR,
    value_json VARCHAR,
    updated_by VARCHAR,
    updated_at TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CHURN_OPS.CONFIG_SNAPSHOTS (
    run_id      VARCHAR,
    config_json VARCHAR,
    valid       BOOLEAN,
    errors      VARCHAR,
    gate_mode   VARCHAR,
    agent_halt  BOOLEAN,
    created_at  TIMESTAMP_NTZ
);

ALTER TABLE CHURN_OPS.RUNS ADD COLUMN IF NOT EXISTS config_snapshot_id VARCHAR;
ALTER TABLE CHURN_OPS.RUNS ADD COLUMN IF NOT EXISTS error_class VARCHAR;
ALTER TABLE CHURN_OPS.RISK_SCORES ADD COLUMN IF NOT EXISTS tier VARCHAR;
