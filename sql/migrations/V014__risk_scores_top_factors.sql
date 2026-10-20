-- Top factors per score: JSON array of {feature, label, direction, contribution, value}, ranked by |contribution|.
ALTER TABLE CHURN_OPS.RISK_SCORES ADD COLUMN IF NOT EXISTS top_factors VARCHAR;
