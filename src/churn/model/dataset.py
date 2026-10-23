"""Point-in-time training snapshots: the same feature SQL at monthly as_of dates plus the A1 label."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from churn.contract.loader import Contract
from churn.contract.views import contract_ctes
from churn.features.builder import as_of_ts, build_features, intervention_log_ready
from churn.features.registry import FEATURE_COLUMNS
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def snapshot_dates(wh: Warehouse, settings: Settings, contract: Contract, train_end: date) -> list[date]:
    """Month-ends with enough history before and a complete label horizon after."""
    first = wh.query(
        f"WITH {contract_ctes(contract, settings.key_pepper, ['subscriptions'])} "
        "SELECT MIN(started_at) AS first_start FROM subscriptions"
    ).iloc[0]["first_start"]
    if pd.isna(first):
        return []
    lo = pd.Timestamp(first) + pd.Timedelta(days=settings.model.min_history_days)
    hi = pd.Timestamp(train_end) - pd.Timedelta(days=settings.label.horizon_days)
    return [d.date() for d in pd.date_range(lo, hi, freq="ME")]


def churners(wh: Warehouse, settings: Settings, contract: Contract, as_of: date | datetime) -> set[str]:
    """Subscribers with a voluntary cancellation in [as_of, as_of + horizon)."""
    sql = (
        f"WITH {contract_ctes(contract, settings.key_pepper, ['subscriptions'])} "
        "SELECT subscriber_key FROM subscriptions "
        "WHERE cancel_type = 'voluntary' AND cancelled_at >= CAST(:as_of AS TIMESTAMP) "
        "AND cancelled_at < DATEADD(day, :horizon, CAST(:as_of AS TIMESTAMP))"
    )
    df = wh.query(sql, {"as_of": as_of_ts(as_of), "horizon": settings.label.horizon_days})
    return set(df["subscriber_key"])


def observed_labels(wh: Warehouse, settings: Settings, as_of: date | datetime) -> dict[str, int]:
    """subscriber_key -> 1 if the latest closed treated outcome is churned, else 0.

    Only outcomes measured at or before ``as_of`` count. Holdout is not a treatment.
    """
    if not intervention_log_ready(wh, settings):
        return {}
    ops = settings.schemas.ops
    frame = wh.query(
        f"""
        SELECT subscriber_key, outcome FROM (
            SELECT subscriber_key, outcome,
                   ROW_NUMBER() OVER (
                       PARTITION BY subscriber_key ORDER BY outcome_at DESC, decision_id
                   ) AS rn
            FROM {ops}.INTERVENTION_LOG
            WHERE execution_status = 'DISPATCHED'
              AND outcome IN ('retained', 'churned')
              AND outcome_at IS NOT NULL
              AND outcome_at <= CAST(:as_of AS TIMESTAMP)
              AND dispatched_at < CAST(:as_of AS TIMESTAMP)
        ) ranked
        WHERE rn = 1
        """,
        {"as_of": as_of_ts(as_of)},
    )
    return {row.subscriber_key: int(row.outcome == "churned") for row in frame.itertuples(index=False)}


def build_dataset(wh: Warehouse, settings: Settings, contract: Contract, train_end: date) -> pd.DataFrame:
    """Rows = (snapshot, active subscriber). Columns: snapshot_date, label, subscriber_key, features.

    The label is the voluntary cancellation inside the horizon. A closed treated outcome replaces it:
    churned is 1 and retained is 0.
    """
    frames = []
    for d in snapshot_dates(wh, settings, contract, train_end):
        feats = build_features(wh, settings, contract, d)
        if feats.empty:
            continue
        positives = churners(wh, settings, contract, d)
        label = feats["subscriber_key"].isin(positives).astype(int)
        observed = observed_labels(wh, settings, d)
        if observed:
            replaced = feats["subscriber_key"].map(observed)
            label = replaced.where(replaced.notna(), label).astype(int)
        feats = feats.assign(snapshot_date=pd.Timestamp(d), label=label)
        frames.append(feats)
    if not frames:
        return pd.DataFrame(columns=["snapshot_date", "label", "subscriber_key", *FEATURE_COLUMNS])
    return pd.concat(frames, ignore_index=True)
