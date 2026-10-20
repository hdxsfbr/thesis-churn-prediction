"""Acceptance tests for T6: TreeSHAP contributions and the ranked factors rendered from them."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

from churn.features import registry
from churn.features.registry import FEATURE_COLUMNS
from churn.model.explain import base_booster, contributions, explain, top_factors


@pytest.fixture(scope="module")
def fitted():
    rng = np.random.default_rng(7)
    n = 600
    X = pd.DataFrame(
        {
            "tenure_days": rng.integers(10, 900, n).astype(float),
            "price_usd": rng.uniform(10, 60, n),
            "interval_days": rng.choice([14, 30, 60], n).astype(float),
            "orders_total": rng.integers(0, 40, n).astype(float),
            "orders_90": rng.integers(0, 6, n).astype(float),
            "orders_365": rng.integers(0, 20, n).astype(float),
            "days_since_last_order": rng.uniform(0, 200, n),
            "overdue_ratio": rng.uniform(0, 6, n),
            "discount_share_365": rng.uniform(0, 0.5, n),
            "refund_count_365": rng.integers(0, 3, n).astype(float),
        }
    )
    for extra in FEATURE_COLUMNS:  # the rest of the catalogue: uninformative noise
        if extra not in X:
            X[extra] = rng.uniform(0, 5, n)
    X = X[FEATURE_COLUMNS]
    logit = 0.02 * X["days_since_last_order"] - 0.003 * X["tenure_days"] + 0.5 * X["refund_count_365"] - 1.5
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-logit))).astype(int)
    X.loc[rng.choice(n, 50, replace=False), "discount_share_365"] = np.nan
    base = lgb.LGBMClassifier(n_estimators=40, num_leaves=8, random_state=1, verbose=-1, n_jobs=1)
    base.fit(X, y)
    return base, X


def test_contributions_plus_bias_equal_margin(fitted):
    base, X = fitted
    contribs, bias = contributions(base, X)
    margin = base.predict(X, raw_score=True)
    assert contribs.shape == (len(X), len(FEATURE_COLUMNS))
    assert np.abs(contribs.sum(axis=1) + bias - margin).max() < 1e-6


def test_contributions_through_calibration_wrapper(fitted):
    base, X = fitted
    wrapped = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid")
    wrapped.fit(X, (X["days_since_last_order"] > 100).astype(int))
    assert base_booster(wrapped) is base
    contribs, bias = contributions(wrapped, X)
    assert np.abs(contribs.sum(axis=1) + bias - base.predict(X, raw_score=True)).max() < 1e-6


def test_factor_direction_matches_sign(fitted):
    base, X = fitted
    contribs, _ = contributions(base, X)
    for factors in top_factors(contribs, X):
        for f in factors:
            assert set(f) == {"feature", "label", "direction", "contribution", "value"}
            if f["contribution"] > 0:
                assert f["direction"] == "up"
            elif f["contribution"] < 0:
                assert f["direction"] == "down"
            else:
                assert f["direction"] == "neutral"


def test_factors_ranked_by_abs_contribution(fitted):
    base, X = fitted
    contribs, _ = contributions(base, X)
    out = top_factors(contribs, X, k=5)
    assert len(out) == len(X)
    for i, factors in enumerate(out):
        assert 3 <= len(factors) <= 5
        mags = [abs(f["contribution"]) for f in factors]
        assert mags == sorted(mags, reverse=True)
        # nothing outside the list outranks the weakest listed factor
        listed = {f["feature"] for f in factors}
        row = pd.Series(contribs[i], index=FEATURE_COLUMNS)
        row = row[X.iloc[i].notna()]
        rest = row[[c for c in row.index if c not in listed]].abs()
        assert rest.empty or rest.max() <= mags[-1] + 1e-12


def test_missing_features_are_not_factors(fitted):
    base, X = fitted
    contribs, _ = contributions(base, X)
    out = top_factors(contribs, X)
    missing_rows = np.flatnonzero(X["discount_share_365"].isna().to_numpy())
    assert len(missing_rows)
    for i in missing_rows:
        assert "discount_share_365" not in {f["feature"] for f in out[i]}


def test_explain_uses_the_models_own_columns(fitted):
    _, X = fitted
    cols = ["days_since_last_order", "tenure_days", "refund_count_365", "price_usd"]  # subset, reordered
    y = (X["days_since_last_order"] > 100).astype(int)
    sub = X[cols]
    model = lgb.LGBMClassifier(n_estimators=20, num_leaves=4, random_state=1, verbose=-1, n_jobs=1).fit(sub, y)
    out = explain(model, sub)
    assert len(out) == len(sub)
    assert {f["feature"] for row in out for f in row} <= set(cols)
    contribs, _ = contributions(model, sub)
    assert contribs.shape[1] == len(cols)


def test_render_label_fails_clearly_without_template(monkeypatch):
    bare = registry.Feature("tenure_days", "subscription", "d", "never missing", "", "days")
    monkeypatch.setitem(registry.FEATURE_BY_NAME, "tenure_days", bare)
    with pytest.raises(ValueError, match="tenure_days"):
        registry.render_label("tenure_days", 5.0)
