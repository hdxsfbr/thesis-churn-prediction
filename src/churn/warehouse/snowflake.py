"""Snowflake backend. Credentials come from the environment and nowhere else."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

from churn.warehouse.bulk import build_merge_sql, check_frame
from churn.warehouse.dialect import render, render_many

_PARAM = re.compile(r":([A-Za-z_]\w*)")
_REQUIRED = {
    "account": "SNOWFLAKE_ACCOUNT",
    "user": "SNOWFLAKE_USER",
    "role": "SNOWFLAKE_ROLE",
    "warehouse": "SNOWFLAKE_WAREHOUSE",
    "database": "SNOWFLAKE_DATABASE",
}


def credentials_from_env() -> dict[str, str]:
    """Read Snowflake credentials from the process environment. No file, flag, or default is consulted."""
    missing = [key for key, name in _REQUIRED.items() if not os.environ.get(name)]
    password = os.environ.get("SNOWFLAKE_PASSWORD") or ""
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH") or ""
    if not password and not key_path:
        missing.append("password or private_key_path")
    if missing:
        raise RuntimeError(
            "Snowflake credentials are missing from the environment: " + ", ".join(missing)
        )
    creds = {key: os.environ[name] for key, name in _REQUIRED.items()}
    if password:
        creds["password"] = password
    if key_path:
        creds["private_key_path"] = key_path
    return creds


def _load_private_key(path: str) -> bytes:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _connector():
    try:
        import snowflake.connector
    except ImportError as exc:
        raise RuntimeError("install the snowflake extra: pip install 'churn[snowflake]'") from exc
    return snowflake.connector


class SnowflakeWarehouse:
    target = "snowflake"

    def __init__(self, connection) -> None:
        self.con = connection
        self._stage_n = 0

    @classmethod
    def from_env(cls) -> SnowflakeWarehouse:
        creds = credentials_from_env()
        kwargs = {key: creds[key] for key in ("account", "user", "role", "warehouse", "database")}
        if "password" in creds:
            kwargs["password"] = creds["password"]
        else:
            kwargs["private_key"] = _load_private_key(creds["private_key_path"])
        return cls(_connector().connect(**kwargs))

    @staticmethod
    def _pyformat(sql: str) -> str:
        return _PARAM.sub(r"%(\1)s", sql)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        cursor = self.con.cursor()
        try:
            cursor.execute(self._pyformat(render(sql, self.target)), params or {})
            rows = cursor.fetchall()
            columns = [col[0].lower() for col in cursor.description] if cursor.description else []
        finally:
            cursor.close()
        return pd.DataFrame(rows, columns=columns)

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        cursor = self.con.cursor()
        try:
            for statement in render_many(sql, self.target):
                cursor.execute(self._pyformat(statement), params or {})
        finally:
            cursor.close()

    def merge_frame(
        self, table: str, df: pd.DataFrame, keys: Sequence[str], insert_only: bool = False
    ) -> int:
        """Write the frame into a temp table, then MERGE. A second call with the same keys updates in place."""
        check_frame(df, keys)
        if df.empty:
            return 0
        from snowflake.connector.pandas_tools import write_pandas

        self._stage_n += 1
        stage = f"MERGE_STAGE_{self._stage_n}"
        write_pandas(
            self.con, df, stage, auto_create_table=True, table_type="temporary", quote_identifiers=False
        )
        try:
            self.execute(build_merge_sql(table, stage, list(df.columns), keys, insert_only))
        finally:
            self.execute(f"DROP TABLE IF EXISTS {stage}")
        return len(df)

    def insert_frame(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        from snowflake.connector.pandas_tools import write_pandas

        schema, name = table.split(".", 1) if "." in table else (None, table)
        write_pandas(
            self.con, df, name, schema=schema, quote_identifiers=False, use_logical_type=True
        )
        return len(df)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.con.autocommit(False)
        try:
            yield
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise
        finally:
            self.con.autocommit(True)

    def close(self) -> None:
        self.con.close()
