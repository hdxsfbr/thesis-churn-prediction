"""Closed treated outcomes move the Beta posterior once."""

from datetime import datetime

import pandas as pd

from churn.feedback.policy_stats import fold_beta, update_posteriors
from churn.runtime_config import load_pinned, seed
from churn.warehouse.migrate import apply_migrations

BUCKET = "high:90-365"
SCORES = '{"formulation": {"bucket": "high:90-365"}}'


def _ready(wh, settings):
    apply_migrations(wh, settings)
    seed(wh, settings)
    return load_pinned(wh, settings, "t17-unit")


def _log(decision_id, *, outcome, intervention="formulation", execution="DISPATCHED", scores=SCORES):
    return {
        "decision_id": decision_id,
        "archetype": "efficacy_dissatisfied",
        "intervention_type": intervention,
        "policy_scores": scores,
        "outcome": outcome,
        "execution_status": execution,
        "outcome_at": datetime(2025, 6, 1),
    }


def _posterior(wh, settings):
    ops = settings.schemas.ops
    frame = wh.query(
        f"""
        SELECT alpha, beta, n FROM {ops}.POLICY_STATS
        WHERE archetype = 'efficacy_dissatisfied'
          AND intervention_type = 'formulation'
          AND context_bucket = :b
        """,
        {"b": BUCKET},
    )
    return None if frame.empty else frame.iloc[0]


def test_retained_increments_alpha_churned_increments_beta(fresh_wh, settings):
    assert fold_beta((20, 80), ["retained"]) == (21, 80)
    assert fold_beta((20, 80), ["churned"]) == (20, 81)
    assert fold_beta((20, 80), ["unknown"]) == (20, 80)
    cfg = _ready(fresh_wh, settings)
    ops = settings.schemas.ops
    fresh_wh.insert_frame(
        f"{ops}.INTERVENTION_LOG",
        pd.DataFrame([
            _log("kept", outcome="retained"),
            _log("gone", outcome="churned"),
            _log("hold", outcome="retained", execution="HOLDOUT"),
            _log("later", outcome="unknown"),
        ]),
    )
    assert update_posteriors(fresh_wh, settings, "t17-unit", cfg) == 2
    row = _posterior(fresh_wh, settings)
    # Default efficacy formulation prior is (80, 20). Holdout and unknown add nothing.
    assert (row["alpha"], row["beta"], row["n"]) == (81, 21, 2)


def test_same_outcome_not_counted_twice(fresh_wh, settings):
    cfg = _ready(fresh_wh, settings)
    ops = settings.schemas.ops
    fresh_wh.insert_frame(f"{ops}.INTERVENTION_LOG", pd.DataFrame([_log("once", outcome="retained")]))
    update_posteriors(fresh_wh, settings, "t17-first", cfg)
    first = _posterior(fresh_wh, settings)
    update_posteriors(fresh_wh, settings, "t17-second", cfg)
    second = _posterior(fresh_wh, settings)
    assert (second["alpha"], second["beta"], second["n"]) == (first["alpha"], first["beta"], first["n"])
    assert second["alpha"] == 81


def test_decay_shrinks_older_evidence():
    alpha, beta = fold_beta((10, 10), ["retained", "churned"], decay=0.5)
    assert alpha == 3
    assert beta == 3.5
