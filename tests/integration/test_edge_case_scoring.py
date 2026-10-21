"""R2.4: one subscriber per edge-case category is featurised and scored; nothing errors, nothing is dropped."""

from __future__ import annotations

import numpy as np

from churn.features.builder import build_features, count_active
from churn.model.score import score_frame
from fixtures import subscribers as fx


def test_every_edge_case_subscriber_scored(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    contract = fx.load(wh)

    features = build_features(wh, settings, contract, fx.AS_OF)
    assert count_active(wh, settings, contract, fx.AS_OF) == len(fx.SUBS)
    scores = score_frame(wh, settings, features, "t-edge", fx.AS_OF)

    assert set(features["subscription_id"]) == set(fx.SUBS.values())
    assert len(scores) == len(fx.SUBS)
    assert scores["subscriber_key"].is_unique
    assert scores["churn_prob"].between(0, 1).all()
    assert not np.isnan(scores["churn_prob"].to_numpy()).any()

    by_sub = features.set_index("subscription_id")
    for name in fx.EDGE_CASES:  # every category is really present in the fixture
        assert fx.SUBS[name] in by_sub.index
    assert by_sub.loc[fx.SUBS["no_quiz"], "has_quiz"] == 0
    assert by_sub.loc[fx.SUBS["no_email"], "has_email"] == 0
    assert by_sub.loc[fx.SUBS["no_tickets"], "has_support"] == 0
    assert by_sub.loc[fx.SUBS["single_order"], "orders_total"] == 1
    assert by_sub.loc[fx.SUBS["new"], "tenure_days"] == 10
