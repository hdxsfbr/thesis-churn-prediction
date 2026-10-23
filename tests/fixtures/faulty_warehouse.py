"""Warehouse wrapper that injects source, write, and step failures for recovery tests."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pandas as pd

from churn.obs.errors import SourceUnavailable, WarehouseWriteError
from churn.warehouse.base import Warehouse


class FaultyWarehouse:
    """Delegates to an inner warehouse. Faults are off until a test sets them."""

    def __init__(self, inner: Warehouse) -> None:
        self.inner = inner
        self.source_down = False
        self.source_attempts = 0
        self.fail_merge_table: str | None = None
        self.fail_merge_on: int | None = None
        self._merges = 0
        self.before_step = None

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        if self.source_down and "THESIS_SYNTH" in sql.upper():
            self.source_attempts += 1
            raise SourceUnavailable("source view unavailable")
        return self.inner.query(sql, params)

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        if self.source_down and "THESIS_SYNTH" in sql.upper():
            self.source_attempts += 1
            raise SourceUnavailable("source view unavailable")
        self.inner.execute(sql, params)

    def merge_frame(
        self, table: str, df: pd.DataFrame, keys: Sequence[str], insert_only: bool = False
    ) -> int:
        token = (self.fail_merge_table or "").upper()
        if token and token in table.upper():
            self._merges += 1
            if self.fail_merge_on is not None and self._merges == self.fail_merge_on:
                raise WarehouseWriteError(f"write failed mid-batch: {table}")
        return self.inner.merge_frame(table, df, keys, insert_only)

    def insert_frame(self, table: str, df: pd.DataFrame) -> int:
        return self.inner.insert_frame(table, df)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.inner.transaction():
            yield

    def close(self) -> None:
        self.inner.close()

    @property
    def target(self) -> str:
        return self.inner.target
