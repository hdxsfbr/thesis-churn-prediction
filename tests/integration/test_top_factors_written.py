"""Acceptance tests for T6: every RISK_SCORES row carries named, human-readable top factors."""

from __future__ import annotations

import json
from datetime import date

from churn.contract.loader import contract_for
from churn.features.builder import as_of_ts, build_features
from churn.features.registry import FEATURE_COLUMNS
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)


def _scores(pipeline, run_id):
    run_cycle(pipeline["wh"], pipeline["settings"], AS_OF, run_id=run_id)
    return pipeline["wh"].query(
        "SELECT subscriber_key, top_factors FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r", {"r": run_id}
    )


def test_every_score_row_has_at_least_3_named_factors(pipeline):
    rows = _scores(pipeline, "t-factors-1")
    assert len(rows) > 100
    contract = contract_for(pipeline["settings"])
    feats = build_features(pipeline["wh"], pipeline["settings"], contract, as_of_ts(AS_OF))
    present = feats.set_index("subscriber_key")[list(FEATURE_COLUMNS)].notna().sum(axis=1)
    for key, raw in zip(rows["subscriber_key"], rows["top_factors"]):
        factors = json.loads(raw)
        assert min(3, int(present.loc[key])) <= len(factors) <= 5
        for f in factors:
            assert f["feature"] in FEATURE_COLUMNS
            assert f["label"].strip()
            assert f["direction"] in {"up", "down", "neutral"}
            assert isinstance(f["contribution"], float)
        mags = [abs(f["contribution"]) for f in factors]
        assert mags == sorted(mags, reverse=True)


def test_factor_labels_human_readable(pipeline):
    rows = _scores(pipeline, "t-factors-2")
    for raw in rows["top_factors"]:
        for f in json.loads(raw):
            assert "_" not in f["label"], f["label"]
            assert not any(name in f["label"] for name in FEATURE_COLUMNS)
            assert not f["label"][0].islower()
            assert "{" not in f["label"] and "nan" not in f["label"].lower()
