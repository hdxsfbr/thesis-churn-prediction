"""Run the feature SQL for one as_of date inside the warehouse and return a frame."""

from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd

from churn.contract.loader import Contract
from churn.contract.views import contract_ctes
from churn.features.registry import FEATURE_COLUMNS, KEY_COLUMNS
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def as_of_ts(as_of: date | datetime) -> datetime:
    return as_of if isinstance(as_of, datetime) else datetime.combine(as_of, time(0))


def _status_list(settings: Settings) -> str:
    return ", ".join("'" + s.replace("'", "''") + "'" for s in settings.scoring.active_statuses)


def render_feature_sql(settings: Settings, contract: Contract, name: str = "subscriptions") -> str:
    text = (settings.sql_dir / "features" / f"{name}.sql").read_text()
    return text.replace("{{contract_ctes}}", contract_ctes(contract, settings.key_pepper)).replace(
        "{{active_statuses}}", _status_list(settings)
    )


def build_features(wh: Warehouse, settings: Settings, contract: Contract, as_of: date | datetime) -> pd.DataFrame:
    sql = render_feature_sql(settings, contract)
    df = wh.query(sql, {"as_of": as_of_ts(as_of)})
    return df[KEY_COLUMNS + FEATURE_COLUMNS]


def count_active(wh: Warehouse, settings: Settings, contract: Contract, as_of: date | datetime) -> int:
    """Active subscribers (distinct subscriber_key) in the source at as_of, counted independently of the feature SQL."""
    sql = (
        f"WITH {contract_ctes(contract, settings.key_pepper, ['subscriptions'])} "
        "SELECT COUNT(DISTINCT subscriber_key) AS n FROM subscriptions "
        "WHERE started_at < CAST(:as_of AS TIMESTAMP) "
        "AND loaded_at <= CAST(:as_of AS TIMESTAMP) "
        "AND (cancelled_at IS NULL OR cancelled_at >= CAST(:as_of AS TIMESTAMP)) "
        f"AND (cancelled_at IS NOT NULL OR status IN ({_status_list(settings)}))"
    )
    return int(wh.query(sql, {"as_of": as_of_ts(as_of)}).iloc[0]["n"])
