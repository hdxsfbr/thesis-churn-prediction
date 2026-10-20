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


DOMAIN_FILES = ("orders", "subscription_events", "email", "support", "quiz")
_HEADER = re.compile(r"^--\s*(requires|columns):\s*(.*)$")


def _domain_cte(settings: Settings, contract: Contract, domain: str) -> tuple[str, str | None]:
    """The CTE text of one domain file, and the contract view it needs (None if that view is not mapped).

    When the view is missing the domain becomes an empty CTE with the same typed columns, so the master
    query runs unchanged: counts default to 0, rates stay NULL and has_<domain> is 0 (R2.4).
    """
    text = (settings.sql_dir / "features" / f"{domain}.sql").read_text()
    meta: dict[str, str] = {}
    body = []
    for line in text.splitlines():
        m = _HEADER.match(line.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip()
        else:
            body.append(line)
    view = meta.get("requires")
    if view and view not in contract.views:
        cols = []
        for spec in meta["columns"].split(","):
            name, _, typ = spec.strip().partition(":")
            cols.append(f"CAST(NULL AS {typ or 'DOUBLE'}) AS {name}")
        return f"{domain}_agg AS (SELECT {', '.join(cols)} WHERE 1 = 0)", view
    return "\n".join(body).strip(), view


def render_feature_sql(settings: Settings, contract: Contract, name: str = "subscriptions") -> str:
    text = (settings.sql_dir / "features" / f"{name}.sql").read_text()
    domain_ctes = ",\n".join(_domain_cte(settings, contract, d)[0] for d in DOMAIN_FILES)
    return (
        text.replace("{{contract_ctes}}", contract_ctes(contract, settings.key_pepper))
        .replace("{{domain_ctes}}", domain_ctes)
        .replace("{{active_statuses}}", _status_list(settings))
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
