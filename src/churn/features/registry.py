"""Feature catalogue: model input columns, in a fixed order, with descriptions and factor labels.

``render_features_md()`` generates ``docs/features.md`` from this list; regenerate it with
``python -m churn.features.registry`` (a test fails when the two drift).
"""

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
    # number | days | usd | usd_delta | ratio | share | orders | refunds | count | points | slope | score | flag
    unit: str = "number"
    windows: tuple[int, ...] = ()  # look-back windows in days (a trend lists both compared windows)
    trend: bool = False  # compares two windows or fits a slope
    leading_indicator: bool = False  # one of the §6.2 leading indicators


F = Feature
FEATURES: tuple[Feature, ...] = (
    # --- subscription (static attributes) ---
    F("tenure_days", "subscription", "Days since the subscription started", "never missing",
      "Subscribed for {value}", "days"),
    F("price_usd", "subscription", "Subscription price per shipment", "never missing",
      "Price per shipment is {value}", "usd"),
    F("interval_days", "subscription", "Days between shipments", "never missing",
      "Ships every {value}", "days"),
    # --- orders ---
    F("orders_total", "orders", "Orders placed before as_of", "0", "{value} placed in total", "orders"),
    F("orders_90", "orders", "Orders in the 90 days before as_of", "0", "{value} in the last 90 days", "orders",
      windows=(90,)),
    F("orders_365", "orders", "Orders in the 365 days before as_of", "0", "{value} in the last year", "orders",
      windows=(365,)),
    F("days_since_last_order", "orders", "Days since the latest order", "NULL",
      "Last order was {value} ago", "days"),
    F("overdue_ratio", "orders", "Days since last order / shipment interval", "NULL",
      "Time since last order is {value} the usual shipment interval", "ratio"),
    F("discount_share_365", "orders", "Discount / gross order value, 365 days", "NULL",
      "{value} of order value was discounted in the last year", "share", windows=(365,)),
    F("refund_count_365", "orders", "Refunded orders in the 365 days", "0", "{value} in the last year",
      "refunds", windows=(365,)),
    F("refund_amount_365", "orders", "Refunded amount in the 365 days", "0",
      "{value} refunded in the last year", "usd", windows=(365,)),
    F("orders_trend_60", "orders",
      "Orders per 60 days in the recent 60 days minus the prior 60 days, each window normalised by "
      "min(tenure, 60)", "NULL when there is no prior window yet (tenure <= 60 days)",
      "Order pace changed by {value} per 60 days versus the prior 60 days", "slope", windows=(60, 60),
      trend=True),
    F("price_change_90", "orders",
      "Average order amount (USD) in the last 90 days minus the average in days 91 to 180",
      "NULL when either window has no orders", "Average order value changed by {value} versus days 91 to 180",
      "usd_delta", windows=(90, 180), trend=True),
    # --- subscription events ---
    F("has_subscription_events", "subscription_events", "1 if any subscription event exists before as_of",
      "never missing", "Subscription activity on record: {value}", "flag"),
    F("skip_count_60", "subscription_events", "Skips in the 60 days before as_of", "0",
      "{value} skipped in the last 60 days", "count", windows=(60,)),
    F("skip_rate_60_vs_prior_60", "subscription_events",
      "Skips per scheduled shipment (skips / (skips + orders)) in the last 60 days minus the same rate in the "
      "prior 60 days", "NULL when either window has no scheduled shipments",
      "Skip rate changed by {value} versus the prior 60 days", "points", windows=(60, 60), trend=True,
      leading_indicator=True),
    F("skip_count_trend", "subscription_events",
      "Skips per 60 days in the recent 60 days minus the prior 60 days, each window normalised by "
      "min(tenure, 60)", "NULL when there is no prior window yet (tenure <= 60 days)",
      "Skips changed by {value} per 60 days versus the prior 60 days", "slope", windows=(60, 60), trend=True,
      leading_indicator=True),
    F("blend_switch_count_90", "subscription_events", "Blend switches in the 90 days before as_of", "0",
      "{value} in the last 90 days", "count", windows=(90,), leading_indicator=True),
    F("blend_switch_count_365", "subscription_events", "Blend switches in the 365 days before as_of", "0",
      "{value} in the last year", "count", windows=(365,)),
    F("blend_switch_rate_change", "subscription_events",
      "Blend switches per 90 days in the recent 90 days minus the prior 90 days, each window normalised by "
      "min(tenure, 90)", "NULL when there is no prior window yet (tenure <= 90 days)",
      "Blend switching changed by {value} per 90 days versus the prior 90 days", "slope", windows=(90, 90),
      trend=True, leading_indicator=True),
    F("pause_events_180", "subscription_events", "Pauses in the 180 days before as_of", "0",
      "{value} in the last 180 days", "count", windows=(180,)),
    F("status_paused", "subscription_events",
      "1 if the latest pause/resume event before as_of is a pause (never reads subscriptions.status)",
      "0 when there are no pause or resume events", "Currently paused: {value}", "flag"),
    F("status_past_due", "subscription",
      "1 if subscriptions.status is PAST_DUE, 0 otherwise; computed only at the live run date, because the "
      "source holds today's status only",
      "NULL in history (training snapshots and backfills), 0/1 only in the live run",
      "Payment is past due: {value}", "flag"),
    # --- email ---
    F("has_email", "email", "1 if any email event exists before as_of", "never missing",
      "Email history on record: {value}", "flag"),
    F("email_unsubscribed", "email", "1 if the subscriber ever unsubscribed from email", "0",
      "Unsubscribed from email: {value}", "flag"),
    F("email_open_rate_90", "email", "Opens / sends in the 90 days before as_of", "NULL when no sends",
      "{value} of emails opened in the last 90 days", "share", windows=(90,)),
    F("email_open_rate_delta_30_90", "email",
      "Open rate over the last 30 days minus the open rate over days 31 to 90 (disjoint windows)",
      "NULL when either window has no sends",
      "Email open rate changed by {value} versus days 31 to 90", "points", windows=(30, 90), trend=True,
      leading_indicator=True),
    F("email_click_slope_90", "email",
      "Least-squares slope of weekly email clicks (clicks per week, per week) over the last 13 weeks, "
      "truncated to the n = min(13, floor(tenure_days / 7)) whole weeks inside the tenure",
      "NULL when n < 2 (tenure under 14 days) or when there are no email events in the n weeks",
      "Weekly email clicks are trending by {value}", "slope", windows=(90,), trend=True,
      leading_indicator=True),
    # --- support ---
    F("has_support", "support", "1 if any support ticket exists before as_of", "never missing",
      "Support history on record: {value}", "flag"),
    F("tickets_30", "support", "Tickets opened in the last 30 days", "0", "{value} in the last 30 days",
      "count", windows=(30,)),
    F("tickets_90", "support", "Tickets opened in the last 90 days", "0", "{value} in the last 90 days",
      "count", windows=(90,)),
    F("escalated_tickets_90", "support", "Escalated tickets in the last 90 days", "0",
      "{value} escalated in the last 90 days", "count", windows=(90,)),
    F("ticket_sentiment_delta_30_90", "support",
      "Average ticket sentiment over the last 30 days minus the average over days 31 to 90",
      "NULL when either window has no scored ticket",
      "Ticket sentiment changed by {value} versus days 31 to 90", "slope", windows=(30, 90), trend=True),
    F("ticket_escalation_score", "support",
      "(tickets_30 - tickets_90 / 3) + 2 * escalated_tickets_90 - ticket_sentiment_delta_30_90 (a missing delta "
      "counts as 0); tickets_90 / 3 is the 30-day pace implied by the 90-day count",
      "0 when there are no tickets",
      "Support escalation score is {value}", "score", windows=(30, 90), trend=True, leading_indicator=True),
    # --- quiz ---
    F("has_quiz", "quiz", "1 if the subscriber has answered the quiz before as_of", "never missing",
      "Quiz taken: {value}", "flag"),
    F("days_since_last_quiz", "quiz", "Days since the latest quiz answer", "NULL when no quiz",
      "Last quiz was {value} ago", "days", leading_indicator=True),
    F("quiz_retake_count_365", "quiz",
      "Quiz sessions started in the last 365 days, not counting the very first session ever", "0",
      "{value} in the last year", "count", windows=(365,)),
    F("quiz_retake_trend_90", "quiz",
      "Quiz sessions per 90 days in the recent 90 days minus the prior 90 days, each window normalised by "
      "min(tenure, 90)", "NULL when no quiz or no prior window yet (tenure <= 90 days)",
      "Quiz retakes changed by {value} per 90 days versus the prior 90 days", "slope", windows=(90, 90),
      trend=True),
)

FEATURE_COLUMNS: list[str] = [f.name for f in FEATURES]
KEY_COLUMNS: list[str] = ["subscriber_key", "subscription_id"]
FEATURE_BY_NAME: dict[str, Feature] = {f.name: f for f in FEATURES}
DOCS_PATH = Path(__file__).resolve().parents[3] / "docs" / "features.md"


def _plural(n: float, one: str, many: str) -> str:
    return f"{n:g} {one if n == 1 else many}"


def format_value(feature: Feature, value: float) -> str:
    """Render a raw feature value for people: units, rounding, no scientific notation."""
    if feature.unit == "days":
        return _plural(round(value), "day", "days")
    if feature.unit == "usd":
        return f"${value:,.2f}"
    if feature.unit == "ratio":
        return f"{value:.1f}×"
    if feature.unit == "share":
        return f"{value * 100:.0f}%"
    if feature.unit == "usd_delta":
        return f"{'-' if value < 0 else '+'}${abs(value):,.2f}"
    if feature.unit == "points":
        return f"{value * 100:+.0f} points"
    if feature.unit == "slope":
        return f"{value:+.2f}"
    if feature.unit == "orders":
        return _plural(round(value), "order", "orders")
    if feature.unit == "refunds":
        return _plural(round(value), "refunded order", "refunded orders")
    if feature.unit == "count":
        return f"{round(value):g}"
    if feature.unit == "flag":
        return "yes" if value >= 0.5 else "no"
    if feature.unit == "score":
        return f"{value:.1f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render_label(name: str, value: float) -> str:
    """Human-readable sentence for one factor, e.g. 'Last order was 74 days ago'."""
    feature = FEATURE_BY_NAME[name]
    if not feature.label.strip():
        raise ValueError(f"feature {name!r} has no label template")
    text = feature.label.format(value=format_value(feature, value))
    return text[0].upper() + text[1:]


def _windows(f: Feature) -> str:
    if not f.windows:
        return "point in time"
    if f.trend and len(f.windows) == 2 and f.windows[0] == f.windows[1]:
        return f"last {f.windows[0]} d vs prior {f.windows[0]} d"
    if f.trend and len(f.windows) == 2:
        return f"last {f.windows[0]} d vs days {f.windows[0] + 1}-{f.windows[1]}"
    return ", ".join(f"{w} d" for w in f.windows)


def render_features_md() -> str:
    """The text of docs/features.md, generated from FEATURES."""
    lines = [
        "# Feature catalogue",
        "",
        "<!-- Generated by `python -m churn.features.registry` from src/churn/features/registry.py. Do not edit. -->",
        "",
        ("All features are computed per active subscriber for one `as_of` date by the SQL in `sql/features/`. "
        "A row is visible only if its event time is before `as_of` and it was loaded on or before `as_of` "
        "(no leakage, R2.3)."),
        "",
        ("Missing data (R2.4): counts default to 0; rates, deltas and recencies stay NULL; every domain except "
        "orders (always mapped by the contract) has a `has_<domain>` indicator. LightGBM reads NULLs natively, "
        "so no subscriber errors out."),
        "",
        ("Tenure truncation: windows of brand-new subscribers are truncated to their tenure. A recent window "
        "is `min(tenure, w)` days long and the prior window is `min(w, max(tenure - w, 0))` days long; "
        "trends are normalised by those lengths and are NULL when the prior window is empty. "
        "`email_click_slope_90` is truncated the same way, in whole weeks: n = min(13, floor(tenure_days / 7)), "
        "fitted over `[as_of - 7n days, as_of)` with week index w = 0..n-1 (oldest to newest), "
        "slope = SUM((w - (n-1)/2) * clicks_w) / (n * (n*n - 1) / 12); it is NULL when n < 2 or there are no "
        "email events in those n weeks."),
        "",
        "Leading indicators (ARCHITECTURE 6.2) are marked **leading**.",
        "",
    ]
    domains = list(dict.fromkeys(f.domain for f in FEATURES))
    for domain in domains:
        lines += [f"## {domain}", "", "| Feature | Windows | Description | Missing data | Kind |", "|---|---|---|---|---|"]
        for f in (x for x in FEATURES if x.domain == domain):
            kind = ("**leading**" if f.leading_indicator else "") + (" trend" if f.trend else "")
            lines.append(
                f"| `{f.name}` | {_windows(f)} | {f.description} | {f.missing} | {kind.strip()} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_features_md(path: Path = DOCS_PATH) -> None:
    path.write_text(render_features_md())


if __name__ == "__main__":
    write_features_md()
    print(f"wrote {DOCS_PATH}")
