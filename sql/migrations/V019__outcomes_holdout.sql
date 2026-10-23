-- Holdout assignments and closed outcomes (R6.2, R6.6). V010 is already taken.
-- arm_group is control or treatment. The name avoids the reserved word GROUP, which the MERGE writer does not quote.
CREATE TABLE IF NOT EXISTS CHURN_OPS.HOLDOUT_ASSIGNMENTS (
    subscriber_key VARCHAR NOT NULL,
    arm_group      VARCHAR NOT NULL,
    salt_version   VARCHAR,
    assigned_at    TIMESTAMP_NTZ,
    PRIMARY KEY (subscriber_key)
);

CREATE TABLE IF NOT EXISTS CHURN_OPS.OUTCOMES (
    decision_id VARCHAR NOT NULL,
    response    VARCHAR,
    outcome     VARCHAR,
    measured_at TIMESTAMP_NTZ,
    window_days INTEGER,
    PRIMARY KEY (decision_id)
);
