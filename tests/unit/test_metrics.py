"""Coverage leaves holdout out of the denominator. Autonomy is autonomous over dispatched."""

import pandas as pd

from churn.feedback.metrics import autonomy, compute_run_metrics, coverage


def test_coverage_excludes_holdout_from_denominator():
    assert coverage(8, 2, 12, 2) == (8 + 2) / (12 - 2)
    assert coverage(8, 2, 12, 0) == 10 / 12
    assert coverage(0, 0, 2, 2) is None


def test_autonomy_rate_formula():
    assert autonomy(7, 3) == 0.7
    assert autonomy(0, 4) == 0
    assert autonomy(0, 0) is None


def _row(kind, approval):
    row = {column: "x" for column in (
        "subscriber_key", "run_id", "risk", "tier", "archetype", "top_factors",
        "intervention_type", "rationale", "approval_status", "execution_status",
        "decided_at", "response", "outcome",
    )}
    row.update({
        "intervention_type": kind,
        "execution_status": "DISPATCHED",
        "approval_status": approval,
        "content_source": "template",
        "outcome": "PENDING",
    })
    return row


def test_escalations_are_outside_the_autonomy_rate():
    only = compute_run_metrics(pd.DataFrame([_row("escalation", "NOT_REQUIRED")]), at_risk=1)
    assert "autonomy_rate" not in only
    mixed = pd.DataFrame(
        [_row("escalation", "NOT_REQUIRED")] * 10
        + [_row("checkin_email", "NOT_REQUIRED")] * 7
        + [_row("formulation", "APPROVED")] * 3
    )
    assert compute_run_metrics(mixed, at_risk=20)["autonomy_rate"] == 0.7
