"""Run the feature SQL for one as_of date inside the warehouse and return a frame."""

from __future__ import annotations

import re
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


# Per-domain feature SQL: file name and the CTE it defines. The master query (subscriptions.sql) joins all five.
DOMAIN_FILES: dict[str, str] = {
    "orders": "orders_f",
    "subscription_events": "subscription_events_f",
    "email": "email_f",
    "support": "support_f",
    "quiz": "quiz_f",
}
_HEADER = re.compile(r"^--\s*(requires|columns):\s*(.+)$", re.MULTILINE)


def _header(text: str, key: str) -> list[str]:
    for k, v in _HEADER.findall(text):
        if k == key:
            return [x.strip() for x in v.split(",") if x.strip()]
    raise ValueError(f"feature SQL is missing its '-- {key}:' header")


def _stub_type(column: str) -> str:
    return "VARCHAR" if column == "subscriber_key" else "TIMESTAMP" if column.endswith("_at") else "DOUBLE"


def _domain_sql(settings: Settings, contract: Contract, domain: str) -> tuple[str, list[str]]:
    """CTE text for one domain and the contract views it reads. A domain whose views are not in the
    contract becomes an empty CTE with the same columns, so its features come out as NULL/0 and its
    has_<domain> indicator as 0 instead of the query failing (R2.4)."""
    text = (settings.sql_dir / "features" / f"{domain}.sql").read_text()
    cte = DOMAIN_FILES[domain]
    required = _header(text, "requires")
    if all(v in contract.views for v in required):
        return text, required
    columns = _header(text, "columns")
    typed = ", ".join(f"CAST(NULL AS {_stub_type(c)}) AS {c}" for c in columns)
    return f"{cte} AS (SELECT {typed} WHERE 1 = 0)", []


def render_feature_sql(settings: Settings, contract: Contract, name: str = "subscriptions") -> str:
    text = (settings.sql_dir / "features" / f"{name}.sql").read_text()
    if "{{domain_ctes}}" in text:
        parts, views = [], ["subscriptions"]
        for domain in DOMAIN_FILES:
            sql, used = _domain_sql(settings, contract, domain)
            parts.append(sql.strip().rstrip(";"))
            views += [v for v in used if v not in views]
        text = text.replace("{{domain_ctes}}", ",\n".join(parts))
    else:
        views = ["subscriptions"]
    ctes = contract_ctes(contract, settings.key_pepper, views)
    return text.replace("{{contract_ctes}}", ctes).replace("{{active_statuses}}", _status_list(settings))


def build_features(wh: Warehouse, settings: Settings, contract: Contract, as_of: date | datetime) -> pd.DataFrame:
    sql = render_feature_sql(settings, contract)
    df = wh.query(sql, {"as_of": as_of_ts(as_of)})
    out = df[KEY_COLUMNS + FEATURE_COLUMNS].copy()
    out[FEATURE_COLUMNS] = out[FEATURE_COLUMNS].astype("float64")
    return out


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
