"""Fit LightGBM on time-ordered snapshots, calibrate isotonically on the latest snapshots, register."""

from __future__ import annotations

import hashlib
from datetime import date

import lightgbm as lgb
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import roc_auc_score

from churn.contract.loader import contract_for
from churn.features.registry import FEATURE_COLUMNS
from churn.model.dataset import build_dataset
from churn.model.registry import ModelError, register
from churn.settings import Settings
from churn.warehouse.base import Warehouse

MIN_CAL_POSITIVES = 30


def fit(df: pd.DataFrame, settings: Settings):
    """Return (calibrated model, metrics). Snapshots are split by date, never by row."""
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


def train(wh: Warehouse, settings: Settings, train_end: date | None = None) -> str:
    """Build snapshots, fit, register and promote. Returns the model version."""
    train_end = train_end or settings.synth.end_date
    contract = contract_for(settings)
    df = build_dataset(wh, settings, contract, train_end)
    if df.empty:
        raise ModelError("no training snapshots; run `churn synth` first")
    model, metrics = fit(df, settings)

    fingerprint = hashlib.sha256(
        pd.util.hash_pandas_object(df[["snapshot_date", "label", *FEATURE_COLUMNS]], index=False).values.tobytes()
    ).hexdigest()[:8]
    version = f"lgbm-{train_end:%Y%m%d}-{fingerprint}"
    bundle = {"model": model, "features": list(FEATURE_COLUMNS), "version": version}
    metrics["positive_rate"] = round(float(df["label"].mean()), 4)
    return register(
        wh, settings, bundle, version,
        train_start=df["snapshot_date"].min().to_pydatetime(),
        train_end=df["snapshot_date"].max().to_pydatetime(),
        metrics=metrics,
    )
