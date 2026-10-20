"""Feature catalogue: model input columns, in a fixed order, with descriptions and factor labels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Feature:
    name: str
    domain: str
    description: str
    missing: str  # policy when the source has no data
    # Label template for explanations: "{value}" is replaced by the formatted value.
    label: str = ""
    unit: str = "number"  # number | days | usd | ratio | share | orders | refunds | rate | delta | score | flag
    windows: tuple[int, ...] = ()  # look-back windows in days (a "prior 60" window, days 61-120, is listed as 120)
    trend: str = ""  # "" | "multi_window" | "delta" | "ratio" | "slope": how windows are combined
    leading_indicator: bool = False  # one of the five R2.1 indicators


# One SQL file per domain under sql/features/ (the "subscription" domain is the master query).
DOMAINS: tuple[str, ...] = ("subscription", "subscription_events", "orders", "email", "support", "quiz")

DOMAIN_TITLES: dict[str, str] = {
    "subscription": "Subscription (`sql/features/subscriptions.sql`)",
    "subscription_events": "Subscription events (`sql/features/subscription_events.sql`)",
    "orders": "Orders (`sql/features/orders.sql`)",
    "email": "Email engagement (`sql/features/email.sql`)",
    "support": "Support (`sql/features/support.sql`)",
    "quiz": "Quiz (`sql/features/quiz.sql`)",
}

FEATURES: tuple[Feature, ...] = (
    # --- subscription -----------------------------------------------------------------------------
    Feature("tenure_days", "subscription", "Days since the subscription started", "never missing",
            "Subscribed for {value}", "days"),
    Feature("price_usd", "subscription", "Subscription price per shipment", "never missing",
            "Price per shipment is {value}", "usd"),
    Feature("interval_days", "subscription", "Days between shipments", "never missing",
            "Ships every {value}", "days"),
    Feature("paid_to_list_price_90", "subscription",
            "Average net amount paid per order in the last 90 days / list price (discount drift)", "NULL",
            "Recent orders were paid at {value} of the list price", "share", (90,)),
    # --- orders -------------------------------------------------------------------------------------
    Feature("orders_total", "orders", "Orders placed before as_of", "0",
            "{value} placed in total", "orders"),
    Feature("orders_60", "orders", "Orders in the 60 days before as_of", "0",
            "{value} in the last 60 days", "orders", (60,)),
    Feature("orders_prior_60", "orders", "Orders in the 60 days before the last 60 (days 61-120)", "0",
            "{value} in the 60 days before that", "orders", (120,)),
    Feature("orders_90", "orders", "Orders in the 90 days before as_of", "0",
            "{value} in the last 90 days", "orders", (90,)),
    Feature("orders_365", "orders", "Orders in the 365 days before as_of", "0",
            "{value} in the last year", "orders", (365,)),
    Feature("orders_per_30d_90", "orders",
            "Orders per 30 days over the last 90 days, normalised by min(tenure, 90) for new subscribers",
            "0", "Ordering about {value} per 30 days", "number", (90,)),
    Feature("orders_ratio_90_365", "orders",
            "Orders in the last 90 days / orders in the last 365 days (a steady subscriber is near 90/365)", "NULL",
            "{value} of the last year's orders came in the last 90 days", "share", (90, 365), "ratio"),
    Feature("days_since_last_order", "orders", "Days since the latest order", "NULL",
            "Last order was {value} ago", "days"),
    Feature("overdue_ratio", "orders", "Days since last order / shipment interval", "NULL",
            "Time since last order is {value} the usual shipment interval", "ratio"),
    Feature("discount_share_365", "orders", "Discount / gross order value, 365 days", "NULL",
            "{value} of order value was discounted in the last year", "share", (365,)),
    Feature("refund_count_365", "orders", "Refunded orders in the 365 days", "0",
            "{value} in the last year", "refunds", (365,)),
    # --- subscription events ------------------------------------------------------------------------
    Feature("has_subscription_events", "subscription_events",
            "1 when any subscription event (skip, pause, switch...) is visible for the subscriber", "0",
            "Subscription events on file: {value}", "flag"),
    Feature("skips_60", "subscription_events", "Skipped shipments in the last 60 days", "0",
            "{value} in the last 60 days", "skips", (60,)),
    Feature("skip_rate_60", "subscription_events", "Skips / (skips + orders) in the last 60 days", "NULL",
            "{value} of recent shipments were skipped", "share", (60,)),
    Feature("skip_rate_prior_60", "subscription_events",
            "Skips / (skips + orders) in the 60 days before the last 60", "NULL",
            "{value} of shipments were skipped in the 60 days before that", "share", (120,)),
    Feature("skip_rate_delta_60", "subscription_events",
            "Skip rate, last 60 days minus the prior 60 days: shipment skip frequency trend", "NULL",
            "Skip rate changed by {value} vs the prior 60 days", "delta", (60, 120), "delta",
            leading_indicator=True),
    Feature("pause_events_180", "subscription_events", "Pause events in the last 180 days", "0",
            "{value} in the last 180 days", "pauses", (180,)),
    Feature("blend_switch_count_90", "subscription_events",
            "Blend switches in the last 90 days: blend-switching frequency", "0",
            "{value} in the last 90 days", "switches", (90,), leading_indicator=True),
    Feature("blend_switch_count_365", "subscription_events", "Blend switches in the last 365 days", "0",
            "{value} in the last year", "switches", (365,)),
    # --- email --------------------------------------------------------------------------------------
    Feature("has_email", "email", "1 when any email event is visible for the subscriber", "0",
            "Email history on file: {value}", "flag"),
    Feature("email_open_rate_30", "email", "Opens per send in the last 30 days", "NULL",
            "Opened {value} of emails in the last 30 days", "share", (30,)),
    Feature("email_open_rate_90", "email", "Opens per send in the last 90 days", "NULL",
            "Opened {value} of emails in the last 90 days", "share", (90,)),
    Feature("email_open_rate_delta_30_90", "email",
            "Open rate over 30 days minus open rate over 90 days: email engagement velocity", "NULL",
            "Email open rate moved {value} (30 vs 90 days)", "delta", (30, 90), "delta",
            leading_indicator=True),
    Feature("email_click_rate_90", "email", "Clicks per send in the last 90 days", "NULL",
            "Clicked {value} of emails in the last 90 days", "share", (90,)),
    Feature("days_since_last_open", "email", "Days since the latest email open", "NULL",
            "Last email open was {value} ago", "days"),
    Feature("email_unsubscribed", "email", "1 when an unsubscribe event is visible", "0",
            "Unsubscribed from email: {value}", "flag"),
    # --- support ------------------------------------------------------------------------------------
    Feature("has_support", "support", "1 when any support ticket is visible for the subscriber", "0",
            "Support history on file: {value}", "flag"),
    Feature("tickets_30", "support", "Tickets opened in the last 30 days", "0",
            "{value} in the last 30 days", "tickets", (30,)),
    Feature("tickets_90", "support", "Tickets opened in the last 90 days", "0",
            "{value} in the last 90 days", "tickets", (90,)),
    Feature("escalated_90", "support", "Escalated tickets in the last 90 days", "0",
            "{value} escalated in the last 90 days", "tickets", (90,)),
    Feature("sentiment_delta_30_90", "support",
            "Average ticket sentiment over 30 days minus over 90 days", "NULL",
            "Ticket sentiment moved {value} (30 vs 90 days)", "score", (30, 90), "delta"),
    Feature("ticket_escalation_score", "support",
            "Escalated tickets (90d) + burst of tickets (30d above a third of the 90d count) + worsening sentiment",
            "0", "Support escalation score is {value}", "score", (30, 90), "multi_window",
            leading_indicator=True),
    # --- quiz ---------------------------------------------------------------------------------------
    Feature("has_quiz", "quiz", "1 when the subscriber has any quiz response", "0",
            "Quiz on file: {value}", "flag"),
    Feature("days_since_last_quiz", "quiz", "Days since the latest quiz answer: time since last retake", "NULL",
            "Last quiz was {value} ago", "days", leading_indicator=True),
    Feature("quiz_sessions_90", "quiz", "Distinct quiz sessions in the last 90 days", "0",
            "{value} in the last 90 days", "sessions", (90,)),
    Feature("quiz_retake_count_365", "quiz",
            "Quiz sessions in the last 365 days beyond the subscriber's first ever", "0",
            "{value} in the last year", "retakes", (365,)),
    Feature("quiz_session_share_90_365", "quiz",
            "Quiz sessions in the last 90 days / quiz sessions in the last 365 days", "NULL",
            "{value} of the last year's quiz sessions were in the last 90 days", "share", (90, 365), "ratio"),
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
    plurals = {"skips": ("skip", "skips"), "pauses": ("pause", "pauses"), "switches": ("blend switch", "blend switches"),
               "tickets": ("ticket", "tickets"), "sessions": ("quiz session", "quiz sessions"),
               "retakes": ("quiz retake", "quiz retakes")}
    if feature.unit in plurals:
        return _plural(round(value), *plurals[feature.unit])
    if feature.unit == "delta":
        return f"{value * 100:+.0f} points"
    if feature.unit == "flag":
        return "yes" if value >= 0.5 else "no"
    if feature.unit == "score":
        return f"{value:+.2f}" if feature.name.startswith("sentiment") else f"{value:.1f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render_label(name: str, value: float) -> str:
    """Human-readable sentence for one factor, e.g. 'Last order was 74 days ago'."""
    feature = FEATURE_BY_NAME[name]
    if not feature.label.strip():
        raise ValueError(f"feature {name!r} has no label template")
    text = feature.label.format(value=format_value(feature, value))
    return text[0].upper() + text[1:]


def is_trend(feature: Feature) -> bool:
    """Structural check: a trend labels how windows combine and must span two or more distinct windows."""
    return bool(feature.trend) and len(set(feature.windows)) >= 2


# The subscription domain is the master query (tenure, price, discount drift); the R2.2 trend requirement
# covers the five behavioural domains only.
TREND_DOMAINS: tuple[str, ...] = tuple(d for d in DOMAINS if d != "subscription")


def render_features_md() -> str:
    """The text of docs/features.md, generated from FEATURES so the documentation cannot drift."""
    lines = [
        "# Feature catalogue",
        "",
        "<!-- Generated by `python -m churn.features.registry`; do not edit by hand. -->",
        "",
        "Every feature is computed in the warehouse by the SQL under `sql/features/`, bounded by `:as_of`:",
        "only rows with an event time before `as_of` that were loaded at or before `as_of` are visible",
        "(R2.3, no leakage). Windows are `[as_of - n days, as_of)`; a \"prior 60\" window is days 61-120, listed as window 120.",
        "",
        "Missing data (R2.4): counts default to `0`, rates, recencies and deltas stay `NULL` (LightGBM handles",
        "NULL natively), and each optional domain has a `has_<domain>` indicator. A domain whose view is absent",
        "from the source contract produces the same values, never an error. Brand-new subscribers use",
        "tenure-truncated windows (`orders_per_30d_90` divides by `min(tenure, 90)`).",
        "",
        "The five leading indicators required by R2.1 are marked **leading**. A **trend** column names the way a",
        "feature combines windows (R2.2): `multi_window` (the same quantity over several windows), `delta` (one",
        "window minus another) or `ratio`. A trend feature always spans two or more distinct windows; the `subscription` domain has none.",
        "",
    ]
    for domain in DOMAINS:
        lines += [f"## {DOMAIN_TITLES[domain]}", "", "| Feature | Description | Windows (days) | Trend | Missing | Leading |",
                  "|---|---|---|---|---|---|"]
        for f in FEATURES:
            if f.domain != domain:
                continue
            windows = ", ".join(str(w) for w in f.windows) or "none"
            lines.append(
                f"| `{f.name}` | {f.description} | {windows} | {f.trend or '-'} | {f.missing} | "
                f"{'**leading**' if f.leading_indicator else ''} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_features_md(path: Path | str = "docs/features.md") -> Path:
    path = Path(path)
    path.write_text(render_features_md())
    return path


if __name__ == "__main__":
    print(f"wrote {write_features_md(Path(__file__).resolve().parents[3] / 'docs' / 'features.md')}")
