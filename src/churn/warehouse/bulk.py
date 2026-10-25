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
    table: str,
    stage: str,
    columns: Sequence[str],
    keys: Sequence[str],
    insert_only: bool = False,
    casts: dict[str, str] | None = None,
) -> str:
    def src(column: str) -> str:
        typ = casts.get(column) if casts else None
        if typ:
            return f"CAST(s.{column} AS {typ})"
        return f"s.{column}"

    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    updatable = [c for c in columns if c not in keys]
    parts = [f"MERGE INTO {table} t USING {stage} s ON {on}"]
    if updatable and not insert_only:
        parts.append("WHEN MATCHED THEN UPDATE SET " + ", ".join(f"{c} = {src(c)}" for c in updatable))
    parts.append(
        "WHEN NOT MATCHED THEN INSERT (" + ", ".join(columns) + ") VALUES ("
        + ", ".join(src(c) for c in columns) + ")"
    )
    return " ".join(parts)
