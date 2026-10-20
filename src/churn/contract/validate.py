"""Cycle-start contract check: every mapped object and column exists with a compatible type.

Runs before anything is written; any mismatch raises ``ContractError`` listing every problem.
"""

from __future__ import annotations

from churn.contract.loader import Contract, ContractError
from churn.warehouse.base import Warehouse

# Declared contract type -> substrings of the warehouse data_type that are compatible.
_FAMILIES: dict[str, tuple[str, ...]] = {
    "varchar": ("CHAR", "TEXT", "STRING"),
    "number": ("INT", "NUMBER", "NUMERIC", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "FIXED"),
    "timestamp": ("TIMESTAMP", "DATE"),
    "timestamp_tz": ("TIMESTAMP",),
    "boolean": ("BOOL",),
}


def compatible(declared: str, actual: str) -> bool:
    families = _FAMILIES.get(declared.lower())
    if families is None:
        return False
    return any(f in actual.upper() for f in families)


def validate(wh: Warehouse, contract: Contract) -> None:
    """Raise ContractError unless every mapped object and column exists with a compatible type."""
    problems: list[str] = []
    for name, view in contract.views.items():
        *_, schema, table = contract.physical(name).split(".")
        found = wh.query(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE UPPER(table_schema) = UPPER(:s) AND UPPER(table_name) = UPPER(:t)",
            {"s": schema, "t": table},
        )
        have = {c.upper(): t for c, t in zip(found["column_name"], found["data_type"], strict=True)}
        if not have:
            problems.append(f"{name}: object {contract.physical(name)} not found")
            continue
        wanted = {c.source for c in view.columns.values()} | {view.loaded_at, view.dedup_key}
        missing = sorted(c for c in wanted if c.upper() not in have)
        if missing:
            problems.append(f"{name}: missing columns {missing}")
        for logical, col in view.columns.items():
            actual = have.get(col.source.upper())
            if actual is not None and not compatible(col.type, actual):
                problems.append(
                    f"{name}.{logical}: source column {col.source} is {actual}, incompatible with declared {col.type}"
                )
    if problems:
        raise ContractError("; ".join(problems))
