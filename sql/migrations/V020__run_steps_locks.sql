-- Checkpoints and the cycle lock. V012 was never used; the next free version after V019 is V020.
CREATE TABLE IF NOT EXISTS CHURN_OPS.RUN_STEPS (
    run_id       VARCHAR NOT NULL,
    step         VARCHAR NOT NULL,
    shard        INTEGER NOT NULL,
    status       VARCHAR,
    rows_written INTEGER,
    attempt      INTEGER,
    error_class  VARCHAR,
    error_msg    VARCHAR,
    updated_at   TIMESTAMP_NTZ,
    PRIMARY KEY (run_id, step, shard)
);

CREATE TABLE IF NOT EXISTS CHURN_OPS.RUN_LOCKS (
    lock_name    VARCHAR NOT NULL,
    holder       VARCHAR,
    heartbeat_at TIMESTAMP_NTZ,
    ttl_s        INTEGER,
    PRIMARY KEY (lock_name)
);
