-- Duration and, later, the T19 run metrics. V017 is coverage gaps.
CREATE TABLE IF NOT EXISTS CHURN_OPS.RUN_METRICS (
    run_id      VARCHAR NOT NULL,
    metric      VARCHAR NOT NULL,
    value       DOUBLE,
    recorded_at TIMESTAMP_NTZ,
    PRIMARY KEY (run_id, metric)
);
