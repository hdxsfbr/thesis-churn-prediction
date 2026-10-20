"""Hand-built source fixture for the feature tests.

One fully populated subscriber (``c_full``) with known expected values for the five leading indicators,
plus one subscriber per edge-case category. Everything is written to its own schema (``FIXSRC``) behind a
complete six-view source contract, so these tests do not depend on the synthetic generator.

``AS_OF`` is the scoring date. ``ago(n)`` is "n days before AS_OF".
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from churn.contract.loader import Contract, load_contract
from churn.settings import Settings
from churn.warehouse.base import Warehouse

SCHEMA = "FIXSRC"
AS_OF = datetime(2025, 6, 30)

# customer ids that make up the edge-case categories (R1.2 / R2.4)
EDGE_CASES: dict[str, str] = {
    "no_quiz": "c_no_quiz",
    "no_email": "c_no_email",
    "no_tickets": "c_no_tickets",
    "single_order": "c_single_order",
    "new_tenure_under_30d": "c_new",
    "nulls": "c_nulls",
    "nothing_but_subscription": "c_bare",
    "paused": "c_paused",
    "past_due": "c_past_due",
    "late_arriving": "c_late",
}
ACTIVE_CUSTOMERS = ["c_full", *EDGE_CASES.values()]
INACTIVE_CUSTOMERS = ["c_cancelled", "c_future"]  # cancelled before AS_OF / starts after AS_OF

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE OR REPLACE TABLE {SCHEMA}.subscriptions (
    customer_id VARCHAR, subscription_id VARCHAR, status VARCHAR, started_at TIMESTAMP,
    cancelled_at TIMESTAMP, cancel_type VARCHAR, cancel_reason_code VARCHAR, price_usd DOUBLE,
    interval_days BIGINT, loaded_at TIMESTAMP);
CREATE OR REPLACE TABLE {SCHEMA}.orders (
    order_id VARCHAR, customer_id VARCHAR, ordered_at TIMESTAMP, blend_sku VARCHAR, amount_usd DOUBLE,
    discount_usd DOUBLE, refund_usd DOUBLE, refunded_at TIMESTAMP, loaded_at TIMESTAMP);
CREATE OR REPLACE TABLE {SCHEMA}.subscription_events (
    event_id VARCHAR, subscription_id VARCHAR, event_type VARCHAR, event_ts TIMESTAMP, loaded_at TIMESTAMP);
CREATE OR REPLACE TABLE {SCHEMA}.email_events (
    event_id VARCHAR, customer_id VARCHAR, event_type VARCHAR, campaign_id VARCHAR, flow_id VARCHAR,
    event_ts TIMESTAMP, loaded_at TIMESTAMP);
CREATE OR REPLACE TABLE {SCHEMA}.support_tickets (
    ticket_id VARCHAR, customer_id VARCHAR, created_at TIMESTAMP, category VARCHAR, escalated BOOLEAN,
    resolution_hours DOUBLE, sentiment_score DOUBLE, loaded_at TIMESTAMP);
CREATE OR REPLACE TABLE {SCHEMA}.quiz_responses (
    answer_id VARCHAR, customer_id VARCHAR, quiz_session_id VARCHAR, question_key VARCHAR,
    answer_value VARCHAR, answered_at TIMESTAMP, recommended_sku VARCHAR, loaded_at TIMESTAMP);
"""


def ago(days: float) -> datetime:
    return AS_OF - timedelta(days=days)


def contract_dict(schema: str = SCHEMA, domains: tuple[str, ...] | None = None) -> dict:
    def v(dedup: str, **cols: tuple[str]) -> dict:
        return {
            "object": None,
            "dedup_key": dedup,
            "loaded_at": "loaded_at",
            "columns": {k: {"source": k, "type": t[0]} for k, t in cols.items()},
        }

    views = {
        "subscriptions": v(
            "subscription_id", customer_id=("varchar",), subscription_id=("varchar",), status=("varchar",),
            started_at=("timestamp",), cancelled_at=("timestamp",), cancel_type=("varchar",),
            cancel_reason_code=("varchar",), price_usd=("number",), interval_days=("number",)),
        "orders": v(
            "order_id", order_id=("varchar",), customer_id=("varchar",), ordered_at=("timestamp",),
            blend_sku=("varchar",), amount_usd=("number",), discount_usd=("number",),
            refund_usd=("number",), refunded_at=("timestamp",)),
        "subscription_events": v(
            "event_id", subscription_id=("varchar",), event_id=("varchar",), event_type=("varchar",),
            event_ts=("timestamp",)),
        "email_events": v(
            "event_id", customer_id=("varchar",), event_id=("varchar",), event_type=("varchar",),
            campaign_id=("varchar",), flow_id=("varchar",), event_ts=("timestamp",)),
        "support_tickets": v(
            "ticket_id", customer_id=("varchar",), ticket_id=("varchar",), created_at=("timestamp",),
            category=("varchar",), escalated=("boolean",), resolution_hours=("number",),
            sentiment_score=("number",)),
        "quiz_responses": v(
            "answer_id", customer_id=("varchar",), quiz_session_id=("varchar",), question_key=("varchar",),
            answer_value=("varchar",), answered_at=("timestamp",), recommended_sku=("varchar",)),
    }
    for name, view in views.items():
        view["object"] = "{schema}." + name
    keep = domains if domains is not None else tuple(views)
    return {"schema": schema, "views": {n: views[n] for n in views if n in keep or n == "subscriptions"}}


def write_contract(path: Path, schema: str = SCHEMA, domains: tuple[str, ...] | None = None) -> Contract:
    path.write_text(yaml.safe_dump(contract_dict(schema, domains)))
    return load_contract(path)


def subscriber_key(settings: Settings, customer_id: str) -> str:
    return hashlib.sha256((settings.key_pepper + customer_id).encode()).hexdigest()


class Source:
    """Accumulates fixture rows per table, then writes them in one go."""

    def __init__(self, schema: str = SCHEMA) -> None:
        self.schema = schema
        self.rows: dict[str, list[dict]] = {
            t: [] for t in ("subscriptions", "orders", "subscription_events", "email_events",
                            "support_tickets", "quiz_responses")
        }
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:05d}"

    def subscription(self, cid: str, started_days_ago: float, status: str = "ACTIVE", price: float | None = 30.0,
                     interval: int = 30, cancelled_days_ago: float | None = None) -> None:
        started = ago(started_days_ago)
        self.rows["subscriptions"].append({
            "customer_id": cid, "subscription_id": "s_" + cid, "status": status, "started_at": started,
            "cancelled_at": ago(cancelled_days_ago) if cancelled_days_ago is not None else pd.NaT,
            "cancel_type": "voluntary" if cancelled_days_ago is not None else None,
            "cancel_reason_code": None, "price_usd": price, "interval_days": interval, "loaded_at": started})

    def order(self, cid: str, days_ago: float, amount: float | None = 30.0, discount: float | None = 0.0,
              refunded_days_ago: float | None = None, sku: str | None = "BLEND_A", loaded: datetime | None = None) -> None:
        ts = ago(days_ago)
        self.rows["orders"].append({
            "order_id": self._id("o"), "customer_id": cid, "ordered_at": ts, "blend_sku": sku,
            "amount_usd": amount, "discount_usd": discount,
            "refund_usd": 1.0 if refunded_days_ago is not None else 0.0,
            "refunded_at": ago(refunded_days_ago) if refunded_days_ago is not None else pd.NaT,
            "loaded_at": loaded or ts})

    def sub_event(self, cid: str, kind: str, days_ago: float, loaded: datetime | None = None) -> None:
        ts = ago(days_ago)
        self.rows["subscription_events"].append({
            "event_id": self._id("e"), "subscription_id": "s_" + cid, "event_type": kind, "event_ts": ts,
            "loaded_at": loaded or ts})

    def email(self, cid: str, kind: str, days_ago: float, event_id: str | None = None,
              loaded: datetime | None = None) -> None:
        ts = ago(days_ago)
        self.rows["email_events"].append({
            "event_id": event_id or self._id("m"), "customer_id": cid, "event_type": kind,
            "campaign_id": "camp1", "flow_id": None, "event_ts": ts, "loaded_at": loaded or ts})

    def ticket(self, cid: str, days_ago: float, escalated: bool | None, sentiment: float | None,
               loaded: datetime | None = None) -> None:
        ts = ago(days_ago)
        self.rows["support_tickets"].append({
            "ticket_id": self._id("t"), "customer_id": cid, "created_at": ts, "category": "billing",
            "escalated": escalated, "resolution_hours": None if sentiment is None else 12.0,
            "sentiment_score": sentiment, "loaded_at": loaded or ts})

    def quiz(self, cid: str, session: str, days_ago: float, answers: int = 2, loaded: datetime | None = None) -> None:
        ts = ago(days_ago)
        for i in range(answers):
            self.rows["quiz_responses"].append({
                "answer_id": self._id("a"), "customer_id": cid, "quiz_session_id": f"{cid}_{session}",
                "question_key": f"q{i}", "answer_value": "x", "answered_at": ts, "recommended_sku": "BLEND_A",
                "loaded_at": loaded or ts})

    def write(self, wh: Warehouse) -> None:
        wh.execute(DDL.replace(SCHEMA, self.schema))
        for table, rows in self.rows.items():
            if rows:
                wh.insert_frame(f"{self.schema}.{table}", pd.DataFrame(rows))


def populate_full_subscriber(src: Source, cid: str = "c_full") -> None:
    """Known values (AS_OF = 2025-06-30, windows are [AS_OF - n days, AS_OF)):

    email_open_rate_30 = 1/4, email_open_rate_90 = 7/12, delta = 1/4 - 7/12
    skips_60 = 4, skips_prior_60 = 1, orders_60 = 2, orders_prior_60 = 2 -> skip rates 2/3 and 1/3
    ticket_escalation_score = 2 + (2 - 4/3) + 0.4 = 3.0666...
    days_since_last_quiz = 40, quiz_retake_count_365 = 2
    blend_switch_count_90 = 3
    """
    src.subscription(cid, 400)
    for days, discount, refunded in [(20, 0, None), (50, 6, 45), (80, 0, None), (110, 0, None),
                                     (200, 3, None), (300, 0, None)]:
        src.order(cid, days, 30.0, float(discount), refunded)
    for d in (5, 10, 15, 20):  # last 30 days: 4 sends, 1 open
        src.email(cid, "send", d)
    src.email(cid, "open", 10)
    for d in (40, 45, 50, 55, 60, 70, 80, 85):  # days 31-90: 8 sends, 6 opens
        src.email(cid, "send", d)
    for d in (45, 50, 55, 60, 70, 80):
        src.email(cid, "open", d)
    src.email(cid, "send", 120)  # outside both windows
    for d in (5, 15, 25, 35):
        src.sub_event(cid, "skip", d)
    src.sub_event(cid, "skip", 70)
    src.sub_event(cid, "pause", 100)
    for d in (30, 60, 85, 200):
        src.sub_event(cid, "blend_switch", d)
    for d, esc, sent in [(10, True, -0.5), (20, False, -0.1), (60, True, 0.4), (80, False, 0.6), (200, True, 0.2)]:
        src.ticket(cid, d, esc, sent)
    src.quiz(cid, "s1", 300, 2)
    src.quiz(cid, "s2", 100, 3)
    src.quiz(cid, "s3", 40, 2)


def build_source(schema: str = SCHEMA) -> Source:
    """The standard population: c_full plus one subscriber per edge-case category."""
    src = Source(schema)
    populate_full_subscriber(src)

    def rich(cid: str, quiz: bool = True, email: bool = True, tickets: bool = True, started: float = 250,
             **sub_kw) -> None:
        src.subscription(cid, started, **sub_kw)
        for d in (20, 50, 80, 150):
            src.order(cid, d)
        if email:
            for d in (5, 25, 55, 75):
                src.email(cid, "send", d)
            src.email(cid, "open", 25)
            src.email(cid, "open", 55)
        if tickets:
            src.ticket(cid, 15, False, 0.2)
        if quiz:
            src.quiz(cid, "s1", 120)
        src.sub_event(cid, "skip", 30)

    rich("c_no_quiz", quiz=False)
    rich("c_no_email", email=False)
    rich("c_no_tickets", tickets=False)
    rich("c_paused", status="PAUSED")
    rich("c_past_due", status="PAST_DUE")

    src.subscription("c_single_order", 90)
    src.order("c_single_order", 40)

    src.subscription("c_new", 10)
    src.order("c_new", 5)
    src.email("c_new", "send", 4)
    src.email("c_new", "open", 3)

    src.subscription("c_nulls", 200, price=None)
    src.order("c_nulls", 30, amount=None, discount=None, sku=None)
    src.order("c_nulls", 60, amount=30.0, discount=None)
    src.ticket("c_nulls", 10, None, None)
    src.email("c_nulls", "send", 10)

    src.subscription("c_bare", 200)

    # c_late: rows with an event date before AS_OF that were only loaded after it, plus a duplicated event.
    src.subscription("c_late", 200)
    src.order("c_late", 20)
    src.order("c_late", 10, loaded=ago(-3))
    src.email("c_late", "send", 8, event_id="dup")
    src.email("c_late", "send", 8, event_id="dup", loaded=ago(7))
    src.email("c_late", "open", 8, loaded=ago(-2))

    src.subscription("c_cancelled", 300, status="CANCELLED", cancelled_days_ago=20)
    src.order("c_cancelled", 100)
    src.subscription("c_future", -5)  # starts after AS_OF
    return src


def feature_frame_by_customer(wh: Warehouse, settings: Settings, contract: Contract) -> pd.DataFrame:
    """Feature rows at AS_OF indexed by customer id."""
    from churn.features.builder import build_features

    frame = build_features(wh, settings, contract, AS_OF)
    by_key = {subscriber_key(settings, c): c for c in [*ACTIVE_CUSTOMERS, *INACTIVE_CUSTOMERS]}
    out = frame.assign(customer_id=frame["subscriber_key"].map(by_key))
    assert out["customer_id"].notna().all(), "unknown subscriber_key in feature frame"
    return out.set_index("customer_id")
