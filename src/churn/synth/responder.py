"""Append simulated opens, redemptions and cancellations for new outbox rows (R6.2).

Events are dated after the outbox row and tagged with ``sim-`` ids. A send event is the marker that
the row was already simulated, so a later day does not write them again. Nothing here is a live send.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def _digest(outbox_id: str) -> bytes:
    return hashlib.sha256(outbox_id.encode()).digest()


def _when(created_at: datetime, days: int) -> pd.Timestamp:
    base = pd.Timestamp(created_at)
    if base.tzinfo is not None:
        base = base.tz_convert("UTC").tz_localize(None)
    return base + pd.Timedelta(days=days)


def _email_ts(moment: pd.Timestamp) -> pd.Timestamp:
    return moment.tz_localize("UTC").tz_convert("America/New_York")


def respond(wh: Warehouse, settings: Settings, as_of: datetime) -> int:
    """Simulate each READY outbox row created at or before ``as_of`` that has no sim marker yet.

    Returns how many outbox rows were simulated. Opens, clicks, redemptions and voluntary
    cancellations are deterministic rolls of the outbox id, so a replay writes the same events.
    """
    ops, synth = settings.schemas.ops, settings.schemas.synth
    outbox = wh.query(
        f"SELECT outbox_id, subscriber_key, intervention_type, offer_code, discount_pct, created_at "
        f"FROM {ops}.OUTBOX WHERE status = 'READY' AND created_at <= CAST(:as_of AS TIMESTAMP)",
        {"as_of": as_of},
    )
    if outbox.empty:
        return 0
    seen = wh.query(
        f"SELECT DISTINCT campaign_id FROM {synth}.email_events WHERE event_id LIKE 'sim-%'"
    )
    done = set(seen["campaign_id"]) if not seen.empty else set()
    pending = outbox[~outbox["outbox_id"].isin(done)]
    if pending.empty:
        return 0
    ctes = contract_ctes(contract_for(settings), settings.key_pepper, ["subscriptions"], include_raw_id=True)
    mapped = wh.query(
        f"WITH {ctes} SELECT subscriber_key, customer_id, subscription_id, status FROM subscriptions"
    )
    by_key: dict[str, list] = {}
    if not mapped.empty:
        for row in mapped.itertuples(index=False):
            by_key.setdefault(row.subscriber_key, []).append(row)
    active = set(settings.scoring.active_statuses)
    emails: list[dict] = []
    orders: list[dict] = []
    events: list[dict] = []
    cancels: list[tuple[str, pd.Timestamp]] = []
    for row in pending.itertuples(index=False):
        digest = _digest(row.outbox_id)
        created = pd.Timestamp(row.created_at).to_pydatetime()
        subs = by_key.get(row.subscriber_key) or []
        customer_id = subs[0].customer_id if subs else None
        if customer_id is None:
            continue
        send_at = _when(created, 0)
        emails.append(_email(row.outbox_id, customer_id, "send", send_at))
        if digest[0] < 140:
            emails.append(_email(row.outbox_id, customer_id, "open", _when(created, 1)))
        if digest[1] < 64:
            emails.append(_email(row.outbox_id, customer_id, "click", _when(created, 2)))
        if row.offer_code and digest[2] < 102:
            orders.append(_order(row.outbox_id, customer_id, _when(created, 3), row.discount_pct))
        if digest[3] < 38:
            cancel_at = _when(created, 10)
            for sub in subs:
                if sub.status in active:
                    cancels.append((sub.subscription_id, cancel_at))
                    events.append(
                        {
                            "subscription_id": sub.subscription_id,
                            "event_id": f"sim-{row.outbox_id}-{sub.subscription_id}-cancel",
                            "event_type": "cancel",
                            "event_ts": cancel_at,
                            "loaded_at": cancel_at,
                        }
                    )
    if emails:
        wh.insert_frame(f"{synth}.email_events", pd.DataFrame(emails))
    if orders:
        wh.insert_frame(f"{synth}.orders", pd.DataFrame(orders))
    if events:
        wh.insert_frame(f"{synth}.subscription_events", pd.DataFrame(events))
    for subscription_id, cancel_at in cancels:
        wh.execute(
            f"UPDATE {synth}.subscriptions SET status = 'CANCELLED', cancel_type = 'voluntary', "
            "cancel_reason_code = 'sim_responder', cancelled_at = :at WHERE subscription_id = :s",
            {"at": cancel_at.to_pydatetime(), "s": subscription_id},
        )
    return sum(row["event_type"] == "send" for row in emails)


def _email(outbox_id: str, customer_id: str, kind: str, moment: pd.Timestamp) -> dict:
    return {
        "customer_id": customer_id,
        "event_id": f"sim-{outbox_id}-{kind}",
        "event_type": kind,
        "campaign_id": outbox_id,
        "flow_id": "sim_responder",
        "event_ts": _email_ts(moment),
        "loaded_at": moment,
    }


def _order(outbox_id: str, customer_id: str, moment: pd.Timestamp, discount_pct: float | None) -> dict:
    pct = 10.0 if discount_pct is None or pd.isna(discount_pct) else float(discount_pct)
    amount = 40.0
    return {
        "order_id": f"sim-{outbox_id}-order",
        "customer_id": customer_id,
        "ordered_at": moment,
        "blend_sku": "sim",
        "amount_usd": round(amount * (1 - pct / 100), 2),
        "discount_usd": round(amount * pct / 100, 2) or 0.01,
        "refund_usd": 0.0,
        "refunded_at": None,
        "loaded_at": moment,
    }
