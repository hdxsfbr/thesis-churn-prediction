-- requires: support_tickets
-- columns: subscriber_key, n_tickets, tickets_30, tickets_90, escalated_90, sentiment_30, sentiment_90
-- Support ticket features per subscriber. Snowflake dialect. Parameter: :as_of.
-- Only tickets with created_at < :as_of that were loaded at or before :as_of are visible.
support_f AS (
    SELECT subscriber_key,
           COUNT(*) AS n_tickets,
           SUM(CASE WHEN created_at >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS tickets_30,
           SUM(CASE WHEN created_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS tickets_90,
           SUM(CASE WHEN created_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP))
                     AND CAST(escalated AS INTEGER) = 1 THEN 1 ELSE 0 END) AS escalated_90,
           AVG(CASE WHEN created_at >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN sentiment_score END) AS sentiment_30,
           AVG(CASE WHEN created_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN sentiment_score END) AS sentiment_90
    FROM support_tickets
    WHERE created_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
    GROUP BY subscriber_key
)
