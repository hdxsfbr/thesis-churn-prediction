"""Hand-built source data for the feature tests.

``alpha`` and ``beta`` carry known values for every leading indicator (see the comments next to
each event). The ``EDGE`` subscribers are one per edge-case category of the PRD (§7).
Everything is written into its own schema (``FIXSRC``) behind a six-view contract, so the
feature SQL is exercised exactly as it would be against real views.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from churn.contract.loader import Contract
from churn.settings import Settings

AS_OF = datetime(2025, 6, 30)
SCHEMA = "FIXSRC"

EDGE = {
    "no_quiz": "edge-no-quiz",
    "no_email": "edge-no-email",
    "no_tickets": "edge-no-tickets",
    "single_order": "edge-single-order",
    "new": "edge-new",
    "partial": "edge-partial",
}

# logical view -> (table, dedup key, {column: (contract type, DDL type)})
_T = {"varchar": "VARCHAR", "timestamp": "TIMESTAMP", "number": "DOUBLE", "boolean": "BOOLEAN"}
NEW_VIEWS: dict[str, tuple[str, dict[str, str]]] = {
    # ARCHITECTURE §4.2: subscription_events is keyed by subscription_id (no customer_id)
    "subscription_events": (
        "event_id",
        {"subscription_id": "varchar", "event_id": "varchar", "event_type": "varchar", "event_ts": "timestamp"},
    ),
    "email_events": (
        "event_id",
        {"event_id": "varchar", "customer_id": "varchar", "event_type": "varchar", "event_ts": "timestamp"},
    ),
    "support_tickets": (
        "ticket_id",
        {
            "ticket_id": "varchar",
            "customer_id": "varchar",
            "created_at": "timestamp",
            "escalated": "boolean",
            "sentiment_score": "number",
        },
    ),
    # §4.2 grain is one row per answer; answer_id is a fixture-only surrogate that serves as the dedup key
    "quiz_responses": (
        "answer_id",
        {
            "answer_id": "varchar",
            "customer_id": "varchar",
            "quiz_session_id": "varchar",
            "question_key": "varchar",
            "answer_value": "varchar",
            "answered_at": "timestamp",
            "recommended_sku": "varchar",
        },
    ),
}
BASE_COLUMNS = {
    "subscriptions": {
        "customer_id": "varchar", "subscription_id": "varchar", "status": "varchar", "started_at": "timestamp",
        "cancelled_at": "timestamp", "cancel_type": "varchar", "cancel_reason_code": "varchar",
        "price_usd": "number", "interval_days": "number",
    },
    "orders": {
        "order_id": "varchar", "customer_id": "varchar", "ordered_at": "timestamp", "blend_sku": "varchar",
        "amount_usd": "number", "discount_usd": "number", "refund_usd": "number", "refunded_at": "timestamp",
    },
}


def ago(days: float, noon: bool = True) -> datetime:
    """A moment ``days`` before AS_OF (at noon of that day, so day-bucket maths has no edge effects)."""
    return AS_OF - timedelta(days=days) + (timedelta(hours=12) if noon else timedelta(0))


def subscriber_key(customer_id: str, settings: Settings) -> str:
    return hashlib.sha256((settings.key_pepper + customer_id).encode()).hexdigest()


def fixture_contract(base: Contract, schema: str = SCHEMA) -> Contract:
    """The default contract plus the four domain views, all pointing into ``schema``."""
    views = {name: view.model_dump() for name, view in base.views.items()}
    for name, (dedup, cols) in NEW_VIEWS.items():
        views[name] = {
            "object": "{schema}." + name,
            "dedup_key": dedup,
            "loaded_at": "loaded_at",
            "columns": {c: {"source": c, "type": t} for c, t in cols.items()},
        }
    return Contract(schema_name=schema, views=views)


@dataclass
class Source:
    rows: dict[str, list[dict]] = field(
        default_factory=lambda: {t: [] for t in ["subscriptions", "orders", *NEW_VIEWS]}
    )
    _n: int = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:05d}"

    def subscriber(self, cid, started_days_ago, price=40.0, interval=30, status="ACTIVE", **extra):
        started = ago(started_days_ago)
        self.rows["subscriptions"].append(
            {"customer_id": cid, "subscription_id": f"s-{cid}", "status": status, "started_at": started,
             "cancelled_at": None, "cancel_type": None, "cancel_reason_code": None,
             "price_usd": price, "interval_days": interval, "loaded_at": started, **extra}
        )

    def order(self, cid, days_ago, amount=40.0, discount=0.0, sku="SKU-A", loaded_at=None, **extra):
        at = ago(days_ago)
        self.rows["orders"].append(
            {"order_id": self._id("o"), "customer_id": cid, "ordered_at": at, "blend_sku": sku,
             "amount_usd": amount, "discount_usd": discount, "refund_usd": 0.0, "refunded_at": None,
             "loaded_at": loaded_at or at, **extra}
        )

    def sub_event(self, cid, kind, days_ago, at=None, loaded_at=None):
        at = at or ago(days_ago)
        self.rows["subscription_events"].append(
            {"subscription_id": f"s-{cid}", "event_id": self._id("e"), "event_type": kind, "event_ts": at,
             "loaded_at": loaded_at or at}
        )

    def email(self, cid, kind, days_ago, at=None, loaded_at=None):
        at = at or ago(days_ago)
        self.rows["email_events"].append(
            {"event_id": self._id("m"), "customer_id": cid, "event_type": kind, "event_ts": at,
             "loaded_at": loaded_at or at}
        )

    def ticket(self, cid, days_ago, escalated=False, sentiment=0.0, loaded_at=None):
        at = ago(days_ago)
        self.rows["support_tickets"].append(
            {"ticket_id": self._id("t"), "customer_id": cid, "created_at": at, "escalated": escalated,
             "sentiment_score": sentiment, "loaded_at": loaded_at or at}
        )

    def quiz(self, cid, days_ago, loaded_at=None, answers=3):
        """One quiz completion = one session of ``answers`` answers; the last answer is at ``days_ago``."""
        session = self._id("qs")
        done = ago(days_ago)
        for i in range(answers):
            at = done - timedelta(minutes=5 * (answers - 1 - i))
            self.rows["quiz_responses"].append(
                {"answer_id": self._id("qa"), "customer_id": cid, "quiz_session_id": session,
                 "question_key": f"q{i + 1}", "answer_value": "a", "answered_at": at,
                 "recommended_sku": "SKU-A" if i == answers - 1 else None, "loaded_at": loaded_at or at}
            )


def _ddl(view: str) -> str:
    cols = BASE_COLUMNS.get(view) or NEW_VIEWS[view][1]
    body = ", ".join(f"{c} {_T[t]}" for c, t in cols.items())
    return f"CREATE OR REPLACE TABLE {SCHEMA}.{view} ({body}, loaded_at TIMESTAMP)"


def load(wh, source: Source) -> None:
    """(Re)create the FIXSRC tables and fill them."""
    wh.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    for view, rows in source.rows.items():
        wh.execute(_ddl(view))
        if rows:
            cols = [*(BASE_COLUMNS.get(view) or NEW_VIEWS[view][1]), "loaded_at"]
            wh.insert_frame(f"{SCHEMA}.{view}", pd.DataFrame(rows)[cols])


def known_values() -> Source:
    """alpha and beta: every leading indicator has a hand-computed value (see tests)."""
    s = Source()

    # ---- alpha: 400 days old, ships every 30 days -------------------------------------------
    a = "alpha"
    s.subscriber(a, 400)
    for d in (10, 40, 70, 100, 115, 200, 300):  # orders: 60d=2 (10,40); prior 60d=3 (70,100,115)
        s.order(a, d)
    # email: last 30 days 10 sends / 3 opens; days 31-90 20 sends / 4 opens; one send+open beyond 90 days
    s.email(a, "send", 30, at=AS_OF - timedelta(days=30))  # exactly 30 days ago belongs to the recent window
    for d in (1, 4, 7, 10, 13, 16, 19, 22, 25):
        s.email(a, "send", d)
    for d in (2, 12, 22):
        s.email(a, "open", d)
    for d in range(31, 91, 3):
        s.email(a, "send", d)
    for d in (35, 50, 65, 80):
        s.email(a, "open", d)
    s.email(a, "send", 100)
    s.email(a, "open", 100)
    # clicks over the 13 weeks ending at as_of: 3 in the newest week, 2 in the middle one, 1 in the oldest
    for d in (1, 2, 3, 49, 49.5, 88):
        s.email(a, "click", d)
    # skips: recent 60d = 1, prior 60d = 2
    for d in (20, 75, 110):
        s.sub_event(a, "skip", d)
    # blend switches: 90d = 3, 365d = 5 (the one at 400 days is outside)
    for d in (5, 50, 80, 150, 250, 400):
        s.sub_event(a, "blend_switch", d)
    s.sub_event(a, "pause", 30)  # other event types must not be counted as switches
    # tickets: 30d = 2 (1 escalated, sentiment -0.6 and -0.2); days 31-90 = 3 (1 escalated, 0.4, 0.2, 0.0)
    s.ticket(a, 3, True, -0.6)
    s.ticket(a, 20, False, -0.2)
    s.ticket(a, 40, False, 0.4)
    s.ticket(a, 60, True, 0.2)
    s.ticket(a, 80, False, 0.0)
    s.ticket(a, 200, True, -0.9)  # beyond 90 days: ignored
    # quizzes: first one 500 days ago (before the subscription), retakes 200 and 20 days ago
    for d in (500, 200, 20):
        s.quiz(a, d)

    # ---- beta: skips rising -----------------------------------------------------------------
    b = "beta"
    s.subscriber(b, 300)
    s.order(b, 10)  # recent 60d: 1 order, 3 skips
    for d in (15, 30, 45):
        s.sub_event(b, "skip", d)
    for d in (70, 90, 110):  # prior 60d: 3 orders, 1 skip
        s.order(b, d)
    s.sub_event(b, "skip", 100)
    return s


def short_tenure() -> Source:
    """gamma: 100 days of tenure, so every prior window is only partly covered (tenure-truncated windows)."""
    s = Source()
    g = "gamma"
    s.subscriber(g, 100)
    for d in (10, 60):  # blend switches: last 90 days = 2; prior window [tenure start, as_of-90d) = 10 days long: 1
        s.sub_event(g, "blend_switch", d)
    s.sub_event(g, "blend_switch", 95)
    s.sub_event(g, "skip", 20)  # skips: last 60 days = 1; prior 60 days covers only 40 days of tenure: 2
    for d in (70, 90):
        s.sub_event(g, "skip", d)
    for d in (10, 40):  # orders: last 60 days = 2; prior 60 days (40 covered) = 1
        s.order(g, d)
    s.order(g, 80)
    for d in (150, 95, 80, 30):  # quiz sessions: the first (150 days ago) is not a retake; retakes 95 / 80 / 30
        s.quiz(g, d)
    return s


def edge_cases() -> Source:
    """One subscriber per edge-case category (PRD §7): each is missing a domain or has partial rows."""
    s = Source()
    # full history except quiz
    cid = EDGE["no_quiz"]
    s.subscriber(cid, 300)
    for d in (10, 40, 70):
        s.order(cid, d)
    s.email(cid, "send", 5)
    s.email(cid, "open", 5)
    s.ticket(cid, 12, False, -0.1)
    # full history except email
    cid = EDGE["no_email"]
    s.subscriber(cid, 300)
    for d in (10, 40, 70):
        s.order(cid, d)
    s.quiz(cid, 100)
    s.ticket(cid, 12, True, -0.5)
    # full history except tickets
    cid = EDGE["no_tickets"]
    s.subscriber(cid, 300)
    for d in (10, 40, 70):
        s.order(cid, d)
    s.email(cid, "send", 5)
    s.quiz(cid, 100)
    # a single order
    cid = EDGE["single_order"]
    s.subscriber(cid, 200)
    s.order(cid, 60)
    # brand new: three days old, nothing else
    s.subscriber(EDGE["new"], 3)
    # partial records: price, interval and order amounts are NULL, an order without sku
    cid = EDGE["partial"]
    s.subscriber(cid, 150, price=None, interval=None)
    s.order(cid, 20, amount=None, discount=None, sku=None)
    s.email(cid, "open", 3)  # an open with no matching send
    s.ticket(cid, 9, False, None)
    return s
