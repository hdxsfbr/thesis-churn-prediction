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
    unit: str = "number"  # number | days | usd | ratio | share | orders | refunds | points | signed
    windows: tuple[int, ...] = ()  # look-back windows in days (R2.2); one entry = single window
    trend: bool = False  # compares windows or fits a slope
    leading_indicator: bool = False  # one of the R2.1 leading indicators


def _f(name, domain, description, missing, label, unit="number", windows=(), trend=False, leading=False):
    return Feature(name, domain, description, missing, label, unit, tuple(windows), trend, leading)


# Domains that must each carry a multi-window or slope feature (R2.2), with the contract view they read.
DOMAIN_VIEWS: dict[str, str] = {
    "orders": "orders",
    "subscription_events": "subscription_events",
    "email": "email_events",
    "support": "support_tickets",
    "quiz": "quiz_responses",
}
TREND_DOMAINS: tuple[str, ...] = tuple(DOMAIN_VIEWS)

_HAS = "0 (no visible rows in this domain)"

FEATURES: tuple[Feature, ...] = (
    _f("tenure_days", "subscription", "Days since the subscription started", "never missing",
       "Subscribed for {value}", "days"),
    _f("price_usd", "subscription", "Subscription price per shipment", "NULL (price not recorded)",
       "Price per shipment is {value}", "usd"),
    _f("interval_days", "subscription", "Days between shipments", "NULL (interval not recorded)",
       "Ships every {value}", "days"),
    # orders
    _f("has_orders", "orders", "1 if the subscriber has any order before as_of", _HAS,
       "Has placed orders: {value}"),
    _f("orders_total", "orders", "Orders placed before as_of", "0",
       "{value} placed in total", "orders"),
    _f("orders_90", "orders", "Orders in the 90 days before as_of", "0",
       "{value} in the last 90 days", "orders", (90,)),
    _f("orders_365", "orders", "Orders in the 365 days before as_of", "0",
       "{value} in the last year", "orders", (365,)),
    _f("orders_trend_60", "orders", "Orders per 60 days in the last 60 days minus the rate in the 60 days before; each window is truncated "
       "to the subscriber's tenure (count / min(tenure, window) * 60)",
       "NULL (tenure of 60 days or less: no prior window)",
       "Orders changed by {value} over the last 60 days vs the 60 before", "signed", (60, 120), True),
    _f("days_since_last_order", "orders", "Days since the latest order", "NULL",
       "Last order was {value} ago", "days"),
    _f("overdue_ratio", "orders", "Days since last order / shipment interval", "NULL",
       "Time since last order is {value} the usual shipment interval", "ratio"),
    _f("discount_share_365", "orders", "Discount / gross order value, 365 days", "NULL",
       "{value} of order value was discounted in the last year", "share", (365,)),
    _f("refund_count_365", "orders", "Refunded orders in the 365 days", "0",
       "{value} in the last year", "refunds", (365,)),
    # subscription events
    _f("has_subscription_events", "subscription_events", "1 if the subscriber has any shipment-modification event",
       _HAS, "Has shipment changes on record: {value}"),
    _f("skip_rate_60_vs_prior_60", "subscription_events",
       "Skips per shipment (skips + orders) in the last 60 days minus the same rate in the 60 days before",
       "NULL (no shipments in either window, or no events)",
       "Skip rate changed by {value} over the last 60 days vs the 60 before", "points", (60, 120), True, True),
    _f("skip_count_trend", "subscription_events", "Skips per 60 days in the last 60 days minus the rate in the 60 days before; each window is truncated "
       "to the subscriber's tenure (count / min(tenure, window) * 60)",
       "NULL (tenure of 60 days or less: no prior window); 0 when there are no events", "Skips changed by {value} over the last 60 days vs the 60 before", "signed", (60, 120), True, True),
    _f("blend_switch_count_90", "subscription_events", "Blend switches in the 90 days before as_of", "0",
       "{value} blend switches in the last 90 days", "number", (90,), False, True),
    _f("blend_switch_count_365", "subscription_events", "Blend switches in the 365 days before as_of", "0",
       "{value} blend switches in the last year", "number", (365,)),
    _f("blend_switch_rate_change", "subscription_events",
       "Switches per 30 days in the last 90 days minus the rate in days 91-365, each window truncated to the "
       "subscriber's tenure (normalised by min(tenure, window))",
       "NULL (tenure of 90 days or less: no prior window); 0 when there are no events",
       "Blend switching rate changed by {value} per month vs the prior year", "signed", (90, 365), True, True),
    # email
    _f("has_email", "email", "1 if the subscriber has any email event before as_of", _HAS,
       "Has email history: {value}"),
    _f("email_open_rate_30", "email", "Opens / sends over the last 30 days", "NULL (no sends)",
       "Opened {value} of emails in the last 30 days", "share", (30,)),
    _f("email_open_rate_delta_30_90", "email",
       "Open rate over the last 30 days minus the open rate over days 31 to 90 (disjoint windows)",
       "NULL (no sends in either window)",
       "Email open rate changed by {value} vs the prior 60 days", "points", (30, 90), True, True),
    _f("email_click_slope_90", "email",
       "Least-squares slope of weekly clicks over 13 weeks (91 days) ending at as_of, in clicks per week",
       "NULL (no email events in the window)",
       "Weekly email clicks are trending by {value} per week", "signed", (91,), True, True),
    _f("email_unsubscribed", "email", "1 if an unsubscribe event was seen before as_of", "0",
       "Unsubscribed from email: {value}"),
    # support
    _f("has_support", "support", "1 if the subscriber has any ticket before as_of", _HAS,
       "Has support history: {value}"),
    _f("tickets_30", "support", "Support tickets in the last 30 days", "0",
       "{value} support tickets in the last 30 days", "number", (30,)),
    _f("tickets_90", "support", "Support tickets in the last 90 days", "0",
       "{value} support tickets in the last 90 days", "number", (90,)),
    _f("tickets_escalated_90", "support", "Escalated tickets in the last 90 days", "0",
       "{value} escalated tickets in the last 90 days", "number", (90,)),
    _f("sentiment_delta_30_90", "support",
       "Mean ticket sentiment over the last 30 days minus the mean over days 31 to 90", "NULL (no tickets in a window)",
       "Support sentiment changed by {value} vs the prior 60 days", "signed", (30, 90), True),
    _f("ticket_escalation_score", "support",
       "tickets_30 + 2 * tickets_escalated_90 + 3 * max(0, -sentiment_delta_30_90) (missing delta counts as 0)",
       "0", "Support escalation score is {value}", "signed", (30, 90), True, True),
    # quiz
    _f("has_quiz", "quiz", "1 if the subscriber has completed a quiz before as_of", _HAS,
       "Has taken the quiz: {value}"),
    _f("days_since_last_quiz", "quiz", "Days since the latest quiz completion (latest quiz session, dated at its last answer)", "NULL (no quiz)",
       "Last quiz was {value} ago", "days", (), False, True),
    _f("quiz_retake_count_90", "quiz", "Quiz completions (distinct quiz sessions) after the first one, in the last 90 days", "0",
       "{value} quiz retakes in the last 90 days", "number", (90,)),
    _f("quiz_retake_trend_90", "quiz",
       "Quiz retakes per 90 days in the last 90 days minus the rate in days 91 to 180; each window is truncated "
       "to the subscriber's tenure (count / min(tenure, window) * 90)",
       "NULL (tenure of 90 days or less: no prior window)",
       "Quiz retakes changed by {value} over the last 90 days vs the 90 before", "signed", (90, 180), True),
    _f("quiz_retake_count_365", "quiz", "Quiz completions (distinct quiz sessions) after the first one, in the last 365 days", "0",
       "{value} quiz retakes in the last year", "number", (365,)),
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
    if feature.unit == "points":
        return f"{value * 100:+.0f} percentage points"
    if feature.unit == "signed":
        return f"{value:+,.2f}"
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


def render_features_md() -> str:
    """The text of docs/features.md, generated from the catalogue so the docs cannot drift."""
    lines = [
        "# Feature catalogue",
        "",
        "Generated from `src/churn/features/registry.py` by `python -m churn.features.registry`; do not edit by hand.",
        "A test fails when this file differs from the registry.",
        "",
        "All features are computed per active subscriber at a scoring date `:as_of` from rows with",
        "the logical event timestamp (`event_ts`; `ordered_at`, `created_at` or `answered_at` in their views) before `:as_of` and `loaded_at <= :as_of` only (no leakage). Windows are `[as_of - N days, as_of)`;",
        "a comparison such as 30 vs 90 uses disjoint windows (last 30 days against days 31 to 90).",
        "",
        "Missing data: counts default to 0, rates and recencies stay NULL, and every domain has a",
        "`has_<domain>` indicator. A domain whose source view is not mapped yields the same defaults, never an error.",
        "Trend features that compare a recent and a prior window (orders, skips, blend switches, quiz retakes) divide each",
        "window's count by its tenure-truncated length, `min(tenure, window)`, so a new subscriber is not shown a false rise;",
        "they are NULL when the subscriber has no prior window yet. Each feature's missing policy is in its row.",
        "",
        "Leading indicators (R2.1) are marked with a star in the table.",
        "",
    ]
    domains = list(dict.fromkeys(f.domain for f in FEATURES))
    for domain in domains:
        view = DOMAIN_VIEWS.get(domain)
        lines.append(f"## {domain}")
        lines.append("")
        lines.append(f"Source view: `{view}`." if view else "Source view: `subscriptions`.")
        lines.append("")
        lines.append("| Feature | Leading | Windows (days) | Kind | Missing policy | Description |")
        lines.append("|---|---|---|---|---|---|")
        for f in FEATURES:
            if f.domain != domain:
                continue
            windows = ", ".join(str(w) for w in f.windows) or "-"
            kind = "trend/slope" if f.trend else "multi-window" if len(f.windows) > 1 else "point/single window"
            star = "★" if f.leading_indicator else ""
            lines.append(f"| `{f.name}` | {star} | {windows} | {kind} | {f.missing} | {f.description} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":  # python -m churn.features.registry
    from pathlib import Path

    target = Path(__file__).resolve().parents[3] / "docs" / "features.md"
    target.write_text(render_features_md())
    print(f"wrote {target}")
