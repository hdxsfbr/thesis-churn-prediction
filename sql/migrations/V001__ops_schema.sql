CREATE SCHEMA IF NOT EXISTS CHURN_OPS;

CREATE TABLE IF NOT EXISTS CHURN_OPS.RUNS (
    run_id           VARCHAR,
    kind             VARCHAR,
    as_of            TIMESTAMP_NTZ,
    status           VARCHAR,
    model_version    VARCHAR,
    active_in_source BIGINT,
    scored           BIGINT,
    started_at       TIMESTAMP_NTZ,
    ended_at         TIMESTAMP_NTZ,
    duration_s       DOUBLE,
    error_msg        VARCHAR
);

CREATE TABLE IF NOT EXISTS CHURN_OPS.RISK_SCORES (
    run_id         VARCHAR,
    subscriber_key VARCHAR,
    as_of          TIMESTAMP_NTZ,
    churn_prob     DOUBLE,
    model_version  VARCHAR,
    scored_at      TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CHURN_OPS.MODEL_REGISTRY (
    model_version VARCHAR,
    artefact_uri  VARCHAR,
    sha256        VARCHAR,
    train_start   TIMESTAMP_NTZ,
    train_end     TIMESTAMP_NTZ,
    metrics_json  VARCHAR,
    status        VARCHAR,
    reason        VARCHAR,
    created_at    TIMESTAMP_NTZ
);
