-- requires: orders
-- columns: subscriber_key, orders_total, orders_60, orders_prior_60, orders_90, orders_365, last_order_at, net_90, discount_365, gross_365, refund_count_365
-- Order features per subscriber. Snowflake dialect. Parameter: :as_of.
-- Only orders with ordered_at < :as_of that were loaded at or before :as_of are visible; a refund only
-- counts once refunded_at < :as_of.
orders_f AS (
    SELECT subscriber_key,
           COUNT(*) AS orders_total,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_60,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -120, CAST(:as_of AS TIMESTAMP))
                     AND ordered_at < DATEADD(day, -60, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_prior_60,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_90,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS orders_365,
           MAX(ordered_at) AS last_order_at,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP))
                    THEN amount_usd - COALESCE(discount_usd, 0) ELSE 0 END) AS net_90,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN discount_usd ELSE 0 END) AS discount_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN amount_usd ELSE 0 END) AS gross_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP))
                     AND refunded_at < CAST(:as_of AS TIMESTAMP) THEN 1 ELSE 0 END) AS refund_count_365
    FROM orders
    WHERE ordered_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
    GROUP BY subscriber_key
)
