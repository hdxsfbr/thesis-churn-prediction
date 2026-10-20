"""Score a feature frame with the promoted model."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from churn.features.builder import as_of_ts
from churn.model.registry import load_promoted
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def score_frame(
    wh: Warehouse, settings: Settings, features: pd.DataFrame, run_id: str, as_of: date | datetime
) -> pd.DataFrame:
    """One RISK_SCORES row per feature row."""
    version, bundle = load_promoted(wh, settings)
    probs = bundle["model"].predict_proba(features[bundle["features"]])[:, 1]
    return pd.DataFrame(
        {
            "run_id": run_id,
            "subscriber_key": features["subscriber_key"].to_numpy(),
            "as_of": as_of_ts(as_of),
            "churn_prob": probs,
            "model_version": version,
            "scored_at": datetime.now(),
        }
    )
