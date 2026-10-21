"""Outbox: one insert-only row per decision, written with its INTERVENTION_LOG row in one transaction.

The build never updates or deletes outbox rows; a replay of the same run id changes nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from churn.settings import Settings
from churn.warehouse.base import Warehouse

if TYPE_CHECKING:
    from churn.agent.cycle import Decision


def dispatch(wh: Warehouse, settings: Settings, decisions: list[Decision]) -> int:
    """Log and enqueue every decision (MERGE, insert-only, keyed on decision_id). Returns decisions handled."""
    if not decisions:
        return 0
    ops = settings.schemas.ops
    now = datetime.now()
    log = pd.DataFrame([d.log_row(now) for d in decisions])
    outbox = pd.DataFrame([d.outbox_row(now) for d in decisions])
    with wh.transaction():
        wh.merge_frame(f"{ops}.INTERVENTION_LOG", log, keys=["decision_id"], insert_only=True)
        wh.merge_frame(f"{ops}.OUTBOX", outbox, keys=["outbox_id"], insert_only=True)
    return len(decisions)
