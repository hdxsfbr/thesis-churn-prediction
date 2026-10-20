-- Point-in-time features per active subscriber. Snowflake dialect.
-- Template tokens (substituted by features.builder): the source-contract CTE list and the quoted
-- active-status list from settings.
-- Parameter: :as_of. Only events with ordered_at < :as_of and loaded_at <= :as_of are visible.
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
visible_orders AS (
    SELECT subscriber_key, ordered_at, amount_usd, discount_usd,
           CASE WHEN refunded_at < CAST(:as_of AS TIMESTAMP) THEN 1 ELSE 0 END AS refunded
    FROM orders
    WHERE ordered_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
),
order_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS orders_total,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_90,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_365,
           MAX(ordered_at) AS last_order_at,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN discount_usd ELSE 0 END) AS discount_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN amount_usd ELSE 0 END) AS gross_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN refunded ELSE 0 END) AS refund_count_365
    FROM visible_orders
    GROUP BY subscriber_key
)
SELECT a.subscriber_key,
       a.subscription_id,
       DATEDIFF(day, a.started_at, CAST(:as_of AS TIMESTAMP)) AS tenure_days,
       a.price_usd,
       a.interval_days,
       COALESCE(o.orders_total, 0) AS orders_total,
       COALESCE(o.orders_90, 0) AS orders_90,
       COALESCE(o.orders_365, 0) AS orders_365,
       DATEDIFF(day, o.last_order_at, CAST(:as_of AS TIMESTAMP)) AS days_since_last_order,
       DATEDIFF(day, o.last_order_at, CAST(:as_of AS TIMESTAMP)) / a.interval_days AS overdue_ratio,
       o.discount_365 / NULLIF(o.gross_365, 0) AS discount_share_365,
       COALESCE(o.refund_count_365, 0) AS refund_count_365
FROM active a
LEFT JOIN order_agg o ON o.subscriber_key = a.subscriber_key
ORDER BY a.subscriber_key
