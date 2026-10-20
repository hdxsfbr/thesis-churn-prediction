"""Latent-state simulator (thin version).

Churn hazard is a *latent* variable that emits observable behaviour (longer gaps between orders as
cancellation nears, discounts, refunds). The pipeline only ever sees the emitted events, through the
source contract. This module must not import churn.features or churn.model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BASE_MONTHLY_CHURN = 0.035
INVOLUNTARY_SHARE = 0.15
WIND_DOWN_DAYS = 75  # order cadence stretches this long before a voluntary cancellation
REASONS = ["TOO_EXPENSIVE", "NOT_WORKING", "TOO_MANY", "LIFE_CHANGE", "OTHER"]
PRICES = np.array([29.0, 39.0, 49.0])
INTERVALS = np.array([30, 30, 30, 60])
SKUS = ["SLEEP", "FOCUS", "GUT", "ENERGY", "CALM", "SKIN"]


@dataclass
class Traits:
    price_sensitivity: np.ndarray
    fatigue: np.ndarray
    hazard_per_day: np.ndarray


def draw_traits(rng: np.random.Generator, n: int) -> Traits:
    price = rng.beta(2, 5, n)
    fatigue = rng.beta(2, 5, n)
    score = 1.6 * (price - price.mean()) + 1.2 * (fatigue - fatigue.mean())
    hazard = BASE_MONTHLY_CHURN / 30.0 * np.exp(2.0 * score)
    return Traits(price, fatigue, hazard)


def draw_cancellation(rng: np.random.Generator, traits: Traits, start_day: np.ndarray):
    """Latent days-to-cancel after start, whether it is voluntary, and the reason code."""
    n = len(start_day)
    days_to_cancel = rng.exponential(1.0 / traits.hazard_per_day)
    voluntary = rng.random(n) >= INVOLUNTARY_SHARE
    reason = rng.choice(REASONS, n, p=[0.35, 0.25, 0.15, 0.1, 0.15])
    return days_to_cancel, voluntary, reason


def order_times(
    rng: np.random.Generator,
    start_day: np.ndarray,
    interval: np.ndarray,
    cancel_day: np.ndarray,
    voluntary: np.ndarray,
    end_day: float,
    max_orders: int = 40,
):
    """Return (customer_index, day) arrays of order events. Days are floats since epoch day 0."""
    n = len(start_day)
    t = start_day.astype(float).copy()
    alive = np.ones(n, dtype=bool)
    idx_out, day_out = [], []
    for _ in range(max_orders):
        stop = np.minimum(cancel_day, end_day)
        alive &= t < stop
        if not alive.any():
            break
        i = np.flatnonzero(alive)
        idx_out.append(i)
        day_out.append(t[i].copy())
        noise = rng.lognormal(0.0, 0.08, len(i))
        winding_down = voluntary[i] & (cancel_day[i] - t[i] < WIND_DOWN_DAYS)
        stretch = np.where(winding_down, 1.0 + rng.uniform(0.6, 1.6, len(i)), 1.0)
        t[i] = t[i] + interval[i] * noise * stretch
    return np.concatenate(idx_out), np.concatenate(day_out)
