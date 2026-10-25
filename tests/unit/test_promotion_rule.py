"""The promotion rule is a tolerance on the two holdout metrics, and the outcome count can mark a retrain due."""

from datetime import datetime

import pandas as pd

from churn.feedback.retrain import new_outcome_count, promotion_decision, retrain_due
from churn.settings import RetrainSettings
from churn.warehouse.migrate import apply_migrations


def test_promote_when_within_tolerance():
    current = {"top_decile_precision": 0.40, "pr_auc": 0.25}
    ok, reason = promotion_decision(
        {"top_decile_precision": 0.39, "pr_auc": 0.24}, current, 0.01
    )
    assert ok
    assert reason.startswith("promoted:")
    ok, reason = promotion_decision(
        {"top_decile_precision": 0.389, "pr_auc": 0.24}, current, 0.01
    )
    assert not ok
    assert "top_decile_precision" in reason
    ok, reason = promotion_decision(
        {"top_decile_precision": 0.38, "pr_auc": 0.25}, current, 0.01
    )
    assert not ok
    assert "top_decile_precision" in reason
    ok, reason = promotion_decision(
        {"top_decile_precision": 0.40, "pr_auc": float("nan")}, current, 0.01
    )
    assert not ok
    assert "undefined" in reason


def test_retrain_due_only_after_enough_new_outcomes(fresh_wh, settings):
    apply_migrations(fresh_wh, settings)
    ops = settings.schemas.ops
    fresh_wh.insert_frame(
        f"{ops}.MODEL_REGISTRY",
        pd.DataFrame([{
            "model_version": "current",
            "artefact_uri": "unused",
            "sha256": "abc",
            "status": "promoted",
            "reason": "first model",
            "created_at": datetime(2025, 1, 1),
        }]),
    )
    tuned = settings.model_copy(update={"retrain": RetrainSettings(min_new_outcomes=1, tolerance=0.01)})
    assert new_outcome_count(fresh_wh, tuned) == 0
    assert not retrain_due(fresh_wh, tuned)
    fresh_wh.insert_frame(
        f"{ops}.INTERVENTION_LOG",
        pd.DataFrame([
            {
                "decision_id": "t18-a",
                "execution_status": "DISPATCHED",
                "outcome": "retained",
                "outcome_at": datetime(2025, 6, 1),
            },
            {
                "decision_id": "t18-b",
                "execution_status": "DISPATCHED",
                "outcome": "churned",
                "outcome_at": datetime(2025, 6, 2),
            },
            {
                "decision_id": "t18-hold",
                "execution_status": "HOLDOUT",
                "outcome": "churned",
                "outcome_at": datetime(2025, 6, 2),
            },
        ]),
    )
    assert new_outcome_count(fresh_wh, tuned) == 2
    assert retrain_due(fresh_wh, tuned)
