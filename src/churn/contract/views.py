"""Render the contract as one CTE per logical view; feature SQL selects from these, never physical names."""

from __future__ import annotations

from collections.abc import Iterable

from churn.contract.loader import Contract


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def view_cte(contract: Contract, name: str, pepper: str) -> str:
    view = contract.views[name]
    select = []
    if "customer_id" in view.columns:
        # Pseudonymous key (R7.1): downstream tables carry subscriber_key only.
        select.append(f"SHA2({_lit(pepper)} || {view.columns['customer_id'].source}, 256) AS subscriber_key")
    for logical, col in view.columns.items():
        if col.type == "timestamp":
            select.append(f"CAST({col.source} AS TIMESTAMP_NTZ) AS {logical}")
        else:
            select.append(f"{col.source} AS {logical}")
    select.append(f"CAST({view.loaded_at} AS TIMESTAMP_NTZ) AS loaded_at")
    dedup = f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {view.dedup_key} ORDER BY {view.loaded_at} DESC) = 1"
    return f"{name} AS (SELECT {', '.join(select)} FROM {contract.physical(name)} {dedup})"


def contract_ctes(contract: Contract, pepper: str, names: Iterable[str] | None = None) -> str:
    """Comma-joined CTE definitions, to be placed after ``WITH``."""
    names = list(names) if names is not None else list(contract.views)
    return ",\n".join(view_cte(contract, n, pepper) for n in names)
