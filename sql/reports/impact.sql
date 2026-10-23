-- Treated vs control churn rate (R6.6). Unknown outcomes are left out of both rates.
-- Control is a HOLDOUT decision. Everyone else with a closed outcome is treated.
SELECT
    CASE WHEN l.intervention_type = 'HOLDOUT' THEN 'control' ELSE 'treated' END AS arm,
    COUNT(*) AS n,
    SUM(CASE WHEN o.outcome = 'churned' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS churn_rate
FROM CHURN_OPS.OUTCOMES o
JOIN CHURN_OPS.INTERVENTION_LOG l ON l.decision_id = o.decision_id
WHERE o.outcome IN ('retained', 'churned')
GROUP BY CASE WHEN l.intervention_type = 'HOLDOUT' THEN 'control' ELSE 'treated' END
