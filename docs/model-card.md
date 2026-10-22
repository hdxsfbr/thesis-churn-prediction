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

## Evaluation (last 3 months, 2025-02-28 to 2025-04-30)

- Numbers come from a holdout refit twin (trained on snapshots before the holdout), not from the promoted `lgbm-20250630-29a8eca6` bundle itself.
- Rows: 55536; churn labels: 1700 (base rate 3.1%)
- ROC-AUC 0.824, PR-AUC 0.484, Brier 0.019

| Requirement | Target | Measured | Status | Reason |
|---|---|---|---|---|
| R3.6 top_decile_precision | > 70% | 16.7% | MISS | top decile precision 0.17 is not above the target 0.70; holdout has 1700 churn labels in 55536 scored rows (base rate 3.1%); the best possible top-decile precision is 0.31 at this base rate |
| R3.6 pre_cancellation_recall | > 60% | 40.0% | MISS | pre cancellation recall 0.40 is not above the target 0.60; holdout has 1700 churn labels in 55536 scored rows (base rate 3.1%); of 1781 cancelling churners, 713 were caught, 64 had no score before cancelling, and 1004 had a last score below the at-risk boundary (0.20) |

Status summary: top_decile_precision MISS; pre_cancellation_recall MISS.

## Limitations

- A target marked MISS is a missed target. It is reported as measured, not tuned away.
- Metrics from a small or synthetic population are noisy; read the churn-label counts next to them.
- Archetypes come from weak labels (cancel reason codes and ticket categories), so they are only as good as those signals; low-confidence archetypes are flagged at scoring time.
- The model has not been audited for differences in accuracy between customer groups.
