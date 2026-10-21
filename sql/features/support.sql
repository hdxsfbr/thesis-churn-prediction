-- Support domain. Defines ticket_agg (one row per subscriber).
-- stub support_tickets: subscriber_key VARCHAR, ticket_id VARCHAR, created_at TIMESTAMP, category VARCHAR, escalated BOOLEAN, sentiment_score DOUBLE, loaded_at TIMESTAMP
visible_tickets AS (
    SELECT subscriber_key, created_at, COALESCE(escalated, FALSE) AS escalated, sentiment_score
    FROM support_tickets
    WHERE created_at < {{as_of}}
      AND loaded_at <= {{as_of}}
),
ticket_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS n_tickets,
           SUM(CASE WHEN created_at >= DATEADD(day, -30, {{as_of}}) THEN 1 ELSE 0 END) AS tickets_30,
           SUM(CASE WHEN created_at >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END) AS tickets_90,
           SUM(CASE WHEN created_at >= DATEADD(day, -90, {{as_of}}) AND escalated THEN 1 ELSE 0 END) AS escalated_90,
           AVG(CASE WHEN created_at >= DATEADD(day, -30, {{as_of}}) THEN sentiment_score END) AS sentiment_30,
           AVG(CASE WHEN created_at >= DATEADD(day, -90, {{as_of}})
                     AND created_at < DATEADD(day, -30, {{as_of}}) THEN sentiment_score END) AS sentiment_31_90
    FROM visible_tickets
    GROUP BY subscriber_key
)
