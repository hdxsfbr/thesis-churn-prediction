"""Render the contract as one CTE per logical view; feature SQL selects from these, never physical names.

Each CTE applies, in order: column renames (source -> logical), UTC normalisation of timestamps,
event dedup (latest ``loaded_at`` per ``dedup_key`` wins) and the pseudonymous ``subscriber_key``.
The raw ``customer_id`` is hidden unless explicitly requested (only the key map needs it).
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from churn.contract.loader import Column, Contract
from churn.warehouse.base import Warehouse


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _utc(col: Column) -> str:
    """Expression yielding a TIMESTAMP_NTZ holding the UTC wall-clock time."""
    if col.type == "timestamp_tz":
        return f"CAST(CONVERT_TIMEZONE('UTC', {col.source}) AS TIMESTAMP_NTZ)"
    if col.tz and col.tz.upper() != "UTC":
        return f"CAST(CONVERT_TIMEZONE({_lit(col.tz)}, 'UTC', CAST({col.source} AS TIMESTAMP_NTZ)) AS TIMESTAMP_NTZ)"
    return f"CAST({col.source} AS TIMESTAMP_NTZ)"


def view_cte(contract: Contract, name: str, pepper: str, include_raw_id: bool = False) -> str:
    view = contract.views[name]
    select = []
    if "customer_id" in view.columns:
        # Pseudonymous key (R7.1): downstream tables carry subscriber_key only.
        select.append(f"SHA2({_lit(pepper)} || {view.columns['customer_id'].source}, 256) AS subscriber_key")
    for logical, col in view.columns.items():
        if logical == "customer_id" and not include_raw_id:
            continue
        if col.type in ("timestamp", "timestamp_tz"):
            select.append(f"{_utc(col)} AS {logical}")
        else:
            select.append(f"{col.source} AS {logical}")
    select.append(f"CAST({view.loaded_at} AS TIMESTAMP_NTZ) AS loaded_at")
    dedup = f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {view.dedup_key} ORDER BY {view.loaded_at} DESC) = 1"
    return f"{name} AS (SELECT {', '.join(select)} FROM {contract.physical(name)} {dedup})"


def contract_ctes(
    contract: Contract, pepper: str, names: Iterable[str] | None = None, include_raw_id: bool = False
) -> str:
    """Comma-joined CTE definitions, to be placed after ``WITH``."""
    names = list(names) if names is not None else list(contract.views)
    return ",\n".join(view_cte(contract, n, pepper, include_raw_id) for n in names)


def key_map_frame(wh: Warehouse, contract: Contract, pepper: str) -> pd.DataFrame:
    """Distinct (subscriber_key, customer_id) pairs across every contract view that carries customer_id."""
    names = [n for n, v in contract.views.items() if "customer_id" in v.columns]
    if not names:
        return pd.DataFrame({"subscriber_key": [], "customer_id": []})
    ctes = contract_ctes(contract, pepper, names, include_raw_id=True)
    union = " UNION ".join(f"SELECT subscriber_key, customer_id FROM {n}" for n in names)
    return wh.query(f"WITH {ctes} SELECT DISTINCT subscriber_key, customer_id FROM ({union}) WHERE customer_id IS NOT NULL")


def populate_key_map(wh: Warehouse, contract: Contract, pepper: str, private_schema: str) -> int:
    """Insert missing keys into KEY_MAP (idempotent MERGE). Returns the number of pairs seen."""
    frame = key_map_frame(wh, contract, pepper)
    return wh.merge_frame(f"{private_schema}.KEY_MAP", frame, keys=["subscriber_key"], insert_only=True)
