"""Edge-case injection for the synthetic source (R1.2).

Everything here is applied to *emitted* data (or to who gets which domain at all); none of it touches the
latent state. Rates are module constants so the data profile can be checked against them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Categories reported by the data profile; each must show a share above 0%.
EDGE_CATEGORIES = (
    "no_quiz",
    "no_email",
    "no_tickets",
    "single_order",
    "tenure_lt_30d",
    "null_values",
    "duplicates",
    "late_arriving",
    "tz_offsets",
)

NO_QUIZ = 0.15
NO_EMAIL = 0.05
SINGLE_ORDER = 0.03
RECENT_START = 0.04  # tenure < 30 days at the end of the history
DUPLICATE_RATE = 0.005
LATE_RATE = 0.01
LATE_DAYS = (2.0, 20.0)
TZ_RATE = 0.05
TZ_OFFSETS_MIN = np.array([-480, -300, 60, 120, 330])
NULL_RATES = {
    ("orders", "blend_sku"): 0.01,
    ("support_tickets", "sentiment"): 0.03,
    ("customers_pii", "phone"): 0.20,
    ("email_events", "campaign_id"): 0.02,
}
CANCEL_REASON_NULL = 0.02  # voluntary cancellations that arrive without a reason code

# view -> (event timestamp column) for rows that can be duplicated or arrive late.
EVENT_TS = {
    "subscriptions": "started_at",
    "orders": "ordered_at",
    "subscription_events": "event_ts",
    "email_events": "event_ts",
    "support_tickets": "created_at",
    "quiz_responses": "answered_at",
}


def coverage(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Which customers have any quiz / email history at all."""
    return rng.random(n) >= NO_QUIZ, rng.random(n) >= NO_EMAIL


def recent_starts(rng: np.random.Generator, start_day: np.ndarray, total_days: float) -> np.ndarray:
    """Make ``RECENT_START`` of customers start within the last 30 days (tenure < 30 d)."""
    pick = rng.random(len(start_day)) < RECENT_START
    return np.where(pick, rng.uniform(total_days - 29.0, total_days - 1.0, len(start_day)), start_day)


def single_order_mask(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.random(n) < SINGLE_ORDER


def keep_orders(idx: np.ndarray, single: np.ndarray) -> np.ndarray:
    """Rows to keep: everything, except orders after the first for customers flagged ``single``."""
    _, first = np.unique(idx, return_index=True)
    is_first = np.zeros(len(idx), dtype=bool)
    is_first[first] = True
    return ~single[idx] | is_first


def apply_nulls(rng: np.random.Generator, frames: dict[str, pd.DataFrame]) -> None:
    for (view, col), rate in NULL_RATES.items():
        df = frames[view]
        mask = rng.random(len(df)) < rate
        df[col] = df[col].astype(object).where(~mask, None)
    subs = frames["subscriptions"]
    drop = (subs["cancel_type"] == "voluntary") & (rng.random(len(subs)) < CANCEL_REASON_NULL)
    subs["cancel_reason_code"] = subs["cancel_reason_code"].astype(object).where(~drop, None)


def add_duplicates(rng: np.random.Generator, frames: dict[str, pd.DataFrame]) -> None:
    """Exact repeat deliveries of ~0.5% of event rows (the contract CTEs dedup on the view key)."""
    for view in EVENT_TS:
        df = frames[view]
        dup = df[rng.random(len(df)) < DUPLICATE_RATE]
        frames[view] = pd.concat([df, dup], ignore_index=True)


def late_arrivals(rng: np.random.Generator, frames: dict[str, pd.DataFrame]) -> None:
    """~1% of rows are loaded days after their event timestamp."""
    for view in EVENT_TS:
        df = frames[view]
        late = rng.random(len(df)) < LATE_RATE
        delay = pd.to_timedelta(np.round(rng.uniform(*LATE_DAYS, len(df)) * 86400), unit="s").astype(
            "timedelta64[us]"
        )
        df["loaded_at"] = df["loaded_at"].where(~late, df["loaded_at"] + delay)


def tz_offsets(rng: np.random.Generator, frames: dict[str, pd.DataFrame]) -> None:
    """Some email/support timestamps are local wall-clock time; ``tz_offset_min`` says by how much."""
    for view, col in (("email_events", "event_ts"), ("support_tickets", "created_at")):
        df = frames[view]
        pick = rng.random(len(df)) < TZ_RATE
        off = np.where(pick, rng.choice(TZ_OFFSETS_MIN, len(df)), 0).astype("int64")
        df["tz_offset_min"] = off
        shift = pd.to_timedelta(off, unit="m").astype("timedelta64[us]")
        df[col] = df[col] + shift
        if view == "support_tickets":
            df["resolved_at"] = df["resolved_at"] + shift
