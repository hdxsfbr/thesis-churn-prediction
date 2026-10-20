# Feature catalogue

Generated from `src/churn/features/registry.py` by `python -m churn.features.registry`; do not edit by hand.
A test fails when this file differs from the registry.

All features are computed per active subscriber at a scoring date `:as_of` from rows with
the logical event timestamp (`event_ts`; `ordered_at`, `created_at` or `answered_at` in their views) before `:as_of` and `loaded_at <= :as_of` only (no leakage). Windows are `[as_of - N days, as_of)`;
a comparison such as 30 vs 90 uses disjoint windows (last 30 days against days 31 to 90).

Missing data: counts default to 0, rates and recencies stay NULL, and every domain has a
`has_<domain>` indicator. A domain whose source view is not mapped yields the same defaults, never an error.
Trend features that compare a recent and a prior window (orders, skips, blend switches, quiz retakes) divide each
window's count by its tenure-truncated length, `min(tenure, window)`, so a new subscriber is not shown a false rise;
they are NULL when the subscriber has no prior window yet. Each feature's missing policy is in its row.

Leading indicators (R2.1) are marked with a star in the table.

## subscription

Source view: `subscriptions`.

| Feature | Leading | Windows (days) | Kind | Missing policy | Description |
|---|---|---|---|---|---|
| `tenure_days` |  | - | point/single window | never missing | Days since the subscription started |
| `price_usd` |  | - | point/single window | NULL (price not recorded) | Subscription price per shipment |
| `interval_days` |  | - | point/single window | NULL (interval not recorded) | Days between shipments |

## orders

Source view: `orders`.

| Feature | Leading | Windows (days) | Kind | Missing policy | Description |
|---|---|---|---|---|---|
| `has_orders` |  | - | point/single window | 0 (no visible rows in this domain) | 1 if the subscriber has any order before as_of |
| `orders_total` |  | - | point/single window | 0 | Orders placed before as_of |
| `orders_90` |  | 90 | point/single window | 0 | Orders in the 90 days before as_of |
| `orders_365` |  | 365 | point/single window | 0 | Orders in the 365 days before as_of |
| `orders_trend_60` |  | 60, 120 | trend/slope | NULL (tenure of 60 days or less: no prior window) | Orders per 60 days in the last 60 days minus the rate in the 60 days before; each window is truncated to the subscriber's tenure (count / min(tenure, window) * 60) |
| `days_since_last_order` |  | - | point/single window | NULL | Days since the latest order |
| `overdue_ratio` |  | - | point/single window | NULL | Days since last order / shipment interval |
| `discount_share_365` |  | 365 | point/single window | NULL | Discount / gross order value, 365 days |
| `refund_count_365` |  | 365 | point/single window | 0 | Refunded orders in the 365 days |

## subscription_events

Source view: `subscription_events`.

| Feature | Leading | Windows (days) | Kind | Missing policy | Description |
|---|---|---|---|---|---|
| `has_subscription_events` |  | - | point/single window | 0 (no visible rows in this domain) | 1 if the subscriber has any shipment-modification event |
| `skip_rate_60_vs_prior_60` | ★ | 60, 120 | trend/slope | NULL (no shipments in either window, or no events) | Skips per shipment (skips + orders) in the last 60 days minus the same rate in the 60 days before |
| `skip_count_trend` | ★ | 60, 120 | trend/slope | NULL (tenure of 60 days or less: no prior window); 0 when there are no events | Skips per 60 days in the last 60 days minus the rate in the 60 days before; each window is truncated to the subscriber's tenure (count / min(tenure, window) * 60) |
| `blend_switch_count_90` | ★ | 90 | point/single window | 0 | Blend switches in the 90 days before as_of |
| `blend_switch_count_365` |  | 365 | point/single window | 0 | Blend switches in the 365 days before as_of |
| `blend_switch_rate_change` | ★ | 90, 365 | trend/slope | NULL (tenure of 90 days or less: no prior window); 0 when there are no events | Switches per 30 days in the last 90 days minus the rate in days 91-365, each window truncated to the subscriber's tenure (normalised by min(tenure, window)) |

## email

Source view: `email_events`.

| Feature | Leading | Windows (days) | Kind | Missing policy | Description |
|---|---|---|---|---|---|
| `has_email` |  | - | point/single window | 0 (no visible rows in this domain) | 1 if the subscriber has any email event before as_of |
| `email_open_rate_30` |  | 30 | point/single window | NULL (no sends) | Opens / sends over the last 30 days |
| `email_open_rate_delta_30_90` | ★ | 30, 90 | trend/slope | NULL (no sends in either window) | Open rate over the last 30 days minus the open rate over days 31 to 90 (disjoint windows) |
| `email_click_slope_90` | ★ | 91 | trend/slope | NULL (no email events in the window) | Least-squares slope of weekly clicks over 13 weeks (91 days) ending at as_of, in clicks per week |
| `email_unsubscribed` |  | - | point/single window | 0 | 1 if an unsubscribe event was seen before as_of |

## support

Source view: `support_tickets`.

| Feature | Leading | Windows (days) | Kind | Missing policy | Description |
|---|---|---|---|---|---|
| `has_support` |  | - | point/single window | 0 (no visible rows in this domain) | 1 if the subscriber has any ticket before as_of |
| `tickets_30` |  | 30 | point/single window | 0 | Support tickets in the last 30 days |
| `tickets_90` |  | 90 | point/single window | 0 | Support tickets in the last 90 days |
| `tickets_escalated_90` |  | 90 | point/single window | 0 | Escalated tickets in the last 90 days |
| `sentiment_delta_30_90` |  | 30, 90 | trend/slope | NULL (no tickets in a window) | Mean ticket sentiment over the last 30 days minus the mean over days 31 to 90 |
| `ticket_escalation_score` | ★ | 30, 90 | trend/slope | 0 | tickets_30 + 2 * tickets_escalated_90 + 3 * max(0, -sentiment_delta_30_90) (missing delta counts as 0) |

## quiz

Source view: `quiz_responses`.

| Feature | Leading | Windows (days) | Kind | Missing policy | Description |
|---|---|---|---|---|---|
| `has_quiz` |  | - | point/single window | 0 (no visible rows in this domain) | 1 if the subscriber has completed a quiz before as_of |
| `days_since_last_quiz` | ★ | - | point/single window | NULL (no quiz) | Days since the latest quiz completion (latest quiz session, dated at its last answer) |
| `quiz_retake_count_90` |  | 90 | point/single window | 0 | Quiz completions (distinct quiz sessions) after the first one, in the last 90 days |
| `quiz_retake_trend_90` |  | 90, 180 | trend/slope | NULL (tenure of 90 days or less: no prior window) | Quiz retakes per 90 days in the last 90 days minus the rate in days 91 to 180; each window is truncated to the subscriber's tenure (count / min(tenure, window) * 90) |
| `quiz_retake_count_365` |  | 365 | point/single window | 0 | Quiz completions (distinct quiz sessions) after the first one, in the last 365 days |
