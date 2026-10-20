"""Latent-state simulator: determinism and noisy archetype-driven cancellation reasons."""

from __future__ import annotations

import numpy as np
import pandas as pd

from churn.settings import Settings
from churn.synth import latent
from churn.synth.generator import build_frames


def _settings(seed: int) -> Settings:
    return Settings.load(overrides={"synth": {"seed": seed}})


def test_deterministic_for_seed():
    a = build_frames(_settings(11), 700)
    b = build_frames(_settings(11), 700)
    c = build_frames(_settings(12), 700)

    assert set(a) >= {
        "subscriptions", "orders", "subscription_events", "products",
        "email_events", "support_tickets", "quiz_responses", "customers_pii",
    }  # fmt: skip
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])
    assert not a["subscriptions"].equals(c["subscriptions"])


def test_reason_codes_follow_latent_archetype_with_noise():
    rng = np.random.default_rng(5)
    n = 60_000
    traits = latent.draw_traits(rng, n)
    _, voluntary, reason = latent.draw_cancellation(rng, traits, np.zeros(n))

    assert set(np.unique(traits.archetype)) == set(latent.ARCHETYPES)
    expected = np.array([latent.ARCHETYPE_REASON[a] for a in traits.archetype])
    match = (reason == expected)[voluntary].mean()
    # ~25% of voluntary reasons are noise (a different code, including OTHER); the rest follow the archetype.
    assert 0.72 <= match <= 0.78
    assert (reason[voluntary] == "OTHER").mean() > 0.03
    # Every archetype still shows mostly its own code.
    for arch in latent.ARCHETYPES:
        m = voluntary & (traits.archetype == arch)
        assert (reason[m] == latent.ARCHETYPE_REASON[arch]).mean() > 0.65


def test_involuntary_churn_comes_from_independent_payment_failure_process():
    rng = np.random.default_rng(6)
    n = 80_000
    days = latent.draw_involuntary_days(rng, n)
    assert days.shape == (n,)
    assert abs(days.mean() / 30.0 - 1.0 / latent.INVOLUNTARY_MONTHLY) / (1.0 / latent.INVOLUNTARY_MONTHLY) < 0.05

    traits = latent.draw_traits(rng, n)
    _, voluntary, _ = latent.draw_cancellation(rng, traits, np.zeros(n))
    assert 0.05 < (~voluntary).mean() < 0.4
