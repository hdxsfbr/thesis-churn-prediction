-- requires: email_events
-- columns: subscriber_key, n_events, sends_30, opens_30, sends_90, opens_90, clicks_90, unsubscribed, last_open_at
-- Email engagement per subscriber. Snowflake dialect. Parameter: :as_of.
-- Only events with event_ts < :as_of that were loaded at or before :as_of are visible.
email_f AS (
    SELECT subscriber_key,
           COUNT(*) AS n_events,
           SUM(CASE WHEN event_type = 'send' AND event_ts >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS sends_30,
           SUM(CASE WHEN event_type = 'open' AND event_ts >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS opens_30,
           SUM(CASE WHEN event_type = 'send' AND event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS sends_90,
           SUM(CASE WHEN event_type = 'open' AND event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS opens_90,
           SUM(CASE WHEN event_type = 'click' AND event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS clicks_90,
           MAX(CASE WHEN event_type = 'unsubscribe' THEN 1 ELSE 0 END) AS unsubscribed,
           MAX(CASE WHEN event_type = 'open' THEN event_ts END) AS last_open_at
    FROM email_events
    WHERE event_ts < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
    GROUP BY subscriber_key
)
