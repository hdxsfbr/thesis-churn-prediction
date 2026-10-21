"""Outbox: one insert-only row per decision, written with its INTERVENTION_LOG row in one transaction.

The build never updates or deletes outbox rows; a replay of the same run id changes nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from churn.execution.approvals import STALE_REASON, approval_row, inactive_subscribers
from churn.settings import Settings
from churn.warehouse.base import Warehouse

if TYPE_CHECKING:
    from churn.agent.cycle import Decision


def dispatch(wh: Warehouse, settings: Settings, decisions: list[Decision]) -> int:
    """Log and route every decision (MERGE, insert-only, keyed on decision_id). Returns the number of outbox rows written.

    Gated decisions go to APPROVALS with no outbox row. Autonomous ones go to the outbox unless the subscriber
    has cancelled since scoring: those are logged CANCELLED_STALE and nothing is sent.
    """
    if not decisions:
        return 0
    ops = settings.schemas.ops
    now = datetime.now()
    autonomous = [d for d in decisions if not d.needs_approval]
    gone = inactive_subscribers(wh, settings, [d.context.subscriber_key for d in autonomous], now)
    send = [d for d in autonomous if d.context.subscriber_key not in gone]
    log = pd.DataFrame([
        d.log_row(now, STALE_REASON if d.context.subscriber_key in gone and not d.needs_approval else None)
        for d in decisions
    ])
    # The approval deadline is kept on the audit log, which the approver cannot edit (the APPROVALS copy is not trusted).
    log["approval_expires_at"] = [approval_row(d, now)["expires_at"] if d.needs_approval else None for d in decisions]
    with wh.transaction():
        wh.merge_frame(f"{ops}.INTERVENTION_LOG", log, keys=["decision_id"], insert_only=True)
        queued = [approval_row(d, now) for d in decisions if d.needs_approval]
        if queued:
            wh.merge_frame(f"{ops}.APPROVALS", pd.DataFrame(queued), keys=["decision_id"], insert_only=True)
        if send:
            outbox = pd.DataFrame([d.outbox_row(now) for d in send])
            wh.merge_frame(f"{ops}.OUTBOX", outbox, keys=["outbox_id"], insert_only=True)
    return len(send)
