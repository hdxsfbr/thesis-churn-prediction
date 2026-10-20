"""Migration runner: V###__name.sql files applied once, checksums recorded in SCHEMA_MIGRATIONS."""

from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from churn.settings import Settings
from churn.warehouse.base import Warehouse

# (directory under sql/, version prefix recorded in SCHEMA_MIGRATIONS)
SOURCES = (("synth", "synth/"), ("migrations", ""))


class MigrationError(RuntimeError):
    pass


def _ensure_tracking(wh: Warehouse, ops: str) -> None:
    wh.execute(
        f"CREATE SCHEMA IF NOT EXISTS {ops};"
        f"CREATE TABLE IF NOT EXISTS {ops}.SCHEMA_MIGRATIONS ("
        "version VARCHAR, checksum VARCHAR, applied_at TIMESTAMP_NTZ)"
    )


def apply_migrations(wh: Warehouse, settings: Settings) -> list[str]:
    """Apply every pending migration; return the versions applied (empty when up to date)."""
    ops = settings.schemas.ops
    _ensure_tracking(wh, ops)
    recorded = wh.query(f"SELECT version, checksum FROM {ops}.SCHEMA_MIGRATIONS")
    known = dict(zip(recorded["version"], recorded["checksum"], strict=True))

    applied: list[str] = []
    for directory, prefix in SOURCES:
        for path in sorted((settings.sql_dir / directory).glob("V*__*.sql")):
            version = prefix + path.stem.split("__")[0]
            text = path.read_text()
            checksum = hashlib.sha256(text.encode()).hexdigest()
            if version in known:
                if known[version] != checksum:
                    raise MigrationError(f"migration {version} was edited after it shipped")
                continue
            with wh.transaction():
                wh.execute(text)
                wh.merge_frame(
                    f"{ops}.SCHEMA_MIGRATIONS",
                    pd.DataFrame(
                        [{"version": version, "checksum": checksum, "applied_at": datetime.now()}]
                    ),
                    keys=["version"],
                )
            applied.append(version)
    return applied
