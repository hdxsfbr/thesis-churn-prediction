-- requires: subscription_events
-- columns: subscriber_key:VARCHAR, events_total, skips_60, skips_prior_60, switches_90, switches_365, switches_prior_275
-- subscription_events has no customer_id (ARCHITECTURE 4.2): the subscriber comes from the subscriptions CTE
-- through subscription_id.
-- Shipment modifications (event_type skip | blend_switch | pause | ...). Visible: event_ts < :as_of, loaded_at <= :as_of.
-- Windows: recent 60 = [as_of-60d, as_of); prior 60 = [as_of-120d, as_of-60d);
-- switches_prior_275 = [as_of-365d, as_of-90d).
visible_sub_events AS (
    SELECT s.subscriber_key, e.event_type, e.event_ts
    FROM subscription_events e
    JOIN subscriptions s ON s.subscription_id = e.subscription_id
    WHERE e.event_ts < CAST(:as_of AS TIMESTAMP)
      AND e.loaded_at <= CAST(:as_of AS TIMESTAMP)
      AND s.subscriber_key IN (SELECT subscriber_key FROM active)
),
subscription_events_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS events_total,
           SUM(CASE WHEN event_type = 'skip' AND event_ts >= DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS skips_60,
           SUM(CASE WHEN event_type = 'skip' AND event_ts >= DATEADD(day, -120, CAST(:as_of AS TIMESTAMP))
                     AND event_ts < DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS skips_prior_60,
           SUM(CASE WHEN event_type = 'blend_switch' AND event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS switches_90,
           SUM(CASE WHEN event_type = 'blend_switch' AND event_ts >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS switches_365,
           SUM(CASE WHEN event_type = 'blend_switch' AND event_ts >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP))
                     AND event_ts < DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS switches_prior_275
    FROM visible_sub_events
    GROUP BY subscriber_key
)
