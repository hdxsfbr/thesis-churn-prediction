-- Pseudonymous key map (R7.1): the only place the raw customer_id sits next to subscriber_key.
-- Grant access to the sender and app roles only (done in the Snowflake deployment, see docs/data-contract.md).
CREATE SCHEMA IF NOT EXISTS CHURN_PRIVATE;

CREATE TABLE IF NOT EXISTS CHURN_PRIVATE.KEY_MAP (
    subscriber_key VARCHAR,
    customer_id    VARCHAR
);
