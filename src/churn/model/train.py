"""Fit LightGBM on time-ordered snapshots, calibrate isotonically on the latest snapshots, register."""

from __future__ import annotations

import hashlib
import random
from datetime import date

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import roc_auc_score

from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.features.registry import FEATURE_COLUMNS
from churn.model.archetype import fit_archetype, load_labels
from churn.model.dataset import build_dataset
from churn.model.registry import ModelError, register
from churn.settings import Settings
from churn.warehouse.base import Warehouse

MIN_CAL_POSITIVES = 30


def fit(df: pd.DataFrame, settings: Settings):
    """Return (calibrated model, metrics). Snapshots are split by date, never by row."""
    random.seed(settings.model.seed)
    np.random.seed(settings.model.seed)
    df = df.sort_values(["snapshot_date", "subscriber_key"]).reset_index(drop=True)
    dates = sorted(df["snapshot_date"].unique())
    n_cal = max(1, int(len(dates) * settings.model.calibration_share))
    cal_dates = set(dates[-n_cal:])
    is_cal = df["snapshot_date"].isin(cal_dates)
    fit_df, cal_df = df[~is_cal], df[is_cal]
    if fit_df["label"].nunique() < 2:
        raise ModelError("training snapshots contain a single class; cannot fit")

    params = dict(settings.model.lightgbm)
    params.update(random_state=settings.model.seed, n_jobs=1, deterministic=True, force_row_wise=True, verbose=-1)
    base = lgb.LGBMClassifier(**params)
    base.fit(fit_df[FEATURE_COLUMNS], fit_df["label"])

    model = base
    calibrated = False
    metrics: dict = {"rows_fit": len(fit_df), "rows_calibration": len(cal_df), "positives_fit": int(fit_df["label"].sum())}
    if cal_df["label"].sum() >= MIN_CAL_POSITIVES and cal_df["label"].nunique() == 2:
        model = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
        model.fit(cal_df[FEATURE_COLUMNS], cal_df["label"])
        calibrated = True
    if cal_df["label"].nunique() == 2:
        metrics["auc_calibration_fold"] = round(float(roc_auc_score(cal_df["label"], model.predict_proba(cal_df[FEATURE_COLUMNS])[:, 1])), 4)
    metrics["calibrated"] = calibrated
    return model, metrics


def weak_labelled_churners(wh: Warehouse, settings: Settings, contract) -> dict[str, str]:
    """subscriber_key -> archetype for voluntary churners whose reason code or tickets map to one (A3)."""
    labels = load_labels()
    ctes = contract_ctes(contract, settings.key_pepper, ["subscriptions"])
    reasons = wh.query(
        f"WITH {ctes} SELECT subscriber_key, cancel_reason_code FROM subscriptions WHERE cancel_type = 'voluntary'"
    )
    tickets: dict[str, list[str]] = {}
    if "support_tickets" in contract.views:
        ctes = contract_ctes(contract, settings.key_pepper, ["support_tickets"])
        t = wh.query(f"WITH {ctes} SELECT subscriber_key, category FROM support_tickets WHERE category IS NOT NULL")
        for key, category in zip(t["subscriber_key"], t["category"], strict=True):
            tickets.setdefault(key, []).append(category)
    out = {}
    for key, reason in zip(reasons["subscriber_key"], reasons["cancel_reason_code"], strict=True):
        label = labels.label(reason, tickets.get(key, ()))
        if label:
            out[key] = label
    return out


def fit_archetypes(df: pd.DataFrame, weak: dict[str, str], settings: Settings):
    """Archetype classifier on churned snapshots (label = 1) that have a weak label; None when too few."""
    churned = df[df["label"] == 1]
    y = churned["subscriber_key"].map(weak).dropna()
    model = fit_archetype(churned.loc[y.index, FEATURE_COLUMNS], y, settings.model.seed)
    return model, {"archetype_rows": len(y), "archetype_trained": model is not None}


def train(wh: Warehouse, settings: Settings, train_end: date | None = None) -> str:
    """Build snapshots, fit, register and promote. Returns the model version."""
    train_end = train_end or settings.synth.end_date
    contract = contract_for(settings)
    df = build_dataset(wh, settings, contract, train_end)
    if df.empty:
        raise ModelError("no training snapshots; run `churn synth` first")
    model, metrics = fit(df, settings)
    archetype_model, archetype_metrics = fit_archetypes(df, weak_labelled_churners(wh, settings, contract), settings)
    metrics.update(archetype_metrics)

    fingerprint = hashlib.sha256(
        pd.util.hash_pandas_object(df[["snapshot_date", "label", *FEATURE_COLUMNS]], index=False).values.tobytes()
    ).hexdigest()[:8]
    version = f"lgbm-{train_end:%Y%m%d}-{fingerprint}"
    bundle = {"model": model, "features": list(FEATURE_COLUMNS), "version": version, "archetype": archetype_model}
    metrics["positive_rate"] = round(float(df["label"].mean()), 4)
    return register(
        wh, settings, bundle, version,
        train_start=df["snapshot_date"].min().to_pydatetime(),
        train_end=df["snapshot_date"].max().to_pydatetime(),
        metrics=metrics,
    )
