CREATE SCHEMA IF NOT EXISTS CHURN_OPS;

CREATE TABLE IF NOT EXISTS CHURN_OPS.DATA_PROFILE (
    section      VARCHAR,
    name         VARCHAR,
    rows         BIGINT,
    total        BIGINT,
    share        DOUBLE,
    detail       VARCHAR,
    generated_at TIMESTAMP_NTZ
);
