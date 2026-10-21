"""Score a feature frame with the promoted model."""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

from churn.features.builder import as_of_ts
from churn.model.archetype import predict_archetype
from churn.model.explain import explain
from churn.model.registry import load_promoted
from churn.runtime_config import RuntimeConfig, assign_tiers
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def _archetype_columns(model, X: pd.DataFrame) -> dict:
    """Archetype, confidence, probabilities and low-confidence flag for every row (nulls when no classifier)."""
    if model is None:
        return {"archetype": None, "archetype_conf": None, "archetype_probs": None, "archetype_low_conf": None}
    c = predict_archetype(model, X)
    return {
        "archetype": c.archetype,
        "archetype_conf": c.confidence,
        "archetype_probs": c.probs_json,
        "archetype_low_conf": c.low_confidence,
    }


def score_frame(
    wh: Warehouse, settings: Settings, features: pd.DataFrame, run_id: str, as_of: date | datetime,
    config: RuntimeConfig | None = None,
) -> pd.DataFrame:
    """One RISK_SCORES row per feature row; tier comes from the run's config snapshot (None if invalid)."""
    version, bundle = load_promoted(wh, settings)
    X = features[bundle["features"]]
    probs = bundle["model"].predict_proba(X)[:, 1]
    factors = explain(bundle["model"], X)
    archetype = _archetype_columns(bundle.get("archetype"), X)
    return pd.DataFrame(
        {
            "run_id": run_id,
            "subscriber_key": features["subscriber_key"].to_numpy(),
            "as_of": as_of_ts(as_of),
            "churn_prob": probs,
            "tier": assign_tiers(probs, config.tiers) if config and config.tiers else None,
            "top_factors": [json.dumps(f) for f in factors],
            **archetype,
            "model_version": version,
            "scored_at": datetime.now(),
        }
    )
