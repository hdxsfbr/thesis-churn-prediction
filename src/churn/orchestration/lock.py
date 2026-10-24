"""One cycle lock. A live holder makes the next cycle skip; a stale heartbeat is taken over."""

from __future__ import annotations

from datetime import datetime

from churn.settings import Settings
from churn.warehouse.base import Warehouse

LOCK_NAME = "cycle"
DEFAULT_TTL_S = 300


def acquire(wh: Warehouse, settings: Settings, holder: str, ttl_s: int = DEFAULT_TTL_S) -> bool:
    """Take `cycle` for `holder`. False when another holder still has a fresh heartbeat."""
    ops = settings.schemas.ops
    now = datetime.now()
    wh.execute(
        f"""
        MERGE INTO {ops}.RUN_LOCKS t
        USING (
            SELECT '{LOCK_NAME}' AS lock_name, :holder AS holder,
                   :now AS heartbeat_at, :ttl AS ttl_s
        ) s
        ON t.lock_name = s.lock_name
        WHEN MATCHED AND (
            t.holder = s.holder
            OR DATEDIFF('second', t.heartbeat_at, s.heartbeat_at) >= t.ttl_s
        ) THEN UPDATE SET
            holder = s.holder, heartbeat_at = s.heartbeat_at, ttl_s = s.ttl_s
        WHEN NOT MATCHED THEN INSERT (lock_name, holder, heartbeat_at, ttl_s)
        VALUES (s.lock_name, s.holder, s.heartbeat_at, s.ttl_s)
        """,
        {"holder": holder, "now": now, "ttl": ttl_s},
    )
    row = wh.query(
        f"SELECT holder FROM {ops}.RUN_LOCKS WHERE lock_name = :name",
        {"name": LOCK_NAME},
    )
    return not row.empty and row.iloc[0]["holder"] == holder


def heartbeat(wh: Warehouse, settings: Settings, holder: str) -> None:
    """Refresh the lock so a long step does not look dead to the next cycle."""
    ops = settings.schemas.ops
    wh.execute(
        f"""
        UPDATE {ops}.RUN_LOCKS
        SET heartbeat_at = :now
        WHERE lock_name = :name AND holder = :holder
        """,
        {"now": datetime.now(), "name": LOCK_NAME, "holder": holder},
    )


def release(wh: Warehouse, settings: Settings, holder: str) -> None:
    """Drop the lock only when this holder still owns it."""
    ops = settings.schemas.ops
    wh.execute(
        f"DELETE FROM {ops}.RUN_LOCKS WHERE lock_name = :name AND holder = :holder",
        {"name": LOCK_NAME, "holder": holder},
    )
