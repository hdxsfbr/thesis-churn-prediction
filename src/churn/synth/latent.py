"""Latent-state simulator.

Each customer carries a latent *archetype* (price sensitivity, efficacy dissatisfaction, subscription
fatigue, life change) and latent traits. Voluntary churn hazard is a function of the traits; involuntary
churn comes from an independent payment-failure process. The latent state *emits* observable events
(order cadence, discounts, refunds, skips, tickets, email engagement) and a noisy cancellation reason
code. The pipeline only ever sees the emitted events, through the source contract.
This module must not import churn.features or churn.model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BASE_MONTHLY_CHURN = 0.035
INVOLUNTARY_MONTHLY = 0.006  # independent payment-failure process, per month
REASON_NOISE = 0.25  # share of voluntary reason codes that do not follow the archetype
WIND_DOWN_DAYS = 75  # order cadence stretches this long before a voluntary cancellation
ARCHETYPES = ["price", "efficacy", "fatigue", "life_change"]
ARCHETYPE_SHARES = [0.35, 0.25, 0.25, 0.15]
ARCHETYPE_REASON = {
    "price": "TOO_EXPENSIVE",
    "efficacy": "NOT_WORKING",
    "fatigue": "TOO_MANY",
    "life_change": "LIFE_CHANGE",
}
REASONS = [ARCHETYPE_REASON[a] for a in ARCHETYPES] + ["OTHER"]
PRICES = np.array([29.0, 39.0, 49.0])
INTERVALS = np.array([30, 30, 30, 60])
SKUS = ["SLEEP", "FOCUS", "GUT", "ENERGY", "CALM", "SKIN"]


@dataclass
class Traits:
    archetype: np.ndarray  # str, one of ARCHETYPES
    price_sensitivity: np.ndarray
    efficacy_gap: np.ndarray
    fatigue: np.ndarray
    life_event: np.ndarray
    hazard_per_day: np.ndarray


def _trait(rng: np.random.Generator, high: np.ndarray) -> np.ndarray:
    return np.where(high, rng.beta(5, 2, len(high)), rng.beta(2, 5, len(high)))


def draw_traits(rng: np.random.Generator, n: int) -> Traits:
    archetype = rng.choice(ARCHETYPES, n, p=ARCHETYPE_SHARES)
    price = _trait(rng, archetype == "price")
    efficacy = _trait(rng, archetype == "efficacy")
    fatigue = _trait(rng, archetype == "fatigue")
    life = _trait(rng, archetype == "life_change")
    score = (
        1.6 * (price - price.mean())
        + 1.0 * (efficacy - efficacy.mean())
        + 1.2 * (fatigue - fatigue.mean())
        + 0.6 * (life - life.mean())
    )
    hazard = BASE_MONTHLY_CHURN / 30.0 * np.exp(1.5 * score)
    return Traits(archetype, price, efficacy, fatigue, life, hazard)


def draw_involuntary_days(rng: np.random.Generator, n: int) -> np.ndarray:
    """Days until a payment failure ends the subscription; independent of every trait."""
    return rng.exponential(30.0 / INVOLUNTARY_MONTHLY, n)


def draw_reasons(rng: np.random.Generator, archetype: np.ndarray) -> np.ndarray:
    """Reason code per customer: follows the archetype, except ``REASON_NOISE`` of the time.

    Noise is a uniformly drawn *different* code, so OTHER shows up as both archetype-free and noisy.
    """
    n = len(archetype)
    true_idx = np.array([ARCHETYPES.index(a) for a in np.unique(archetype)])
    lookup = dict(zip(np.unique(archetype), true_idx, strict=True))
    t = np.fromiter((lookup[a] for a in archetype), dtype=np.int64, count=n)
    noisy = rng.random(n) < REASON_NOISE
    shift = rng.integers(1, len(REASONS), n)
    idx = np.where(noisy, (t + shift) % len(REASONS), t)
    return np.array(REASONS)[idx]


def draw_cancellation(rng: np.random.Generator, traits: Traits, start_day: np.ndarray):
    """Days from start to cancellation, whether it is voluntary, and the reason code."""
    n = len(start_day)
    voluntary_days = rng.exponential(1.0 / traits.hazard_per_day)
    involuntary_days = draw_involuntary_days(rng, n)
    voluntary = voluntary_days <= involuntary_days
    reason = np.where(voluntary, draw_reasons(rng, traits.archetype), "PAYMENT_FAILED")
    return np.minimum(voluntary_days, involuntary_days), voluntary, reason


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
