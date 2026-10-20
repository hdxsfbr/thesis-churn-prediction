-- requires: orders
-- columns: subscriber_key:VARCHAR, orders_total, orders_60, orders_prior_60, orders_90, orders_365, last_order_at:TIMESTAMP, discount_365, gross_365, refund_count_365
-- Orders domain. Only orders with ordered_at < :as_of and loaded_at <= :as_of are visible (no leakage).
visible_orders AS (
    SELECT subscriber_key, ordered_at, amount_usd, discount_usd,
           CASE WHEN refunded_at < CAST(:as_of AS TIMESTAMP) THEN 1 ELSE 0 END AS refunded
    FROM orders
    WHERE ordered_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
      AND subscriber_key IN (SELECT subscriber_key FROM active)
),
orders_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS orders_total,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_60,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -120, CAST(:as_of AS TIMESTAMP))
                     AND ordered_at < DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_prior_60,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_90,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_365,
           MAX(ordered_at) AS last_order_at,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN COALESCE(discount_usd, 0) ELSE 0 END) AS discount_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN COALESCE(amount_usd, 0) ELSE 0 END) AS gross_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN refunded ELSE 0 END) AS refund_count_365
    FROM visible_orders
    GROUP BY subscriber_key
)
