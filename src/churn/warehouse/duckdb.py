"""DuckDB backend (tests, local runs, full-scale timing)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from churn.settings import Settings
from churn.warehouse.bulk import build_merge_sql, check_frame
from churn.warehouse.dialect import render, render_many

_PARAM = re.compile(r"\$([A-Za-z_]\w*)")


class DuckDBWarehouse:
    target = "duckdb"

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(path)
        self._depth = 0
        self._stage_n = 0

    @staticmethod
    def _bind(sql: str, params: dict[str, Any] | None) -> dict[str, Any]:
        used = set(_PARAM.findall(sql))
        params = params or {}
        missing = used - set(params)
        if missing:
            raise KeyError(f"unbound SQL parameters: {sorted(missing)}")
        return {k: params[k] for k in used}

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        rendered = render(sql, self.target)
        return self.con.execute(rendered, self._bind(rendered, params)).df()

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        for stmt in render_many(sql, self.target):
            self.con.execute(stmt, self._bind(stmt, params))

    def merge_frame(
        self, table: str, df: pd.DataFrame, keys: Sequence[str], insert_only: bool = False
    ) -> int:
        check_frame(df, keys)
        if df.empty:
            return 0
        self._stage_n += 1
        stage = f"_stage_{self._stage_n}"
        self.con.register(stage, df)
        try:
            sql = build_merge_sql(table, stage, list(df.columns), keys, insert_only)
            self.con.execute(render(sql, self.target))
        finally:
            self.con.unregister(stage)
        return len(df)

    def insert_frame(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        self.con.register("_insert_frame", df)
        try:
            cols = ", ".join(df.columns)
            self.con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _insert_frame")
        finally:
            self.con.unregister("_insert_frame")
        return len(df)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth:  # nested: join the outer transaction
            yield
            return
        self.con.execute("BEGIN")
        self._depth = 1
        try:
            yield
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        else:
            self.con.execute("COMMIT")
        finally:
            self._depth = 0

    def close(self) -> None:
        self.con.close()


def open_warehouse(settings: Settings) -> DuckDBWarehouse:
    if settings.backend != "duckdb":
        raise NotImplementedError(f"backend {settings.backend!r} is not available yet")
    return DuckDBWarehouse(settings.duckdb_path)
