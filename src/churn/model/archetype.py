"""Archetype classifier: weak labels (A3) from cancel reason codes and ticket categories, multinomial logit.

The classifier is trained on churned snapshots that have a weak label and applied to every scored subscriber.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from churn.settings import config_dir

ARCHETYPES = ("price_sensitive", "efficacy_dissatisfied", "fatigued", "life_change")
MIN_CONF = 0.40
MIN_MARGIN = 0.10
MIN_TRAIN_ROWS = 20


def _norm(value: Any) -> str | None:
    if value is None or not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value or None


@dataclass(frozen=True)
class WeakLabels:
    reason_codes: dict[str, str]
    ticket_categories: dict[str, str]

    def label(self, reason_code: Any = None, ticket_categories: Iterable[Any] = ()) -> str | None:
        """Archetype for a churner, or None. The reason code wins; tickets decide when it has no mapping."""
        reason = _norm(reason_code)
        if reason in self.reason_codes:
            return self.reason_codes[reason]
        counts = Counter(
            self.ticket_categories[c] for c in map(_norm, ticket_categories) if c in self.ticket_categories
        )
        if not counts:
            return None
        best = max(counts.values())
        # ties resolve in ARCHETYPES order so the label is deterministic
        return next(a for a in ARCHETYPES if counts.get(a) == best)


def load_labels(path: Path | str | None = None) -> WeakLabels:
    data = yaml.safe_load(Path(path or config_dir() / "archetype_labels.yaml").read_text()) or {}
    out = {}
    for section in ("reason_codes", "ticket_categories"):
        mapping = {_norm(str(k)): v for k, v in (data.get(section) or {}).items()}
        unknown = set(mapping.values()) - set(ARCHETYPES)
        if unknown:
            raise ValueError(f"archetype_labels.yaml {section} maps to unknown archetypes: {sorted(unknown)}")
        out[section] = mapping
    return WeakLabels(out["reason_codes"], out["ticket_categories"])


@dataclass(frozen=True)
class Classification:
    archetype: np.ndarray
    confidence: np.ndarray
    probs_json: list[str]
    low_confidence: np.ndarray


def classify(
    probs: np.ndarray, classes: list[str] | tuple[str, ...], min_conf: float = MIN_CONF, min_margin: float = MIN_MARGIN
) -> Classification:
    """Argmax, its confidence and the low-confidence flag, for rows of class probabilities."""
    probs = np.asarray(probs, dtype=float)
    order = np.sort(probs, axis=1)
    top1 = order[:, -1]
    top2 = order[:, -2] if probs.shape[1] > 1 else np.zeros(len(probs))
    eps = 1e-9  # exact boundary values count as confident
    low = (top1 < min_conf - eps) | (top1 - top2 < min_margin - eps)
    return Classification(
        archetype=np.asarray(classes, dtype=object)[probs.argmax(axis=1)],
        confidence=top1,
        probs_json=[json.dumps({c: float(p) for c, p in zip(classes, row, strict=True)}) for row in probs],
        low_confidence=low,
    )


def fit_archetype(X: pd.DataFrame, y: pd.Series, seed: int = 7) -> Any | None:
    """Standardised multinomial logistic regression; None when there are too few labelled churners."""
    if len(y) < MIN_TRAIN_ROWS or y.nunique() < 2:
        return None
    model = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed),
    )
    model.fit(X, y)
    return model


def predict_archetype(model: Any, X: pd.DataFrame) -> Classification:
    """Probabilities over all four archetypes (zero for any the model never saw) classified with the A3 thresholds."""
    raw = model.predict_proba(X)
    probs = np.zeros((len(X), len(ARCHETYPES)))
    for j, cls in enumerate(model.classes_):
        probs[:, ARCHETYPES.index(cls)] = raw[:, j]
    return classify(probs, ARCHETYPES)
