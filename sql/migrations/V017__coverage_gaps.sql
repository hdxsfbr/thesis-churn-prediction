-- At-risk subscribers the agent did not act on (R4.6, R6.5). Logical key: (run_id, subscriber_key).
-- V007 is already the archetype columns on RISK_SCORES, so this table takes the next free version.
CREATE TABLE IF NOT EXISTS CHURN_OPS.COVERAGE_GAPS (
    run_id           VARCHAR,
    subscriber_key   VARCHAR,
    reason           VARCHAR,
    guardrail_events VARCHAR,
    created_at       TIMESTAMP_NTZ
);
