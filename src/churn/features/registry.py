"""Feature catalogue: model input columns, in a fixed order, with descriptions and factor labels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    name: str
    domain: str
    description: str
    missing: str  # policy when the source has no data
    # Label template for explanations: "{value}" is replaced by the formatted value.
    label: str = ""
    unit: str = "number"  # number | days | usd | ratio | share | orders | refunds


FEATURES: tuple[Feature, ...] = (
    Feature("tenure_days", "subscription", "Days since the subscription started", "never missing",
            "Subscribed for {value}", "days"),
    Feature("price_usd", "subscription", "Subscription price per shipment", "never missing",
            "Price per shipment is {value}", "usd"),
    Feature("interval_days", "subscription", "Days between shipments", "never missing",
            "Ships every {value}", "days"),
    Feature("orders_total", "orders", "Orders placed before as_of", "0",
            "{value} placed in total", "orders"),
    Feature("orders_90", "orders", "Orders in the 90 days before as_of", "0",
            "{value} in the last 90 days", "orders"),
    Feature("orders_365", "orders", "Orders in the 365 days before as_of", "0",
            "{value} in the last year", "orders"),
    Feature("days_since_last_order", "orders", "Days since the latest order", "NULL",
            "Last order was {value} ago", "days"),
    Feature("overdue_ratio", "orders", "Days since last order / shipment interval", "NULL",
            "Time since last order is {value} the usual shipment interval", "ratio"),
    Feature("discount_share_365", "orders", "Discount / gross order value, 365 days", "NULL",
            "{value} of order value was discounted in the last year", "share"),
    Feature("refund_count_365", "orders", "Refunded orders in the 365 days", "0",
            "{value} in the last year", "refunds"),
)

FEATURE_COLUMNS: list[str] = [f.name for f in FEATURES]
KEY_COLUMNS: list[str] = ["subscriber_key", "subscription_id"]
FEATURE_BY_NAME: dict[str, Feature] = {f.name: f for f in FEATURES}


def _plural(n: float, one: str, many: str) -> str:
    return f"{n:g} {one if n == 1 else many}"


def format_value(feature: Feature, value: float) -> str:
    """Render a raw feature value for people: units, rounding, no scientific notation."""
    if feature.unit == "days":
        n = round(value)
        return _plural(n, "day", "days")
    if feature.unit == "usd":
        return f"${value:,.2f}"
    if feature.unit == "ratio":
        return f"{value:.1f}×"
    if feature.unit == "share":
        return f"{value * 100:.0f}%"
    if feature.unit == "orders":
        return _plural(round(value), "order", "orders")
    if feature.unit == "refunds":
        return _plural(round(value), "refunded order", "refunded orders")
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render_label(name: str, value: float) -> str:
    """Human-readable sentence for one factor, e.g. 'Last order was 74 days ago'."""
    feature = FEATURE_BY_NAME[name]
    if not feature.label.strip():
        raise ValueError(f"feature {name!r} has no label template")
    text = feature.label.format(value=format_value(feature, value))
    return text[0].upper() + text[1:]
