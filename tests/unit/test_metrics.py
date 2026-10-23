"""Coverage leaves holdout out of the denominator. Autonomy is autonomous over dispatched."""

from churn.feedback.metrics import autonomy, coverage


def test_coverage_excludes_holdout_from_denominator():
    assert coverage(8, 2, 12, 2) == (8 + 2) / (12 - 2)
    assert coverage(8, 2, 12, 0) == 10 / 12
    assert coverage(0, 0, 2, 2) is None


def test_autonomy_rate_formula():
    assert autonomy(7, 3) == 0.7
    assert autonomy(0, 4) == 0
    assert autonomy(0, 0) is None
