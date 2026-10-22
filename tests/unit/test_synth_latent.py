import numpy as np
import pandas as pd

from churn.synth import latent
from churn.synth.generator import build_frames, generate, latent_archetypes
from churn.warehouse.migrate import apply_migrations


def test_deterministic_for_seed(settings):
    a, b = build_frames(settings, 1500), build_frames(settings, 1500)
    assert isinstance(a, dict)
    assert len(a) == 9
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])


def test_reason_codes_follow_latent_archetype_with_noise(settings):
    archetypes = latent.draw_archetypes(settings.synth.seed, 50000)
    reasons = latent.reasons_for(settings.synth.seed, archetypes)
    truth = np.array([latent.ARCHETYPE_REASON[a] for a in archetypes])
    # 25% random redraws, including the correct code: 20% observable disagreement.
    assert 0.18 < (reasons != truth).mean() < 0.22
    frames = build_frames(settings, 1500)
    assert isinstance(frames, dict)
    tickets = frames['support_tickets'].merge(frames['latent_truth'], on='customer_id')
    expected = tickets.archetype.map({a: latent.EMISSIONS[a]['category'] for a in latent.ARCHETYPES})
    assert 0.10 < (tickets.category != expected).mean() < 0.35


def test_latent_truth_table_matches_latent_archetypes(settings, fresh_wh):
    apply_migrations(fresh_wh, settings)
    generate(fresh_wh, settings)
    truth = fresh_wh.query('SELECT customer_id, archetype FROM THESIS_SYNTH.latent_truth ORDER BY customer_id')
    actual = truth.set_index('customer_id').archetype
    pd.testing.assert_series_equal(actual, latent_archetypes(settings), check_names=False)
    expected = np.random.default_rng([settings.synth.seed, 4242]).choice(
        latent.ARCHETYPES, len(actual), p=latent.ARCHETYPE_SHARES)
    assert actual.tolist() == expected.tolist()
