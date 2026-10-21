"""T7 (R3.3): scoring writes archetype, confidence, probabilities and a low-confidence flag for at-risk rows."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

from churn.model.archetype import ARCHETYPES, MIN_CONF, MIN_MARGIN, classify
from churn.orchestration.runner import run_cycle
from churn.synth.generator import latent_archetypes

AS_OF = date(2025, 6, 30)
AT_RISK = ("medium", "high", "critical")

COLUMNS = (
    "s.subscriber_key, s.tier, s.archetype, s.archetype_conf, s.archetype_probs, s.archetype_low_conf, "
    "s.model_version"
)


@pytest.fixture(scope="module")
def scored(pipeline):
    wh = pipeline["wh"]
    run_cycle(wh, pipeline["settings"], AS_OF, run_id="t-archetype")
    return wh.query(
        f"SELECT {COLUMNS}, k.customer_id FROM CHURN_OPS.RISK_SCORES s "
        "JOIN CHURN_PRIVATE.KEY_MAP k ON k.subscriber_key = s.subscriber_key WHERE s.run_id = 't-archetype'"
    )


def test_every_at_risk_row_has_archetype_and_confidence(scored):
    at_risk = scored[scored["tier"].isin(AT_RISK)]
    assert len(at_risk) >= 5
    for _, row in at_risk.iterrows():
        assert row["archetype"] in ARCHETYPES
        probs = json.loads(row["archetype_probs"])
        assert set(probs) == set(ARCHETYPES)
        assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)
        assert row["archetype"] == max(probs, key=probs.get)
        assert row["archetype_conf"] == pytest.approx(max(probs.values()))
        assert 0.25 - 1e-9 <= row["archetype_conf"] <= 1.0
        top = sorted(probs.values(), reverse=True)
        expected_low = top[0] < MIN_CONF or top[0] - top[1] < MIN_MARGIN
        assert bool(row["archetype_low_conf"]) == expected_low
    assert (MIN_CONF, MIN_MARGIN) == (0.40, 0.10)


def test_low_confidence_flag_on_tied_probs():
    cls = list(ARCHETYPES)
    tied = classify(np.array([[0.25, 0.25, 0.25, 0.25]]), cls)
    assert tied.low_confidence.tolist() == [True]
    assert tied.confidence[0] == pytest.approx(0.25)

    clear = classify(np.array([[0.70, 0.10, 0.10, 0.10]]), cls)
    assert clear.low_confidence.tolist() == [False]
    assert clear.archetype.tolist() == [ARCHETYPES[0]]

    two_way_tie = classify(np.array([[0.45, 0.45, 0.05, 0.05]]), cls)  # confident, but no margin
    assert two_way_tie.low_confidence.tolist() == [True]

    diffuse = classify(np.array([[0.39, 0.20, 0.21, 0.20]]), cls)  # margin fine, confidence below min_conf
    assert diffuse.low_confidence.tolist() == [True]

    boundary = classify(np.array([[0.40, 0.30, 0.20, 0.10]]), cls)  # exactly min_conf and min_margin: not low
    assert boundary.low_confidence.tolist() == [False]
    assert json.loads(boundary.probs_json[0]) == pytest.approx(dict(zip(cls, [0.40, 0.30, 0.20, 0.10], strict=True)))


def test_archetype_beats_chance_on_latent_truth(pipeline, scored):
    truth = latent_archetypes(pipeline["settings"], "tiny")
    # The tiny population has only a handful of at-risk rows, so judge every scored subscriber.
    actual = truth.reindex(scored["customer_id"]).to_numpy()
    predicted = scored["archetype"].to_numpy()
    assert not any(a is None for a in actual)
    for archetype in ARCHETYPES:
        called = predicted == archetype
        assert called.sum() > 0, f"{archetype} is never predicted"
        precision = float((actual[called] == archetype).mean())
        assert precision > 0.25, f"{archetype}: precision {precision:.2f}"
