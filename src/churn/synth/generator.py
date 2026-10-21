"""Synthetic subscriptions/orders written into THESIS_SYNTH through the warehouse protocol.

Deterministic for a given ``synth.seed``. Imports only warehouse/settings (and its own latent module).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from churn.settings import Settings
from churn.synth import latent
from churn.warehouse.base import Warehouse

CHUNK = 500_000


def _ts(start: datetime, days: np.ndarray) -> pd.Series:
    secs = np.round(np.asarray(days, dtype=float) * 86400).astype("int64")
    return pd.Series(pd.Timestamp(start) + pd.to_timedelta(secs, unit="s")).astype("datetime64[us]")


def build_frames(settings: Settings, n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = settings.synth
    rng = np.random.default_rng(cfg.seed)
    end = datetime.combine(cfg.end_date, time(0))
    hist_start = end - timedelta(days=30 * cfg.history_months)
    total_days = float((end - hist_start).days)

    traits = latent.draw_traits(rng, n)
    start_day = rng.uniform(0, total_days - 14, n)
    interval = rng.choice(latent.INTERVALS, n)
    price = rng.choice(latent.PRICES, n)
    to_cancel, voluntary, _ = latent.draw_cancellation(rng, traits, start_day)
    archetype = latent.draw_archetypes(cfg.seed, n)
    reason = latent.reasons_for(cfg.seed, archetype)
    cancel_day = start_day + to_cancel
    cancelled = cancel_day <= total_days

    status = rng.choice(["ACTIVE", "PAUSED", "PAST_DUE"], n, p=[0.92, 0.05, 0.03])
    status = np.where(cancelled, "CANCELLED", status)
    cancel_type = np.where(cancelled, np.where(voluntary, "voluntary", "involuntary"), None)
    reason_code = np.where(cancelled, np.where(voluntary, reason, "PAYMENT_FAILED"), None)

    ids = np.arange(n)
    started = _ts(hist_start, start_day)
    cancelled_at = _ts(hist_start, cancel_day).where(cancelled, pd.NaT)
    subs = pd.DataFrame(
        {
            "customer_id": [f"c{i:07d}" for i in ids],
            "subscription_id": [f"s{i:07d}" for i in ids],
            "status": status,
            "started_at": started,
            "cancelled_at": cancelled_at,
            "cancel_type": cancel_type,
            "cancel_reason_code": reason_code,
            "price_usd": price,
            "interval_days": interval.astype("int64"),
            "loaded_at": started,
        }
    )

    stretch = latent.by_archetype(latent.STRETCH_SCALE, archetype, 1.0)
    idx, day = latent.order_times(
        rng, start_day, interval, cancel_day, voluntary, total_days + 1, stretch_scale=stretch
    )
    m = len(idx)
    amount = price[idx]
    discount_boost = latent.by_archetype(latent.DISCOUNT_BOOST, archetype)
    refund_boost = latent.by_archetype(latent.REFUND_BOOST, archetype)
    discounted = rng.random(m) < 0.05 + 0.7 * traits.price_sensitivity[idx] + discount_boost[idx]
    discount = np.where(discounted, amount * rng.uniform(0.1, 0.3, m), 0.0).round(2)
    refunded = rng.random(m) < 0.01 + 0.15 * traits.fatigue[idx] + refund_boost[idx]
    refund_day = day + 5
    refunded &= refund_day <= total_days
    ordered_at = _ts(hist_start, day)
    orders = pd.DataFrame(
        {
            "order_id": [f"o{i:09d}" for i in range(m)],
            "customer_id": [f"c{i:07d}" for i in idx],
            "ordered_at": ordered_at,
            "blend_sku": rng.choice(latent.SKUS, m),
            "amount_usd": amount,
            "discount_usd": discount,
            "refund_usd": np.where(refunded, amount - discount, 0.0).round(2),
            "refunded_at": _ts(hist_start, refund_day).where(refunded, pd.NaT),
            "loaded_at": ordered_at,
        }
    )
    return subs, orders


def latent_archetypes(settings: Settings, scale: str = "tiny") -> pd.Series:
    """The generator's latent archetype per customer_id: ground truth for judging the classifier, never a feature."""
    n = settings.synth.scales[scale]
    return pd.Series(latent.draw_archetypes(settings.synth.seed, n), index=[f"c{i:07d}" for i in range(n)])


def generate(wh: Warehouse, settings: Settings, scale: str = "tiny") -> dict[str, int]:
    """Replace the contents of THESIS_SYNTH.subscriptions/orders with a fresh synthetic population."""
    try:
        n = settings.synth.scales[scale]
    except KeyError:
        raise ValueError(f"unknown scale {scale!r}; choose one of {sorted(settings.synth.scales)}") from None
    subs, orders = build_frames(settings, n)
    schema = settings.schemas.synth
    with wh.transaction():
        wh.execute(f"DELETE FROM {schema}.subscriptions; DELETE FROM {schema}.orders")
        wh.insert_frame(f"{schema}.subscriptions", subs)
        for lo in range(0, len(orders), CHUNK):
            wh.insert_frame(f"{schema}.orders", orders.iloc[lo : lo + CHUNK])
    return {"subscriptions": len(subs), "orders": len(orders)}
