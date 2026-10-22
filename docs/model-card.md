# Model card: churn risk model

- **Model version:** `lgbm-20250630-29a8eca6`
- **Type:** LightGBM gradient-boosted trees, isotonic-calibrated when enough calibration churners exist; TreeSHAP factors; a multinomial logistic archetype classifier on top.
- **Report:** `docs/eval-report.md` (regenerate with `churn evaluate`).

## Intended use

Rank active subscribers by the probability of a voluntary cancellation in the next 30 days, so the retention agent can pick an intervention for the at-risk ones. Not for pricing, credit or any decision outside retention outreach, and not a statement about any individual's intent.

## Label and data

- Positive: voluntary cancellation within 30 days after the scoring date (A1). Involuntary cancellations are excluded.
- Training data: monthly point-in-time snapshots of active subscribers built by the feature SQL, so each row only sees data from before its scoring date.
- Data in this repository is synthetic. Numbers below say nothing about real Thesis customers until the model is evaluated on their data. The 90-day impact target can only be checked after go-live.

## Evaluation (holdout 2025-02-28 to 2025-04-30)

- Splits: training 2023-09-30 to 2024-09-30; validation 2024-11-30 to 2024-12-31 (2 snapshots after the purge, boundary selection only); holdout the last 3 months. Synthetic scale: small.
- The numbers come from an evaluation model fitted on the training split only, not from the promoted `lgbm-20250630-29a8eca6` bundle. The production model is refit on all snapshots.
- Rows: 55536; churn labels: 1700 (base rate 3.1%)
- ROC-AUC 0.824, PR-AUC 0.478, Brier 0.019
- **Recommended at-risk boundary:** 0.030, the highest threshold whose recall on the validation split is at least 0.65 (validation recall 71.2% over 969 validation churners). It is a recommendation only: the runtime tiers and `AGENT_CONFIG` are not changed by this report.
- **Holdout recall at the boundary:** 62.9%: 1120 of 1781 cancelling churners caught, 64 with no score before cancelling, 597 with a last score below the boundary.
- **Holdout precision at the boundary (reported, no target):** 10.3%: 1115 churners among 10792 rows scored at or above the boundary.
- Top-decile precision 17.3% against a ceiling of 30.6% at the measured base rate; the earlier 0.70 top-decile target was replaced because it is unreachable under this label.

| Requirement | Target | Measured | Status | Reason |
|---|---|---|---|---|
| R3.6 top_share_precision | > 50% | 67.1% | PASS | target met |
| R3.6 top_decile_lift | >= 4.0x | 5.65x | PASS | target met |
| R3.6 pre_cancellation_recall | > 60% | 62.9% | PASS | target met |

Status summary: top_share_precision PASS; top_decile_lift PASS; pre_cancellation_recall PASS.

## Limitations

- A target marked MISS is a missed target. It is reported as measured, not tuned away.
- Metrics from a small or synthetic population are noisy; read the churn-label counts next to them.
- Archetypes come from weak labels (cancel reason codes and ticket categories), so they are only as good as those signals; low-confidence archetypes are flagged at scoring time.
- The model has not been audited for differences in accuracy between customer groups.
