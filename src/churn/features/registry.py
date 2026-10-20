"""Feature catalogue: model input columns, in a fixed order, with descriptions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    name: str
    domain: str
    description: str
    missing: str  # policy when the source has no data


FEATURES: tuple[Feature, ...] = (
    Feature("tenure_days", "subscription", "Days since the subscription started", "never missing"),
    Feature("price_usd", "subscription", "Subscription price per shipment", "never missing"),
    Feature("interval_days", "subscription", "Days between shipments", "never missing"),
    Feature("orders_total", "orders", "Orders placed before as_of", "0"),
    Feature("orders_90", "orders", "Orders in the 90 days before as_of", "0"),
    Feature("orders_365", "orders", "Orders in the 365 days before as_of", "0"),
    Feature("days_since_last_order", "orders", "Days since the latest order", "NULL"),
    Feature("overdue_ratio", "orders", "Days since last order / shipment interval", "NULL"),
    Feature("discount_share_365", "orders", "Discount / gross order value, 365 days", "NULL"),
    Feature("refund_count_365", "orders", "Refunded orders in the 365 days", "0"),
)

FEATURE_COLUMNS: list[str] = [f.name for f in FEATURES]
KEY_COLUMNS: list[str] = ["subscriber_key", "subscription_id"]
