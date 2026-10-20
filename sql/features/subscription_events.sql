-- requires: subscription_events, subscriptions
-- columns: subscriber_key, n_events, skips_60, skips_prior_60, pause_events_180, blend_switch_count_90, blend_switch_count_365
-- Skip / pause / blend-switch features per subscriber. Snowflake dialect. Parameter: :as_of.
-- Events carry subscription_id only, so they are mapped to the subscriber through the subscriptions view.
-- Only events with event_ts < :as_of that were loaded at or before :as_of are visible.
subscription_events_f AS (
    SELECT s.subscriber_key,
           COUNT(*) AS n_events,
           SUM(CASE WHEN e.event_type = 'skip'
                     AND e.event_ts >= DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS skips_60,
           SUM(CASE WHEN e.event_type = 'skip'
                     AND e.event_ts >= DATEADD(day, -120, CAST(:as_of AS TIMESTAMP))
                     AND e.event_ts < DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS skips_prior_60,
           SUM(CASE WHEN e.event_type = 'pause'
                     AND e.event_ts >= DATEADD(day, -180, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS pause_events_180,
           SUM(CASE WHEN e.event_type = 'blend_switch'
                     AND e.event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS blend_switch_count_90,
           SUM(CASE WHEN e.event_type = 'blend_switch'
                     AND e.event_ts >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS blend_switch_count_365
    FROM subscription_events e
    JOIN subscriptions s ON s.subscription_id = e.subscription_id
    WHERE e.event_ts < CAST(:as_of AS TIMESTAMP)
      AND e.loaded_at <= CAST(:as_of AS TIMESTAMP)
    GROUP BY s.subscriber_key
)
