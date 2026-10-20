-- requires: support_tickets
-- columns: subscriber_key:VARCHAR, tickets_total, tickets_30, tickets_90, escalated_90, sentiment_30, sentiment_prior
-- Support tickets. Visible: created_at < :as_of, loaded_at <= :as_of.
-- Recent = [as_of-30d, as_of); prior = [as_of-90d, as_of-30d); sentiment_score is averaged within each window.
visible_tickets AS (
    SELECT subscriber_key, created_at, escalated, sentiment_score
    FROM support_tickets
    WHERE created_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
      AND subscriber_key IN (SELECT subscriber_key FROM active)
),
support_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS tickets_total,
           SUM(CASE WHEN created_at >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS tickets_30,
           SUM(CASE WHEN created_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS tickets_90,
           SUM(CASE WHEN created_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) AND escalated THEN 1 ELSE 0 END) AS escalated_90,
           AVG(CASE WHEN created_at >= DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN sentiment_score END) AS sentiment_30,
           AVG(CASE WHEN created_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP))
                     AND created_at < DATEADD(day, -30, CAST(:as_of AS TIMESTAMP)) THEN sentiment_score END) AS sentiment_prior
    FROM visible_tickets
    GROUP BY subscriber_key
)
