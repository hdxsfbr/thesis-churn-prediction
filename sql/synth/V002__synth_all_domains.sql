CREATE TABLE IF NOT EXISTS THESIS_SYNTH.subscription_events (
    event_id        VARCHAR,
    customer_id     VARCHAR,
    subscription_id VARCHAR,
    event_type      VARCHAR,
    event_ts        TIMESTAMP_NTZ,
    detail          VARCHAR,
    loaded_at       TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.products (
    sku           VARCHAR,
    product_name  VARCHAR,
    goal_category VARCHAR,
    price_usd     DOUBLE,
    status        VARCHAR,
    loaded_at     TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.email_events (
    event_id      VARCHAR,
    customer_id   VARCHAR,
    event_type    VARCHAR,
    campaign_id   VARCHAR,
    flow          VARCHAR,
    event_ts      TIMESTAMP_NTZ,
    tz_offset_min BIGINT,
    loaded_at     TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.support_tickets (
    ticket_id     VARCHAR,
    customer_id   VARCHAR,
    category      VARCHAR,
    created_at    TIMESTAMP_NTZ,
    resolved_at   TIMESTAMP_NTZ,
    sentiment     DOUBLE,
    status        VARCHAR,
    tz_offset_min BIGINT,
    loaded_at     TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.quiz_responses (
    response_id  VARCHAR,
    customer_id  VARCHAR,
    question_key VARCHAR,
    answer_value VARCHAR,
    answered_at  TIMESTAMP_NTZ,
    loaded_at    TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS THESIS_SYNTH.customers_pii (
    customer_id VARCHAR,
    first_name  VARCHAR,
    last_name   VARCHAR,
    email       VARCHAR,
    phone       VARCHAR,
    postal_code VARCHAR,
    loaded_at   TIMESTAMP_NTZ
);

-- Generator ground truth for evaluation (archetype recovery). Not part of the source contract.
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.latent_truth (
    customer_id       VARCHAR,
    archetype         VARCHAR,
    price_sensitivity DOUBLE,
    efficacy_gap      DOUBLE,
    fatigue           DOUBLE,
    life_event        DOUBLE,
    hazard_per_day    DOUBLE
);
