-- Model evaluation results (R3.6): one row per model version and holdout end date, rewritten on rerun.
CREATE TABLE IF NOT EXISTS CHURN_OPS.EVAL_REPORTS (
    report_id         VARCHAR,
    model_version     VARCHAR,
    holdout_start     TIMESTAMP_NTZ,
    holdout_end       TIMESTAMP_NTZ,
    label_window_days BIGINT,
    n_rows            BIGINT,
    n_positives       BIGINT,
    metrics_json      VARCHAR,
    targets_json      VARCHAR,
    archetype_json    VARCHAR,
    calibration_json  VARCHAR,
    report_md         VARCHAR,
    created_at        TIMESTAMP_NTZ
);
