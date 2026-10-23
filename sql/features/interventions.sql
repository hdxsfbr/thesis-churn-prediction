-- Dispatched intervention history. Holdout is logged but it is not a contact, so it stays out.
-- {{ops_schema}} is the ops schema (CHURN_OPS). {{as_of}} is the bound scoring timestamp.
-- Defines intervention_agg and intervention_last. last_type is the intervention name; the builder encodes it.
intervention_visible AS (
    SELECT subscriber_key, intervention_type, dispatched_at
    FROM {{ops_schema}}.INTERVENTION_LOG
    WHERE execution_status = 'DISPATCHED'
      AND intervention_type <> 'HOLDOUT'
      AND dispatched_at IS NOT NULL
      AND dispatched_at < {{as_of}}
),
intervention_agg AS (
    SELECT subscriber_key,
           SUM(CASE WHEN dispatched_at >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END) AS n_90,
           MAX(dispatched_at) AS last_at
    FROM intervention_visible
    GROUP BY subscriber_key
),
intervention_last AS (
    SELECT subscriber_key, intervention_type AS last_type
    FROM (
        SELECT subscriber_key,
               intervention_type,
               ROW_NUMBER() OVER (
                   PARTITION BY subscriber_key ORDER BY dispatched_at DESC, intervention_type
               ) AS rn
        FROM intervention_visible
    ) ranked
    WHERE rn = 1
)
