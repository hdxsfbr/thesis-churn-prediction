-- Point-in-time features per active subscriber. Snowflake dialect.
-- Template tokens (substituted by features.builder): the source-contract CTE list, the quoted
-- active-status list from settings and the per-domain CTE list.
-- The domain CTE list is replaced by the per-domain files (orders, subscription_events, email, support, quiz); a
-- domain whose view is not in the source contract is replaced by an empty CTE with the same columns.
-- Parameter: :as_of. Only rows with an event time < :as_of that were loaded at or before :as_of are visible.
WITH {{contract_ctes}},
active AS (
    -- One row per subscriber (the most recently started active subscription), so RISK_SCORES keeps
    -- its (run_id, subscriber_key) key. Status is only used to decide who is active: the source holds
    -- today's status only, so status-derived features would leak the label until subscription_events.
    SELECT subscriber_key,
           subscription_id,
           started_at,
           price_usd,
           interval_days
    FROM subscriptions
    WHERE started_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
      AND (cancelled_at IS NULL OR cancelled_at >= CAST(:as_of AS TIMESTAMP))
      AND (cancelled_at IS NOT NULL OR status IN ({{active_statuses}}))
    QUALIFY ROW_NUMBER() OVER (PARTITION BY subscriber_key ORDER BY started_at DESC, subscription_id DESC) = 1
),
{{domain_ctes}},
features AS (
    SELECT a.subscriber_key,
           a.subscription_id,
           DATEDIFF(day, a.started_at, CAST(:as_of AS TIMESTAMP)) AS tenure_days,
           a.price_usd,
           a.interval_days,
           -- orders
           COALESCE(o.orders_total, 0) AS orders_total,
           COALESCE(o.orders_60, 0) AS orders_60,
           COALESCE(o.orders_prior_60, 0) AS orders_prior_60,
           COALESCE(o.orders_90, 0) AS orders_90,
           COALESCE(o.orders_365, 0) AS orders_365,
           -- tenure-truncated window: a subscriber who joined 10 days ago is normalised by 10, not 90
           COALESCE(o.orders_90, 0) * 30.0
               / GREATEST(LEAST(DATEDIFF(day, a.started_at, CAST(:as_of AS TIMESTAMP)), 90), 1) AS orders_per_30d_90,
           COALESCE(o.orders_90, 0) * 1.0 / NULLIF(COALESCE(o.orders_365, 0), 0) AS orders_ratio_90_365,
           DATEDIFF(day, o.last_order_at, CAST(:as_of AS TIMESTAMP)) AS days_since_last_order,
           DATEDIFF(day, o.last_order_at, CAST(:as_of AS TIMESTAMP)) / a.interval_days AS overdue_ratio,
           o.discount_365 / NULLIF(o.gross_365, 0) AS discount_share_365,
           COALESCE(o.refund_count_365, 0) AS refund_count_365,
           (o.net_90 / NULLIF(o.orders_90, 0)) / NULLIF(a.price_usd, 0) AS paid_to_list_price_90,
           -- subscription events
           CASE WHEN COALESCE(s.n_events, 0) > 0 THEN 1 ELSE 0 END AS has_subscription_events,
           COALESCE(s.skips_60, 0) AS skips_60,
           COALESCE(s.skips_60, 0) * 1.0 / NULLIF(COALESCE(s.skips_60, 0) + COALESCE(o.orders_60, 0), 0) AS skip_rate_60,
           COALESCE(s.skips_prior_60, 0) * 1.0
               / NULLIF(COALESCE(s.skips_prior_60, 0) + COALESCE(o.orders_prior_60, 0), 0) AS skip_rate_prior_60,
           COALESCE(s.skips_60, 0) * 1.0 / NULLIF(COALESCE(s.skips_60, 0) + COALESCE(o.orders_60, 0), 0)
               - COALESCE(s.skips_prior_60, 0) * 1.0
                 / NULLIF(COALESCE(s.skips_prior_60, 0) + COALESCE(o.orders_prior_60, 0), 0) AS skip_rate_delta_60,
           COALESCE(s.pause_events_180, 0) AS pause_events_180,
           COALESCE(s.blend_switch_count_90, 0) AS blend_switch_count_90,
           COALESCE(s.blend_switch_count_365, 0) AS blend_switch_count_365,
           -- email
           CASE WHEN COALESCE(e.n_events, 0) > 0 THEN 1 ELSE 0 END AS has_email,
           e.opens_30 * 1.0 / NULLIF(e.sends_30, 0) AS email_open_rate_30,
           e.opens_90 * 1.0 / NULLIF(e.sends_90, 0) AS email_open_rate_90,
           e.opens_30 * 1.0 / NULLIF(e.sends_30, 0) - e.opens_90 * 1.0 / NULLIF(e.sends_90, 0) AS email_open_rate_delta_30_90,
           e.clicks_90 * 1.0 / NULLIF(e.sends_90, 0) AS email_click_rate_90,
           DATEDIFF(day, e.last_open_at, CAST(:as_of AS TIMESTAMP)) AS days_since_last_open,
           COALESCE(e.unsubscribed, 0) AS email_unsubscribed,
           -- support
           CASE WHEN COALESCE(t.n_tickets, 0) > 0 THEN 1 ELSE 0 END AS has_support,
           COALESCE(t.tickets_30, 0) AS tickets_30,
           COALESCE(t.tickets_90, 0) AS tickets_90,
           COALESCE(t.escalated_90, 0) AS escalated_90,
           t.sentiment_30 - t.sentiment_90 AS sentiment_delta_30_90,
           COALESCE(t.escalated_90, 0)
               + GREATEST(COALESCE(t.tickets_30, 0) - COALESCE(t.tickets_90, 0) / 3.0, 0)
               + GREATEST(COALESCE(t.sentiment_30 - t.sentiment_90, 0) * -1, 0) AS ticket_escalation_score,
           -- quiz
           CASE WHEN COALESCE(q.n_answers, 0) > 0 THEN 1 ELSE 0 END AS has_quiz,
           DATEDIFF(day, q.last_quiz_at, CAST(:as_of AS TIMESTAMP)) AS days_since_last_quiz,
           COALESCE(q.sessions_90, 0) AS quiz_sessions_90,
           GREATEST(COALESCE(q.sessions_365, 0)
               - CASE WHEN q.first_quiz_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END, 0)
               AS quiz_retake_count_365,
           COALESCE(q.sessions_90, 0) * 1.0 / NULLIF(COALESCE(q.sessions_365, 0), 0) AS quiz_session_share_90_365
    FROM active a
    LEFT JOIN orders_f o ON o.subscriber_key = a.subscriber_key
    LEFT JOIN subscription_events_f s ON s.subscriber_key = a.subscriber_key
    LEFT JOIN email_f e ON e.subscriber_key = a.subscriber_key
    LEFT JOIN support_f t ON t.subscriber_key = a.subscriber_key
    LEFT JOIN quiz_f q ON q.subscriber_key = a.subscriber_key
)
SELECT * FROM features
ORDER BY subscriber_key
