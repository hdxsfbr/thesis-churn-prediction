CREATE SCHEMA IF NOT EXISTS THESIS_SYNTH;

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.subscriptions (
    customer_id        VARCHAR,
    subscription_id    VARCHAR,
    status             VARCHAR,
    started_at         TIMESTAMP_NTZ,
    cancelled_at       TIMESTAMP_NTZ,
    cancel_type        VARCHAR,
    cancel_reason_code VARCHAR,
    price_usd          DOUBLE,
    interval_days      BIGINT,
    loaded_at          TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.orders (
    order_id     VARCHAR,
    customer_id  VARCHAR,
    ordered_at   TIMESTAMP_NTZ,
    blend_sku    VARCHAR,
    amount_usd   DOUBLE,
    discount_usd DOUBLE,
    refund_usd   DOUBLE,
    refunded_at  TIMESTAMP_NTZ,
    loaded_at    TIMESTAMP_NTZ
);
