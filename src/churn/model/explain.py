"""Explanations: LightGBM TreeSHAP contributions (``pred_contrib``) rendered as ranked top factors."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from churn.features.registry import render_label

TOP_K = 5
MIN_FACTORS = 3


def base_booster(model: Any) -> Any:
    """The LightGBM classifier inside a (possibly calibrated) model."""
    if hasattr(model, "calibrated_classifiers_"):
        inner = model.calibrated_classifiers_[0].estimator
        return getattr(inner, "estimator", inner)  # FrozenEstimator wraps the fitted model
    return model


def contributions(model: Any, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per-feature log-odds contributions (n x f) and the bias (n,); contributions + bias = raw margin."""
    raw = np.asarray(base_booster(model).predict(X, pred_contrib=True), dtype=float)
    return raw[:, :-1], raw[:, -1]


def _direction(c: float) -> str:
    return "up" if c > 0 else "down" if c < 0 else "neutral"


def top_factors(
    contribs: np.ndarray, X: pd.DataFrame, k: int = TOP_K, min_factors: int = MIN_FACTORS
) -> list[list[dict]]:
    """Top-k factors per row by |contribution|, as {feature, label, direction, contribution, value}.

    Features whose value is missing are not named (there is nothing to say about them); a row
    therefore has fewer than ``min_factors`` only when fewer features are non-missing.
    """
    names = list(X.columns)
    values = X.to_numpy(dtype=float)
    out: list[list[dict]] = []
    for row_c, row_v in zip(contribs, values, strict=True):
        order = sorted(
            (j for j in range(len(names)) if not np.isnan(row_v[j])),
            key=lambda j: (-abs(row_c[j]), j),
        )
        n = max(k, min_factors)
        factors = []
        for j in order[:n]:
            c = float(row_c[j])
            factors.append(
                {
                    "feature": names[j],
                    "label": render_label(names[j], float(row_v[j])),
                    "direction": _direction(c),
                    "contribution": c,
                    "value": float(row_v[j]),
                }
            )
        out.append(factors)
    return out


def explain(model: Any, X: pd.DataFrame, k: int = TOP_K) -> list[list[dict]]:
    """Contributions then ranked factors for every row of X, using X's columns exactly as given.

    Callers pass the columns the model was trained on (the promoted bundle's feature list).
    """
    contribs, _ = contributions(model, X)
    return top_factors(contribs, X, k=k)
