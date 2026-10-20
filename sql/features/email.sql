-- Email domain (send, open, click, unsubscribe). Defines email_agg (one row per subscriber).
-- stub email_events: subscriber_key VARCHAR, event_id VARCHAR, event_type VARCHAR, event_ts TIMESTAMP, loaded_at TIMESTAMP
-- Click slope (ARCHITECTURE 6.2): n = active.slope_weeks = LEAST(13, FLOOR(tenure_days / 7)) whole weeks end at
-- as_of. Only events in [as_of - 7n days, as_of) count. A click that is `age` seconds old falls in week
-- w = n - CEIL(age / 604800) (0 = oldest, n - 1 = newest), so SUM(w - (n - 1) / 2) over clicks is the numerator of
-- the least-squares slope; the master select divides it by n * (n * n - 1) / 12.
visible_email AS (
    SELECT subscriber_key, event_type, event_ts
    FROM email_events
    WHERE event_ts < {{as_of}}
      AND loaded_at <= {{as_of}}
),
email_agg AS (
    SELECT v.subscriber_key,
           COUNT(*) AS n_events,
           SUM(CASE WHEN v.event_type = 'send' AND v.event_ts >= DATEADD(day, -30, {{as_of}}) THEN 1 ELSE 0 END)
               AS sends_30,
           SUM(CASE WHEN v.event_type = 'open' AND v.event_ts >= DATEADD(day, -30, {{as_of}}) THEN 1 ELSE 0 END)
               AS opens_30,
           SUM(CASE WHEN v.event_type = 'send' AND v.event_ts >= DATEADD(day, -90, {{as_of}})
                     AND v.event_ts < DATEADD(day, -30, {{as_of}}) THEN 1 ELSE 0 END) AS sends_31_90,
           SUM(CASE WHEN v.event_type = 'open' AND v.event_ts >= DATEADD(day, -90, {{as_of}})
                     AND v.event_ts < DATEADD(day, -30, {{as_of}}) THEN 1 ELSE 0 END) AS opens_31_90,
           SUM(CASE WHEN v.event_type = 'send' AND v.event_ts >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END)
               AS sends_90,
           SUM(CASE WHEN v.event_type = 'open' AND v.event_ts >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END)
               AS opens_90,
           MAX(CASE WHEN v.event_type = 'unsubscribe' THEN 1 ELSE 0 END) AS unsubscribed,
           SUM(CASE WHEN a.slope_weeks >= 1 AND v.event_ts >= DATEADD(day, -7 * a.slope_weeks, {{as_of}})
                    THEN 1 ELSE 0 END) AS slope_window_events,
           SUM(CASE WHEN a.slope_weeks >= 2 AND v.event_type = 'click'
                     AND v.event_ts >= DATEADD(day, -7 * a.slope_weeks, {{as_of}})
                    THEN a.slope_weeks - CEIL(DATEDIFF(second, v.event_ts, {{as_of}}) / 604800.0)
                         - (a.slope_weeks - 1) / 2.0
                    ELSE 0 END) AS click_moment
    FROM visible_email v
    JOIN active a ON a.subscriber_key = v.subscriber_key
    GROUP BY v.subscriber_key
)
