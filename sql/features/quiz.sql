-- requires: quiz_responses
-- columns: subscriber_key, n_answers, first_quiz_at, last_quiz_at, sessions_90, sessions_365
-- Quiz features per subscriber. Snowflake dialect. Parameter: :as_of.
-- Only answers with answered_at < :as_of that were loaded at or before :as_of are visible.
quiz_f AS (
    SELECT subscriber_key,
           COUNT(*) AS n_answers,
           MIN(answered_at) AS first_quiz_at,
           MAX(answered_at) AS last_quiz_at,
           COUNT(DISTINCT CASE WHEN answered_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN quiz_session_id END) AS sessions_90,
           COUNT(DISTINCT CASE WHEN answered_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN quiz_session_id END) AS sessions_365
    FROM quiz_responses
    WHERE answered_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
    GROUP BY subscriber_key
)
