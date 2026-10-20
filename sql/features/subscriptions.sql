-- Point-in-time features per active subscriber. Snowflake dialect.
-- Template tokens (substituted by features.builder): the source-contract CTE list, the per-domain CTE
-- files (orders, subscription_events, email, support, quiz; an empty typed stub replaces a domain whose
-- contract view is not mapped) and the quoted active-status list from settings.
-- Parameter: :as_of. Only events with event_ts < :as_of (created_at / ordered_at / answered_at in their views) and loaded_at <= :as_of are visible (R2.3).
-- Missing data (R2.4): counts default to 0; rates and recencies stay NULL; has_<domain> says whether the
-- subscriber has any visible row in that domain.
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
base AS (
    SELECT a.subscriber_key,
           a.subscription_id,
           DATEDIFF(day, a.started_at, CAST(:as_of AS TIMESTAMP)) AS tenure_days,
           a.price_usd,
           a.interval_days,
           o.orders_total, o.orders_60, o.orders_prior_60, o.orders_90, o.orders_365, o.last_order_at,
           o.discount_365, o.gross_365, o.refund_count_365,
           s.events_total AS sub_events_total, s.skips_60, s.skips_prior_60,
           s.switches_90, s.switches_365, s.switches_prior_275,
           m.events_total AS email_events_total, m.sends_30, m.opens_30, m.sends_prior, m.opens_prior,
           m.events_91, m.click_slope_num, m.unsubscribed,
           t.tickets_total, t.tickets_30, t.tickets_90, t.escalated_90, t.sentiment_30, t.sentiment_prior,
           q.quizzes_total, q.last_quiz_at, q.retakes_90, q.retakes_prior_90, q.retakes_365
    FROM active a
    LEFT JOIN orders_agg o ON o.subscriber_key = a.subscriber_key
    LEFT JOIN subscription_events_agg s ON s.subscriber_key = a.subscriber_key
    LEFT JOIN email_agg m ON m.subscriber_key = a.subscriber_key
    LEFT JOIN support_agg t ON t.subscriber_key = a.subscriber_key
    LEFT JOIN quiz_agg q ON q.subscriber_key = a.subscriber_key
),
derived AS (
    SELECT b.*,
           -- tenure-truncated window lengths in days (ARCHITECTURE 6.2: normalised by min(tenure, window)).
           -- A prior window the subscriber was not yet around for has length 0 and its trend is NULL.
           LEAST(b.tenure_days, 90) AS recent_len,
           LEAST(GREATEST(b.tenure_days - 90, 0), 275) AS prior_len,
           LEAST(b.tenure_days, 60) AS recent_len_60,
           LEAST(GREATEST(b.tenure_days - 60, 0), 60) AS prior_len_60,
           LEAST(b.tenure_days, 90) AS recent_len_90,
           LEAST(GREATEST(b.tenure_days - 90, 0), 90) AS prior_len_90,
           b.opens_30 / NULLIF(b.sends_30, 0) AS open_rate_30,
           b.opens_prior / NULLIF(b.sends_prior, 0) AS open_rate_prior,
           b.skips_60 / NULLIF(COALESCE(b.orders_60, 0) + b.skips_60, 0) AS skip_rate_recent,
           b.skips_prior_60 / NULLIF(COALESCE(b.orders_prior_60, 0) + b.skips_prior_60, 0) AS skip_rate_prior,
           b.sentiment_30 - b.sentiment_prior AS sentiment_delta
    FROM base b
)
SELECT d.subscriber_key,
       d.subscription_id,
       d.tenure_days,
       d.price_usd,
       d.interval_days,
       -- orders
       CASE WHEN d.orders_total IS NULL THEN 0 ELSE 1 END AS has_orders,
       COALESCE(d.orders_total, 0) AS orders_total,
       COALESCE(d.orders_90, 0) AS orders_90,
       COALESCE(d.orders_365, 0) AS orders_365,
       -- orders per 60 days, last 60 days minus the 60 before, each window truncated to the tenure
       COALESCE(d.orders_60, 0) / (NULLIF(d.recent_len_60, 0) / 60.0)
         - COALESCE(d.orders_prior_60, 0) / (NULLIF(d.prior_len_60, 0) / 60.0) AS orders_trend_60,
       DATEDIFF(day, d.last_order_at, CAST(:as_of AS TIMESTAMP)) AS days_since_last_order,
       DATEDIFF(day, d.last_order_at, CAST(:as_of AS TIMESTAMP)) / d.interval_days AS overdue_ratio,
       d.discount_365 / NULLIF(d.gross_365, 0) AS discount_share_365,
       COALESCE(d.refund_count_365, 0) AS refund_count_365,
       -- subscription events: skips and blend switching
       CASE WHEN d.sub_events_total IS NULL THEN 0 ELSE 1 END AS has_subscription_events,
       d.skip_rate_recent - d.skip_rate_prior AS skip_rate_60_vs_prior_60,
       COALESCE(d.skips_60, 0) / (NULLIF(d.recent_len_60, 0) / 60.0)
         - COALESCE(d.skips_prior_60, 0) / (NULLIF(d.prior_len_60, 0) / 60.0) AS skip_count_trend,
       COALESCE(d.switches_90, 0) AS blend_switch_count_90,
       COALESCE(d.switches_365, 0) AS blend_switch_count_365,
       COALESCE(d.switches_90, 0) / (NULLIF(d.recent_len, 0) / 30.0)
         - COALESCE(d.switches_prior_275, 0) / (NULLIF(d.prior_len, 0) / 30.0) AS blend_switch_rate_change,
       -- email
       CASE WHEN d.email_events_total IS NULL THEN 0 ELSE 1 END AS has_email,
       d.open_rate_30 AS email_open_rate_30,
       d.open_rate_30 - d.open_rate_prior AS email_open_rate_delta_30_90,
       CASE WHEN d.events_91 > 0 THEN d.click_slope_num / 182.0 END AS email_click_slope_90,
       COALESCE(d.unsubscribed, 0) AS email_unsubscribed,
       -- support
       CASE WHEN d.tickets_total IS NULL THEN 0 ELSE 1 END AS has_support,
       COALESCE(d.tickets_30, 0) AS tickets_30,
       COALESCE(d.tickets_90, 0) AS tickets_90,
       COALESCE(d.escalated_90, 0) AS tickets_escalated_90,
       d.sentiment_delta AS sentiment_delta_30_90,
       COALESCE(d.tickets_30, 0) + 2 * COALESCE(d.escalated_90, 0)
         + 3 * GREATEST(-COALESCE(d.sentiment_delta, 0), 0) AS ticket_escalation_score,
       -- quiz
       CASE WHEN d.quizzes_total IS NULL THEN 0 ELSE 1 END AS has_quiz,
       DATEDIFF(day, d.last_quiz_at, CAST(:as_of AS TIMESTAMP)) AS days_since_last_quiz,
       COALESCE(d.retakes_90, 0) AS quiz_retake_count_90,
       COALESCE(d.retakes_90, 0) / (NULLIF(d.recent_len_90, 0) / 90.0)
         - COALESCE(d.retakes_prior_90, 0) / (NULLIF(d.prior_len_90, 0) / 90.0) AS quiz_retake_trend_90,
       COALESCE(d.retakes_365, 0) AS quiz_retake_count_365
FROM derived d
ORDER BY d.subscriber_key
