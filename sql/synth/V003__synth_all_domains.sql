CREATE TABLE IF NOT EXISTS THESIS_SYNTH.subscription_events (
 subscription_id VARCHAR, event_id VARCHAR, event_type VARCHAR, event_ts TIMESTAMP_NTZ, loaded_at TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.products (
 blend_sku VARCHAR, blend_name VARCHAR, goal_category VARCHAR, price_usd DOUBLE, loaded_at TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.email_events (
 customer_id VARCHAR, event_id VARCHAR, event_type VARCHAR, campaign_id VARCHAR, flow_id VARCHAR,
 event_ts TIMESTAMP_TZ, loaded_at TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.support_tickets (
 customer_id VARCHAR, ticket_id VARCHAR, created_at TIMESTAMP_NTZ, category VARCHAR, escalated BOOLEAN,
 resolution_hours DOUBLE, sentiment_score DOUBLE, loaded_at TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.quiz_responses (
 answer_id VARCHAR, customer_id VARCHAR, quiz_session_id VARCHAR, question_key VARCHAR, answer_value VARCHAR,
 answered_at TIMESTAMP_NTZ, recommended_sku VARCHAR, loaded_at TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.customers_pii (
 customer_id VARCHAR, first_name VARCHAR, email VARCHAR, phone VARCHAR, address VARCHAR, loaded_at TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS THESIS_SYNTH.latent_truth (
 customer_id VARCHAR, archetype VARCHAR, price_sensitivity DOUBLE, fatigue DOUBLE, hazard_per_day DOUBLE,
 churn_cause VARCHAR
);
