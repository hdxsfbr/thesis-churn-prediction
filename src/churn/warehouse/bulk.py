"""Stage-then-MERGE writer SQL, shared by every backend (Snowflake dialect)."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def check_frame(df: pd.DataFrame, keys: Sequence[str]) -> None:
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise ValueError(f"merge keys missing from frame: {missing}")
    if df.duplicated(list(keys)).any():
        raise ValueError(f"duplicate merge keys in frame: {list(keys)}")


def build_merge_sql(
    table: str, stage: str, columns: Sequence[str], keys: Sequence[str], insert_only: bool = False
) -> str:
    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    updatable = [c for c in columns if c not in keys]
    parts = [f"MERGE INTO {table} t USING {stage} s ON {on}"]
    if updatable and not insert_only:
        parts.append("WHEN MATCHED THEN UPDATE SET " + ", ".join(f"{c} = s.{c}" for c in updatable))
    parts.append(
        "WHEN NOT MATCHED THEN INSERT (" + ", ".join(columns) + ") VALUES ("
        + ", ".join(f"s.{c}" for c in columns) + ")"
    )
    return " ".join(parts)
