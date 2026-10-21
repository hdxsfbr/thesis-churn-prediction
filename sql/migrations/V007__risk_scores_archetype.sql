-- Archetype per score: label, confidence (max probability), probabilities as JSON text and the low-confidence flag.
ALTER TABLE CHURN_OPS.RISK_SCORES ADD COLUMN IF NOT EXISTS archetype VARCHAR;
ALTER TABLE CHURN_OPS.RISK_SCORES ADD COLUMN IF NOT EXISTS archetype_conf DOUBLE;
ALTER TABLE CHURN_OPS.RISK_SCORES ADD COLUMN IF NOT EXISTS archetype_probs VARCHAR;
ALTER TABLE CHURN_OPS.RISK_SCORES ADD COLUMN IF NOT EXISTS archetype_low_conf BOOLEAN;
