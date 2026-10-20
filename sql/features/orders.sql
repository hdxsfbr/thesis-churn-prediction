-- Orders domain. Defines order_agg (one row per subscriber). Requires the `orders` contract view.
-- A refund counts only if it happened before as_of.
visible_orders AS (
    SELECT subscriber_key, ordered_at, amount_usd, discount_usd, refund_usd,
           CASE WHEN refunded_at < {{as_of}} THEN 1 ELSE 0 END AS refunded
    FROM orders
    WHERE ordered_at < {{as_of}}
      AND loaded_at <= {{as_of}}
),
order_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS orders_total,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END) AS orders_90,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, {{as_of}}) THEN 1 ELSE 0 END) AS orders_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -60, {{as_of}}) THEN 1 ELSE 0 END) AS orders_recent60,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -120, {{as_of}})
                     AND ordered_at < DATEADD(day, -60, {{as_of}}) THEN 1 ELSE 0 END) AS orders_prior60,
           MAX(ordered_at) AS last_order_at,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, {{as_of}}) THEN discount_usd ELSE 0 END) AS discount_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, {{as_of}}) THEN amount_usd ELSE 0 END) AS gross_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, {{as_of}}) THEN refunded ELSE 0 END) AS refund_count_365,
           SUM(CASE WHEN ordered_at >= DATEADD(day, -365, {{as_of}}) AND refunded = 1
                    THEN COALESCE(refund_usd, 0) ELSE 0 END) AS refund_amount_365
    FROM visible_orders
    GROUP BY subscriber_key
)
