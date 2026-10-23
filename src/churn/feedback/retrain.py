"""Retrain on intervention history and promote only when the holdout stays inside tolerance (R6.3)."""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime
from typing import Any

import pandas as pd
from sklearn.metrics import average_precision_score

from churn.contract.loader import contract_for
from churn.features.registry import FEATURE_COLUMNS
from churn.model.dataset import build_dataset
from churn.model.evaluate import split_snapshots, top_decile_precision
from churn.model.registry import ModelError, load_promoted, save_model
from churn.model.train import fit, fit_archetypes, weak_labelled_churners
from churn.settings import Settings
from churn.warehouse.base import Warehouse

METRICS = ("top_decile_precision", "pr_auc")


def promotion_decision(candidate: dict, current: dict, tolerance: float) -> tuple[bool, str]:
    """Promote when both holdout metrics are at least the current model's minus tolerance."""
    failures = []
    for name in METRICS:
        cand, base = float(candidate[name]), float(current[name])
        if not math.isfinite(cand) or not math.isfinite(base):
            failures.append(f"{name} is undefined")
            continue
        shortfall = base - cand
        if shortfall - tolerance > 1e-9:
            failures.append(f"{name} {cand:.4f} is more than {tolerance} below the current {base:.4f}")
    if failures:
        return False, "rejected: " + "; ".join(failures)
    return True, f"promoted: top-decile precision and PR-AUC within {tolerance} of the current model"


def new_outcome_count(wh: Warehouse, settings: Settings) -> int:
    """Closed treated outcomes measured after the promoted model was registered."""
    ops = settings.schemas.ops
    rows = wh.query(
        f"SELECT created_at FROM {ops}.MODEL_REGISTRY WHERE status = 'promoted' ORDER BY created_at DESC"
    )
    if rows.empty or pd.isna(rows.iloc[0]["created_at"]):
        return 0
    counted = wh.query(
        f"""
        SELECT COUNT(*) AS n FROM {ops}.INTERVENTION_LOG
        WHERE execution_status = 'DISPATCHED'
          AND outcome IN ('retained', 'churned')
          AND outcome_at > :since
        """,
        {"since": pd.Timestamp(rows.iloc[0]["created_at"]).to_pydatetime()},
    )
    return int(counted.iloc[0]["n"])


def retrain_due(wh: Warehouse, settings: Settings) -> bool:
    """True when more closed treated outcomes have arrived than ``retrain.min_new_outcomes``."""
    return new_outcome_count(wh, settings) > settings.retrain.min_new_outcomes


def score_holdout(model, df: pd.DataFrame, settings: Settings) -> dict[str, float | int]:
    """Top-decile precision and PR-AUC on the same time holdout the evaluation split uses."""
    dates = sorted(pd.to_datetime(df["snapshot_date"]).unique())
    splits = split_snapshots(dates, horizon_days=settings.label.horizon_days)
    hold = df[pd.to_datetime(df["snapshot_date"]).isin(set(pd.to_datetime(splits.holdout)))].reset_index(drop=True)
    if hold.empty:
        raise ModelError("holdout is empty; cannot compare a candidate")
    probs = model.predict_proba(hold[FEATURE_COLUMNS])[:, 1]
    y = hold["label"].to_numpy()
    pr_auc = float(average_precision_score(y, probs)) if int(y.sum()) else float("nan")
    precision = float(top_decile_precision(y, probs, hold["subscriber_key"], hold["snapshot_date"]))
    return {
        "top_decile_precision": precision,
        "pr_auc": pr_auc,
        "holdout_rows": len(hold),
    }


def _version_for(wh: Warehouse, settings: Settings, train_end: date, df: pd.DataFrame) -> str:
    fingerprint = hashlib.sha256(
        pd.util.hash_pandas_object(df[["snapshot_date", "label", *FEATURE_COLUMNS]], index=False).values.tobytes()
    ).hexdigest()[:8]
    ops = settings.schemas.ops
    base = f"retrain-{train_end:%Y%m%d}-{fingerprint}"
    version = base
    n = 2
    while not wh.query(
        f"SELECT model_version FROM {ops}.MODEL_REGISTRY WHERE model_version = :v", {"v": version}
    ).empty:
        version = f"{base}-{n}"
        n += 1
    return version


def retrain(wh: Warehouse, settings: Settings, train_end: date | None = None) -> tuple[str, str]:
    """Fit a candidate. Promote it when the holdout stays within tolerance; otherwise record a rejection.

    Returns ``(version, status)``. The current promoted model stays promoted when the candidate is rejected.
    """
    train_end = train_end or settings.synth.end_date
    current_version, current = load_promoted(wh, settings)
    contract = contract_for(settings)
    df = build_dataset(wh, settings, contract, train_end)
    if df.empty:
        raise ModelError("no training snapshots; run `churn synth` first")
    model, fit_metrics = fit(df, settings)
    archetype_model, archetype_metrics = fit_archetypes(df, weak_labelled_churners(wh, settings, contract), settings)
    candidate_metrics = score_holdout(model, df, settings)
    current_metrics = score_holdout(current["model"], df, settings)
    promote, reason = promotion_decision(candidate_metrics, current_metrics, settings.retrain.tolerance)
    version = _version_for(wh, settings, train_end, df)
    metrics: dict[str, Any] = {
        **fit_metrics,
        **archetype_metrics,
        **candidate_metrics,
        "compared_to": current_version,
        "current_top_decile_precision": current_metrics["top_decile_precision"],
        "current_pr_auc": current_metrics["pr_auc"],
        "tolerance": settings.retrain.tolerance,
    }
    status = "promoted" if promote else "rejected"
    save_model(
        wh, settings,
        {"model": model, "features": list(FEATURE_COLUMNS), "version": version, "archetype": archetype_model},
        version, pd.Timestamp(df["snapshot_date"].min()).to_pydatetime(),
        datetime(train_end.year, train_end.month, train_end.day),
        metrics, status=status, reason=reason,
    )
    return version, status
