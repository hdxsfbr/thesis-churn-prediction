"""Run the feature SQL for one as_of date inside the warehouse and return a frame.

``sql/features/subscriptions.sql`` is the master query. Each domain file (orders, subscription_events, email,
support, quiz) holds the CTEs for one domain and is spliced into the master at ``{{domain_ctes}}``. A domain file
may declare ``-- stub <view>: <column> <TYPE>, ...`` lines: when the source contract does not map that logical
view the builder substitutes an empty typed CTE, so the domain's features are NULL/0 and ``has_<domain>`` is 0
instead of the query failing (R2.4).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time

import pandas as pd

from churn.contract.loader import Contract
from churn.contract.views import contract_ctes
from churn.features.registry import FEATURE_COLUMNS, KEY_COLUMNS
from churn.settings import Settings
from churn.warehouse.base import Warehouse

DOMAIN_FILES = ("orders", "subscription_events", "email", "support", "quiz")
AS_OF_SQL = "CAST(:as_of AS TIMESTAMP)"
_STUB = re.compile(r"^--\s*stub\s+(\w+):\s*(.+)$")


def as_of_ts(as_of: date | datetime) -> datetime:
    return as_of if isinstance(as_of, datetime) else datetime.combine(as_of, time(0))


def _status_list(settings: Settings) -> str:
    return ", ".join("'" + s.replace("'", "''") + "'" for s in settings.scoring.active_statuses)


def _read(settings: Settings, name: str) -> str:
    return (settings.sql_dir / "features" / f"{name}.sql").read_text()


def _strip_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


def _stubs(text: str) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for line in text.splitlines():
        m = _STUB.match(line.strip())
        if m:
            cols = [tuple(c.strip().split()) for c in m.group(2).split(",")]
            out[m.group(1)] = [(c[0], c[1]) for c in cols]
    return out


def _stub_cte(view: str, columns: list[tuple[str, str]]) -> str:
    select = ", ".join(f"CAST(NULL AS {typ}) AS {col}" for col, typ in columns)
    return f"{view} AS (SELECT {select} WHERE 1 = 0)"


def render_feature_sql(settings: Settings, contract: Contract, name: str = "subscriptions") -> str:
    texts = [_read(settings, d) for d in DOMAIN_FILES]
    ctes = [contract_ctes(contract, settings.key_pepper)] if contract.views else []
    for text in texts:
        for view, cols in _stubs(text).items():
            if view not in contract.views:
                ctes.append(_stub_cte(view, cols))
    domain = ",\n".join(_strip_comments(t).strip().rstrip(",") for t in texts)
    sql = _strip_comments(_read(settings, name))
    return (
        sql.replace("{{contract_ctes}}", ",\n".join(ctes))
        .replace("{{domain_ctes}}", domain)
        .replace("{{active_statuses}}", _status_list(settings))
        .replace("{{as_of}}", AS_OF_SQL)
    )


def build_features(
    wh: Warehouse, settings: Settings, contract: Contract, as_of: date | datetime, current_status: bool = False
) -> pd.DataFrame:
    """Features for every subscriber active at as_of.

    ``current_status`` (bound as ``:use_current_status``) enables ``status_past_due``, which reads today's
    ``subscriptions.status``. Pass True only for the live run date; the default NULLs it so history never leaks.
    """
    sql = render_feature_sql(settings, contract)
    df = wh.query(sql, {"as_of": as_of_ts(as_of), "use_current_status": bool(current_status)})
    out = df[KEY_COLUMNS + FEATURE_COLUMNS].copy()
    out[FEATURE_COLUMNS] = out[FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").astype(float)
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
