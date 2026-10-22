-- V008 is a historical hole; V015 is reserved for T29 (Architecture 5.3).
-- Missing contexts use the run-pinned informative priors until outcomes arrive.
CREATE TABLE IF NOT EXISTS CHURN_OPS.POLICY_STATS (
    archetype VARCHAR NOT NULL,
    intervention_type VARCHAR NOT NULL,
    context_bucket VARCHAR NOT NULL,
    alpha DOUBLE NOT NULL,
    beta DOUBLE NOT NULL,
    n BIGINT DEFAULT 0,
    last_updated_run VARCHAR,
    PRIMARY KEY (archetype, intervention_type, context_bucket)
);
