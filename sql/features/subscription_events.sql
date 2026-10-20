-- Subscription-event domain (skip, pause, resume, modify, blend_switch, reactivate).
-- Defines sub_event_agg (one row per subscriber). Events attach to the subscriber's active subscription.
-- stub subscription_events: subscription_id VARCHAR, event_id VARCHAR, event_type VARCHAR, event_ts TIMESTAMP, loaded_at TIMESTAMP
visible_sub_events AS (
    SELECT a.subscriber_key, e.event_type, e.event_ts
    FROM subscription_events e
    JOIN active a ON a.subscription_id = e.subscription_id
    WHERE e.event_ts < {{as_of}}
      AND e.loaded_at <= {{as_of}}
),
sub_event_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS n_events,
           SUM(CASE WHEN event_type = 'skip' AND event_ts >= DATEADD(day, -60, {{as_of}}) THEN 1 ELSE 0 END)
               AS skips_recent60,
           SUM(CASE WHEN event_type = 'skip' AND event_ts >= DATEADD(day, -120, {{as_of}})
                     AND event_ts < DATEADD(day, -60, {{as_of}}) THEN 1 ELSE 0 END) AS skips_prior60,
           SUM(CASE WHEN event_type = 'blend_switch' AND event_ts >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END)
               AS switches_90,
           SUM(CASE WHEN event_type = 'blend_switch' AND event_ts >= DATEADD(day, -180, {{as_of}})
                     AND event_ts < DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END) AS switches_prior90,
           SUM(CASE WHEN event_type = 'blend_switch' AND event_ts >= DATEADD(day, -365, {{as_of}}) THEN 1 ELSE 0 END)
               AS switches_365,
           SUM(CASE WHEN event_type = 'pause' AND event_ts >= DATEADD(day, -180, {{as_of}}) THEN 1 ELSE 0 END)
               AS pauses_180
    FROM visible_sub_events
    GROUP BY subscriber_key
)
