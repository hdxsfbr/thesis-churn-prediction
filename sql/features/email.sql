-- requires: email_events
-- columns: subscriber_key:VARCHAR, events_total, sends_30, opens_30, sends_prior, opens_prior, events_91, click_slope_num, unsubscribed
-- Email engagement (event_type send | open | click | unsubscribe). Visible: event_ts < :as_of, loaded_at <= :as_of.
-- Open rate delta: recent = [as_of-30d, as_of), prior = [as_of-90d, as_of-30d) (disjoint, ARCHITECTURE 6.2).
-- Click slope: 13 weekly buckets over [as_of-91d, as_of), week index 0 (oldest) .. 12 (newest); the least-squares
-- slope of weekly clicks is sum((week-6) * clicks) / 182, because the week indices are centred on 6.
visible_email AS (
    SELECT subscriber_key, event_type, event_ts
    FROM email_events
    WHERE event_ts < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
      AND subscriber_key IN (SELECT subscriber_key FROM active)
),
email_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS events_total,
           SUM(CASE WHEN event_type = 'send' AND event_ts >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS sends_30,
           SUM(CASE WHEN event_type = 'open' AND event_ts >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS opens_30,
           SUM(CASE WHEN event_type = 'send' AND event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP))
                     AND event_ts < DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS sends_prior,
           SUM(CASE WHEN event_type = 'open' AND event_ts >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP))
                     AND event_ts < DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS opens_prior,
           SUM(CASE WHEN event_ts >= DATEADD(day, -91, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS events_91,
           SUM(CASE WHEN event_type = 'click' AND event_ts >= DATEADD(day, -91, CAST(:as_of AS TIMESTAMP))
                    THEN LEAST(FLOOR(DATEDIFF(day, DATEADD(day, -91, CAST(:as_of AS TIMESTAMP)), event_ts) / 7.0), 12) - 6
                    ELSE 0 END) AS click_slope_num,
           MAX(CASE WHEN event_type = 'unsubscribe' THEN 1 ELSE 0 END) AS unsubscribed
    FROM visible_email
    GROUP BY subscriber_key
)
