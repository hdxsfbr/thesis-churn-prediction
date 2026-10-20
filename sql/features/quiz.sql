-- requires: quiz_responses
-- columns: subscriber_key:VARCHAR, quizzes_total, last_quiz_at:TIMESTAMP, retakes_90, retakes_prior_90, retakes_365
-- Quiz completions. quiz_responses has one row per answer (ARCHITECTURE 4.2); a completion is one quiz_session_id,
-- dated at its latest visible answer (MAX(answered_at)). Visible answers: answered_at < :as_of, loaded_at <= :as_of.
-- A retake is any completion after the subscriber's first one (the first may lie outside the window).
visible_quiz AS (
    SELECT subscriber_key, quiz_session_id, MAX(answered_at) AS completed_at
    FROM quiz_responses
    WHERE answered_at < CAST(:as_of AS TIMESTAMP)
      AND loaded_at <= CAST(:as_of AS TIMESTAMP)
      AND subscriber_key IN (SELECT subscriber_key FROM active)
    GROUP BY subscriber_key, quiz_session_id
),
visible_quiz_n AS (
    SELECT subscriber_key, completed_at,
           ROW_NUMBER() OVER (PARTITION BY subscriber_key ORDER BY completed_at, quiz_session_id) AS nth
    FROM visible_quiz
),
quiz_agg AS (
    SELECT subscriber_key,
           COUNT(*) AS quizzes_total,
           MAX(completed_at) AS last_quiz_at,
           SUM(CASE WHEN nth > 1 AND completed_at >= DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS retakes_90,
           SUM(CASE WHEN nth > 1 AND completed_at >= DATEADD(day, -180, CAST(:as_of AS TIMESTAMP))
                     AND completed_at < DATEADD(day, -90, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS retakes_prior_90,
           SUM(CASE WHEN nth > 1 AND completed_at >= DATEADD(day, -365, CAST(:as_of AS TIMESTAMP)) THEN 1 ELSE 0 END) AS retakes_365
    FROM visible_quiz_n
    GROUP BY subscriber_key
)
