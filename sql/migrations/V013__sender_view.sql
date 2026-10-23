-- Rows the downstream sender is allowed to read. The sender never sees cancelled rows.
CREATE OR REPLACE VIEW CHURN_OPS.OUTBOX_FOR_SENDER AS
SELECT outbox_id,
       channel,
       intervention_type,
       subject,
       body,
       offer_code,
       discount_pct,
       escalation_reason,
       subscriber_key,
       created_at
FROM CHURN_OPS.OUTBOX
WHERE status = 'READY';
