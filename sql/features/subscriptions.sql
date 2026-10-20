-- Point-in-time features per active subscriber. Snowflake dialect.
-- Template tokens (substituted by features.builder):
--   {{contract_ctes}}  the source-contract CTEs (plus empty typed stubs for views the contract lacks)
--   {{domain_ctes}}    the per-domain CTEs from orders.sql, subscription_events.sql, email.sql, support.sql, quiz.sql
--   {{as_of}}          the bound :as_of parameter as a timestamp
--   {{active_statuses}} the quoted active-status list from settings
-- Leakage rule (R2.3): a row is visible only if its event time is < as_of and loaded_at <= as_of.
-- Window lengths are tenure-truncated (ARCHITECTURE 6.2): recent windows are min(tenure, w) days long and the
-- prior window is min(w, max(tenure - w, 0)) days long, so a new subscriber never gets a rate over empty time.
WITH {{contract_ctes}},
active AS (
    -- One row per subscriber (the most recently started active subscription), so RISK_SCORES keeps
    -- its (run_id, subscriber_key) key. Status is only used to decide who is active: the source holds
    -- today's status only, so status-derived features would leak the label until subscription_events.
    SELECT b.subscriber_key,
           b.subscription_id,
           b.started_at,
           b.price_usd,
           b.interval_days,
           b.tenure_days,
           LEAST(60, b.tenure_days) AS recent60_len,
           LEAST(60, GREATEST(b.tenure_days - 60, 0)) AS prior60_len,
           LEAST(90, b.tenure_days) AS recent90_len,
           LEAST(90, GREATEST(b.tenure_days - 90, 0)) AS prior90_len,
           -- whole weeks inside the tenure that end at as_of, at most 13 (the click-slope window)
           LEAST(13, CAST(FLOOR(b.tenure_days / 7.0) AS INTEGER)) AS slope_weeks
    FROM (
        SELECT subscriber_key,
               subscription_id,
               started_at,
               price_usd,
               interval_days,
               DATEDIFF(day, started_at, {{as_of}}) AS tenure_days
        FROM subscriptions
        WHERE started_at < {{as_of}}
          AND loaded_at <= {{as_of}}
          AND (cancelled_at IS NULL OR cancelled_at >= {{as_of}})
          AND (cancelled_at IS NOT NULL OR status IN ({{active_statuses}}))
        QUALIFY ROW_NUMBER() OVER (PARTITION BY subscriber_key ORDER BY started_at DESC, subscription_id DESC) = 1
    ) b
),
{{domain_ctes}}
SELECT a.subscriber_key,
       a.subscription_id,
       -- subscription
       a.tenure_days,
       a.price_usd,
       a.interval_days,
       -- orders
       COALESCE(o.orders_total, 0) AS orders_total,
       COALESCE(o.orders_90, 0) AS orders_90,
       COALESCE(o.orders_365, 0) AS orders_365,
       DATEDIFF(day, o.last_order_at, {{as_of}}) AS days_since_last_order,
       DATEDIFF(day, o.last_order_at, {{as_of}}) / a.interval_days AS overdue_ratio,
       o.discount_365 / NULLIF(o.gross_365, 0) AS discount_share_365,
       COALESCE(o.refund_count_365, 0) AS refund_count_365,
       COALESCE(o.refund_amount_365, 0) AS refund_amount_365,
       CASE WHEN a.recent60_len > 0 AND a.prior60_len > 0
            THEN 60.0 * (COALESCE(o.orders_recent60, 0) / CAST(a.recent60_len AS DOUBLE)
                         - COALESCE(o.orders_prior60, 0) / CAST(a.prior60_len AS DOUBLE))
       END AS orders_trend_60,
       -- subscription events
       CASE WHEN COALESCE(s.n_events, 0) > 0 THEN 1 ELSE 0 END AS has_subscription_events,
       COALESCE(s.skips_recent60, 0) AS skip_count_60,
       CASE WHEN COALESCE(s.skips_recent60, 0) + COALESCE(o.orders_recent60, 0) > 0
                 AND COALESCE(s.skips_prior60, 0) + COALESCE(o.orders_prior60, 0) > 0
            THEN COALESCE(s.skips_recent60, 0)
                     / CAST(COALESCE(s.skips_recent60, 0) + COALESCE(o.orders_recent60, 0) AS DOUBLE)
                 - COALESCE(s.skips_prior60, 0)
                     / CAST(COALESCE(s.skips_prior60, 0) + COALESCE(o.orders_prior60, 0) AS DOUBLE)
       END AS skip_rate_60_vs_prior_60,
       CASE WHEN a.recent60_len > 0 AND a.prior60_len > 0
            THEN 60.0 * (COALESCE(s.skips_recent60, 0) / CAST(a.recent60_len AS DOUBLE)
                         - COALESCE(s.skips_prior60, 0) / CAST(a.prior60_len AS DOUBLE))
       END AS skip_count_trend,
       COALESCE(s.switches_90, 0) AS blend_switch_count_90,
       COALESCE(s.switches_365, 0) AS blend_switch_count_365,
       CASE WHEN a.recent90_len > 0 AND a.prior90_len > 0
            THEN 90.0 * (COALESCE(s.switches_90, 0) / CAST(a.recent90_len AS DOUBLE)
                         - COALESCE(s.switches_prior90, 0) / CAST(a.prior90_len AS DOUBLE))
       END AS blend_switch_rate_change,
       COALESCE(s.pauses_180, 0) AS pause_events_180,
       -- email
       CASE WHEN COALESCE(e.n_events, 0) > 0 THEN 1 ELSE 0 END AS has_email,
       COALESCE(e.unsubscribed, 0) AS email_unsubscribed,
       CASE WHEN e.sends_90 > 0 THEN e.opens_90 / CAST(e.sends_90 AS DOUBLE) END AS email_open_rate_90,
       CASE WHEN e.sends_30 > 0 AND e.sends_31_90 > 0
            THEN e.opens_30 / CAST(e.sends_30 AS DOUBLE) - e.opens_31_90 / CAST(e.sends_31_90 AS DOUBLE)
       END AS email_open_rate_delta_30_90,
       -- least-squares slope of weekly clicks over the n whole weeks of tenure (n <= 13):
       -- SUM((w - (n - 1) / 2) * clicks_w) / (n * (n * n - 1) / 12), w = 0..n-1 oldest to newest
       CASE WHEN a.slope_weeks >= 2 AND e.slope_window_events > 0
            THEN e.click_moment / (a.slope_weeks * (a.slope_weeks * a.slope_weeks - 1) / 12.0)
       END AS email_click_slope_90,
       -- support
       CASE WHEN COALESCE(t.n_tickets, 0) > 0 THEN 1 ELSE 0 END AS has_support,
       COALESCE(t.tickets_30, 0) AS tickets_30,
       COALESCE(t.tickets_90, 0) AS tickets_90,
       COALESCE(t.escalated_90, 0) AS escalated_tickets_90,
       t.sentiment_30 - t.sentiment_31_90 AS ticket_sentiment_delta_30_90,
       COALESCE(t.tickets_30, 0) + 2 * COALESCE(t.escalated_90, 0)
           - COALESCE(t.sentiment_30 - t.sentiment_31_90, 0) AS ticket_escalation_score,
       -- quiz
       CASE WHEN COALESCE(q.n_sessions, 0) > 0 THEN 1 ELSE 0 END AS has_quiz,
       DATEDIFF(day, q.last_quiz_at, {{as_of}}) AS days_since_last_quiz,
       COALESCE(q.sessions_365, 0) - CASE WHEN q.first_started_at >= DATEADD(day, -365, {{as_of}}) THEN 1 ELSE 0 END
           AS quiz_retake_count_365,
       CASE WHEN a.recent90_len > 0 AND a.prior90_len > 0 AND q.n_sessions IS NOT NULL
            THEN 90.0 * (COALESCE(q.sessions_90, 0) / CAST(a.recent90_len AS DOUBLE)
                         - COALESCE(q.sessions_prior90, 0) / CAST(a.prior90_len AS DOUBLE))
       END AS quiz_retake_trend_90
FROM active a
LEFT JOIN order_agg o ON o.subscriber_key = a.subscriber_key
LEFT JOIN sub_event_agg s ON s.subscriber_key = a.subscriber_key
LEFT JOIN email_agg e ON e.subscriber_key = a.subscriber_key
LEFT JOIN ticket_agg t ON t.subscriber_key = a.subscriber_key
LEFT JOIN quiz_agg q ON q.subscriber_key = a.subscriber_key
ORDER BY a.subscriber_key
