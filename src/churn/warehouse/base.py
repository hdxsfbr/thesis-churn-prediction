"""Warehouse protocol: the only surface the pipeline uses to touch data."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

import pandas as pd


class Warehouse(Protocol):
    target: str  # sqlglot dialect name of the engine

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        """Run one Snowflake-dialect statement with ``:name`` params and return a frame."""

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        """Run a script of Snowflake-dialect statements."""

    def merge_frame(
        self, table: str, df: pd.DataFrame, keys: Sequence[str], insert_only: bool = False
    ) -> int:
        """Idempotent write: MERGE ``df`` into ``table`` on ``keys``. Returns rows in ``df``."""

    def insert_frame(self, table: str, df: pd.DataFrame) -> int:
        """Plain bulk append (used for generated source data)."""

    def transaction(self) -> AbstractContextManager[None]:
        """Commit on clean exit, roll back on exception."""

    def close(self) -> None: ...
