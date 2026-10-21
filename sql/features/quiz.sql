-- Quiz domain: one row per answer, grouped into sessions by quiz_session_id. Defines quiz_agg.
-- stub quiz_responses: subscriber_key VARCHAR, quiz_session_id VARCHAR, question_key VARCHAR, answer_value VARCHAR, answered_at TIMESTAMP, recommended_sku VARCHAR, loaded_at TIMESTAMP
visible_quiz AS (
    SELECT subscriber_key, quiz_session_id, answered_at
    FROM quiz_responses
    WHERE answered_at < {{as_of}}
      AND loaded_at <= {{as_of}}
),
quiz_sessions AS (
    SELECT subscriber_key, quiz_session_id, MIN(answered_at) AS started_at, MAX(answered_at) AS last_at
    FROM visible_quiz
    GROUP BY subscriber_key, quiz_session_id
),
quiz_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS n_sessions,
           MAX(last_at) AS last_quiz_at,
           MIN(started_at) AS first_started_at,
           SUM(CASE WHEN started_at >= DATEADD(day, -365, {{as_of}}) THEN 1 ELSE 0 END) AS sessions_365,
           SUM(CASE WHEN started_at >= DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END) AS sessions_90,
           SUM(CASE WHEN started_at >= DATEADD(day, -180, {{as_of}})
                     AND started_at < DATEADD(day, -90, {{as_of}}) THEN 1 ELSE 0 END) AS sessions_prior90
    FROM quiz_sessions
    GROUP BY subscriber_key
)
