"""Hand-built subscribers over a six-view contract (FIXSRC), one per edge-case category plus a full one.

All data are relative to ``AS_OF`` so the known values in the tests are easy to check by hand.
``load(wh)`` creates the schema and tables and returns the contract; ``SUBS`` maps a subscriber name to
its ``subscription_id`` (the feature frame is indexed by it in the tests).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from churn.contract.loader import Contract

AS_OF = datetime(2025, 6, 30)
SCHEMA = "FIXSRC"
LOADED = datetime(2025, 1, 1)  # loaded long before AS_OF unless a row says otherwise

SUBS = {
    name: f"s_{name}"
    for name in (
        "alpha",  # full history in every domain (known-value subscriber)
        "no_quiz",
        "no_email",
        "no_tickets",
        "single_order",
        "new",  # tenure 10 days
        "partial",  # NULLs in non-key columns
        "short_tenure",  # tenure 30 days (n = 4 whole weeks)
        "no_data",  # subscription only
    )
}
EDGE_CASES = ["no_quiz", "no_email", "no_tickets", "single_order", "new", "partial"]
EXCLUDED = {"cancelled": "s_cancelled", "future": "s_future"}  # not active at AS_OF


def ago(days: float = 0, hours: float = 0) -> datetime:
    return AS_OF - timedelta(days=days, hours=hours)


def _contract() -> Contract:
    def col(source: str, type_: str) -> dict:
        return {"source": source, "type": type_}

    def view(table: str, dedup: str, columns: dict) -> dict:
        return {"object": f"{{schema}}.{table}", "dedup_key": dedup, "loaded_at": "loaded_at", "columns": columns}

    return Contract(
        schema_name=SCHEMA,
        views={
            "subscriptions": view(
                "subscriptions",
                "subscription_id",
                {
                    "customer_id": col("customer_id", "varchar"),
                    "subscription_id": col("subscription_id", "varchar"),
                    "status": col("status", "varchar"),
                    "started_at": col("started_at", "timestamp"),
                    "cancelled_at": col("cancelled_at", "timestamp"),
                    "price_usd": col("price_usd", "number"),
                    "interval_days": col("interval_days", "number"),
                },
            ),
            "orders": view(
                "orders",
                "order_id",
                {
                    "order_id": col("order_id", "varchar"),
                    "customer_id": col("customer_id", "varchar"),
                    "ordered_at": col("ordered_at", "timestamp"),
                    "amount_usd": col("amount_usd", "number"),
                    "discount_usd": col("discount_usd", "number"),
                    "refund_usd": col("refund_usd", "number"),
                    "refunded_at": col("refunded_at", "timestamp"),
                },
            ),
            "subscription_events": view(
                "subscription_events",
                "event_id",
                {
                    "subscription_id": col("subscription_id", "varchar"),
                    "event_id": col("event_id", "varchar"),
                    "event_type": col("event_type", "varchar"),
                    "event_ts": col("event_ts", "timestamp"),
                },
            ),
            "email_events": view(
                "email_events",
                "event_id",
                {
                    "customer_id": col("customer_id", "varchar"),
                    "event_id": col("event_id", "varchar"),
                    "event_type": col("event_type", "varchar"),
                    "event_ts": col("event_ts", "timestamp"),
                },
            ),
            "support_tickets": view(
                "support_tickets",
                "ticket_id",
                {
                    "customer_id": col("customer_id", "varchar"),
                    "ticket_id": col("ticket_id", "varchar"),
                    "created_at": col("created_at", "timestamp"),
                    "category": col("category", "varchar"),
                    "escalated": col("escalated", "boolean"),
                    "sentiment_score": col("sentiment_score", "number"),
                },
            ),
            "quiz_responses": view(
                "quiz_responses",
                "answer_id",  # surrogate single-column dedup key (the contract needs one column)
                {
                    "answer_id": col("answer_id", "varchar"),
                    "customer_id": col("customer_id", "varchar"),
                    "quiz_session_id": col("quiz_session_id", "varchar"),
                    "question_key": col("question_key", "varchar"),
                    "answer_value": col("answer_value", "varchar"),
                    "answered_at": col("answered_at", "timestamp"),
                    "recommended_sku": col("recommended_sku", "varchar"),
                },
            ),
        },
    )


DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE {SCHEMA}.subscriptions (customer_id VARCHAR, subscription_id VARCHAR, status VARCHAR,
  started_at TIMESTAMP, cancelled_at TIMESTAMP, price_usd DOUBLE, interval_days DOUBLE, loaded_at TIMESTAMP);
CREATE TABLE {SCHEMA}.orders (order_id VARCHAR, customer_id VARCHAR, ordered_at TIMESTAMP, amount_usd DOUBLE,
  discount_usd DOUBLE, refund_usd DOUBLE, refunded_at TIMESTAMP, loaded_at TIMESTAMP);
CREATE TABLE {SCHEMA}.subscription_events (subscription_id VARCHAR, event_id VARCHAR, event_type VARCHAR,
  event_ts TIMESTAMP, loaded_at TIMESTAMP);
CREATE TABLE {SCHEMA}.email_events (customer_id VARCHAR, event_id VARCHAR, event_type VARCHAR,
  event_ts TIMESTAMP, loaded_at TIMESTAMP);
CREATE TABLE {SCHEMA}.support_tickets (customer_id VARCHAR, ticket_id VARCHAR, created_at TIMESTAMP,
  category VARCHAR, escalated BOOLEAN, sentiment_score DOUBLE, loaded_at TIMESTAMP);
CREATE TABLE {SCHEMA}.quiz_responses (answer_id VARCHAR, customer_id VARCHAR, quiz_session_id VARCHAR,
  question_key VARCHAR, answer_value VARCHAR, answered_at TIMESTAMP, recommended_sku VARCHAR,
  loaded_at TIMESTAMP)
"""


def _cust(name: str) -> str:
    return f"c_{name}"


class _Rows:
    def __init__(self) -> None:
        self.t: dict[str, list[dict]] = {k: [] for k in
                                         ("subscriptions", "orders", "subscription_events", "email_events",
                                          "support_tickets", "quiz_responses")}
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def sub(self, name: str, tenure_days: int, status: str = "ACTIVE", cancelled_at=None,
            started_at=None, interval_days: float = 30) -> None:
        self.t["subscriptions"].append({
            "customer_id": _cust(name), "subscription_id": f"s_{name}", "status": status,
            "started_at": started_at or ago(tenure_days), "cancelled_at": cancelled_at, "price_usd": 40.0,
            "interval_days": interval_days, "loaded_at": LOADED})

    def order(self, name, at, amount=40.0, discount=0.0, refund=None, refunded_at=None, loaded=LOADED):
        self.t["orders"].append({"order_id": self._id("o"), "customer_id": _cust(name), "ordered_at": at,
                                     "amount_usd": amount, "discount_usd": discount, "refund_usd": refund,
                                     "refunded_at": refunded_at, "loaded_at": loaded})

    def sub_event(self, name, kind, at, loaded=LOADED):
        self.t["subscription_events"].append({"subscription_id": f"s_{name}", "event_id": self._id("e"),
                                                  "event_type": kind, "event_ts": at, "loaded_at": loaded})

    def email(self, name, kind, at, loaded=LOADED):
        self.t["email_events"].append({"customer_id": _cust(name), "event_id": self._id("m"),
                                           "event_type": kind, "event_ts": at, "loaded_at": loaded})

    def ticket(self, name, at, escalated, sentiment, category="billing", loaded=LOADED):
        self.t["support_tickets"].append({"customer_id": _cust(name), "ticket_id": self._id("t"),
                                              "created_at": at, "category": category, "escalated": escalated,
                                              "sentiment_score": sentiment, "loaded_at": loaded})

    def quiz(self, name, session, at, answers=3, loaded=LOADED):
        for k in range(answers):
            self.t["quiz_responses"].append({
                "answer_id": self._id("a"), "customer_id": _cust(name), "quiz_session_id": session,
                "question_key": f"q{k}", "answer_value": "x", "answered_at": at + timedelta(minutes=k),
                "recommended_sku": "sku_a", "loaded_at": loaded})


def _alpha(r: _Rows, name: str = "alpha", quiz: bool = True, email: bool = True, tickets: bool = True,
           single_order: bool = False) -> None:
    """Full history. Known values (AS_OF = 2025-06-30):

    email   last-30d: 4 sends / 3 opens; days 31-90: 10 sends / 4 opens; clicks: 2 at age 3d, 1 at 20d, 4 at 80d
    orders  recent 60d: 2; prior 60d: 3; skips recent 60d: 3, prior 60d: 1
    switches 2 in last 90d, 1 in days 91-180, 1 at age 300d
    tickets last 30d: 2 (1 escalated; sentiment -0.5, -0.1); days 31-90: 3 (1 escalated; 0.2, 0.4, 0.0)
    quiz    sessions at age 200d and 40d
    """
    r.sub(name, 546)
    if single_order:
        r.order(name, ago(20))
    else:
        for d in (10, 45, 70, 100, 115, 200, 230, 400):
            r.order(name, ago(d), amount=40.0, discount=4.0 if d < 365 else 0.0)
        r.order(name, ago(150), amount=40.0, discount=0.0, refund=40.0, refunded_at=ago(148))
    for d in (5, 30, 50):  # recent skips
        r.sub_event(name, "skip", ago(d))
    r.sub_event(name, "skip", ago(90))  # prior skip
    r.sub_event(name, "pause", ago(100))
    for d in (20, 60):
        r.sub_event(name, "blend_switch", ago(d))
    r.sub_event(name, "blend_switch", ago(150))
    r.sub_event(name, "blend_switch", ago(300))
    if email:
        for d in (2, 9, 16, 23):
            r.email(name, "send", ago(d))
        for d in (2, 9, 16):
            r.email(name, "open", ago(d, hours=-1))
        for d in (35, 40, 45, 50, 55, 60, 65, 70, 75, 80):
            r.email(name, "send", ago(d))
        for d in (35, 45, 55, 65):
            r.email(name, "open", ago(d, hours=-1))
        for d in (3, 3, 20, 80, 80, 80, 80):
            r.email(name, "click", ago(d))
    if tickets:
        r.ticket(name, ago(3), True, -0.5)
        r.ticket(name, ago(12), False, -0.1)
        r.ticket(name, ago(40), True, 0.2)
        r.ticket(name, ago(55), False, 0.4)
        r.ticket(name, ago(80), False, 0.0)
        r.ticket(name, ago(200), True, -0.9)  # outside 90 days
    if quiz:
        r.quiz(name, "qz1", ago(200))
        r.quiz(name, "qz2", ago(40))


def load(wh) -> Contract:
    """Create FIXSRC with every subscriber's rows. Returns the contract that maps the six views."""
    wh.execute(DDL)
    r = _Rows()
    _alpha(r, "alpha")
    _alpha(r, "no_quiz", quiz=False)
    _alpha(r, "no_email", email=False)
    _alpha(r, "no_tickets", tickets=False)
    _alpha(r, "single_order", single_order=True)

    # brand new: 10 days, everything inside the first 10 days
    r.sub("new", 10)
    r.order("new", ago(9))
    r.email("new", "send", ago(5))
    r.email("new", "open", ago(5, hours=-1))
    r.email("new", "click", ago(4))
    r.email("new", "click", ago(2))

    # 30-day tenure: n = 4 whole weeks. Clicks at age 3d (2), 10d (1), 24d (3); an unrelated send/open
    r.sub("short_tenure", 30)
    r.order("short_tenure", ago(29))
    r.email("short_tenure", "send", ago(6))
    for d in (3, 3, 10, 24, 24, 24):
        r.email("short_tenure", "click", ago(d))

    # partial: NULLs in non-key columns
    r.sub("partial", 200)
    r.order("partial", ago(15), amount=None, discount=None)
    r.email("partial", "send", ago(10))
    r.email("partial", "send", None)
    r.ticket("partial", ago(10), None, None, category=None)
    r.quiz("partial", "qzp", ago(100), answers=1)

    r.sub("no_data", 120)

    # never active at AS_OF
    r.sub("cancelled", 300, status="CANCELLED", cancelled_at=ago(10))
    r.sub("future", 0, started_at=AS_OF + timedelta(days=3))

    for table, rows in r.t.items():
        df = pd.DataFrame(rows)
        for c in df.columns:
            if c.endswith(("_at", "_ts")):
                df[c] = pd.to_datetime(df[c])
        wh.insert_frame(f"{SCHEMA}.{table}", df)
    return _contract()


def add_row(wh, table: str, **values) -> None:
    """Insert one extra row (used by the leakage test)."""
    df = pd.DataFrame([values])
    for c in df.columns:
        if c.endswith(("_at", "_ts")):
            df[c] = pd.to_datetime(df[c])
    wh.insert_frame(f"{SCHEMA}.{table}", df)
