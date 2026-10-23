"""Close intervention and holdout decisions after the outcome window (R6.2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.runtime_config import ConfigError, RuntimeConfig, load
from churn.settings import Settings
from churn.warehouse.base import Warehouse

CLOSED = ("retained", "churned", "unknown")


def _moment(as_of: date | datetime) -> datetime:
    return as_of if isinstance(as_of, datetime) else datetime(as_of.year, as_of.month, as_of.day)


def _response(opened: int, clicked: int, redeemed: int) -> str:
    if redeemed:
        return "redeemed"
    if clicked:
        return "clicked"
    if opened:
        return "opened"
    return "none"


def _outcome(churned: int, active_at_end: int) -> str:
    if churned:
        return "churned"
    if active_at_end:
        return "retained"
    return "unknown"


def close_outcomes(
    wh: Warehouse, settings: Settings, as_of: date | datetime, cfg: RuntimeConfig | None = None
) -> int:
    """Merge OUTCOMES and copy response/outcome onto the log. Rows still inside the window stay PENDING.

    A row is due when ``dispatched_at + window_days <= as_of``. Holdout rows use the decision time as
    ``dispatched_at`` so the same window applies. Response is an open or click inside the response
    window, or a redemption (a discounted order) for a retention offer. Outcome is ``churned`` for a
    voluntary cancellation inside the window, ``retained`` when a subscription is still active at the
    window end, and ``unknown`` otherwise.
    """
    cfg = cfg or load(wh, settings)
    if cfg.outcomes is None:
        raise ConfigError("outcomes config is missing or invalid")
    moment = _moment(as_of)
    window = cfg.outcomes.window_days
    response_days = cfg.outcomes.response_window_days
    ops = settings.schemas.ops
    statuses = ", ".join("'" + s.replace("'", "''") + "'" for s in settings.scoring.active_statuses)
    ctes = contract_ctes(
        contract_for(settings), settings.key_pepper, ["email_events", "orders", "subscriptions"]
    )
    sql = f"""
        WITH {ctes},
        due AS (
            SELECT decision_id, subscriber_key, dispatched_at, intervention_type
            FROM {ops}.INTERVENTION_LOG
            WHERE outcome = 'PENDING'
              AND dispatched_at IS NOT NULL
              AND DATEADD(day, :window, dispatched_at) <= CAST(:as_of AS TIMESTAMP)
              AND execution_status IN ('DISPATCHED', 'HOLDOUT')
        )
        SELECT d.decision_id,
               MAX(CASE WHEN e.event_type = 'open' AND e.loaded_at <= CAST(:as_of AS TIMESTAMP)
                         AND e.event_ts > d.dispatched_at
                         AND e.event_ts <= DATEADD(day, :response, d.dispatched_at) THEN 1 ELSE 0 END) AS opened,
               MAX(CASE WHEN e.event_type = 'click' AND e.loaded_at <= CAST(:as_of AS TIMESTAMP)
                         AND e.event_ts > d.dispatched_at
                         AND e.event_ts <= DATEADD(day, :response, d.dispatched_at) THEN 1 ELSE 0 END) AS clicked,
               MAX(CASE WHEN d.intervention_type = 'retention_offer' AND o.discount_usd > 0
                         AND o.loaded_at <= CAST(:as_of AS TIMESTAMP)
                         AND o.ordered_at > d.dispatched_at
                         AND o.ordered_at <= DATEADD(day, :response, d.dispatched_at) THEN 1 ELSE 0 END) AS redeemed,
               MAX(CASE WHEN LOWER(s.cancel_type) = 'voluntary' AND s.loaded_at <= CAST(:as_of AS TIMESTAMP)
                         AND s.cancelled_at > d.dispatched_at
                         AND s.cancelled_at <= DATEADD(day, :window, d.dispatched_at) THEN 1 ELSE 0 END) AS churned,
               MAX(CASE WHEN s.loaded_at <= CAST(:as_of AS TIMESTAMP) AND s.status IN ({statuses})
                         AND (s.cancelled_at IS NULL OR s.cancelled_at > DATEADD(day, :window, d.dispatched_at))
                        THEN 1 ELSE 0 END) AS active_at_end
        FROM due d
        LEFT JOIN email_events e ON e.subscriber_key = d.subscriber_key
        LEFT JOIN orders o ON o.subscriber_key = d.subscriber_key
        LEFT JOIN subscriptions s ON s.subscriber_key = d.subscriber_key
        GROUP BY d.decision_id
    """
    due = wh.query(sql, {"window": window, "response": response_days, "as_of": moment})
    if due.empty:
        return 0
    responses = [
        _response(int(r.opened), int(r.clicked), int(r.redeemed)) for r in due.itertuples(index=False)
    ]
    outcomes = [_outcome(int(r.churned), int(r.active_at_end)) for r in due.itertuples(index=False)]
    if any(item not in CLOSED for item in outcomes):
        raise RuntimeError("outcome classifier returned a value outside retained/churned/unknown")
    closed = pd.DataFrame(
        {
            "decision_id": due["decision_id"].tolist(),
            "response": responses,
            "outcome": outcomes,
            "measured_at": moment,
            "window_days": window,
        }
    )
    wh.merge_frame(f"{ops}.OUTCOMES", closed, keys=["decision_id"])
    wh.merge_frame(
        f"{ops}.INTERVENTION_LOG",
        closed[["decision_id", "response", "outcome"]].assign(outcome_at=moment),
        keys=["decision_id"],
    )
    return len(closed)


def advance(wh: Warehouse, settings: Settings, start: date, days: int) -> int:
    """Move the clock forward one day at a time. Each day responds to the outbox, then closes elapsed windows."""
    from churn.synth.responder import respond

    if days < 1:
        raise ValueError("days must be at least 1")
    clock = start
    closed = 0
    for _ in range(days):
        clock = clock + timedelta(days=1)
        moment = datetime(clock.year, clock.month, clock.day)
        respond(wh, settings, moment)
        closed += close_outcomes(wh, settings, moment)
    return closed
