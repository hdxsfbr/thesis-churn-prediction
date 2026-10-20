"""R2.4 / PRD §7: a run over one subscriber per edge-case category scores every one of them."""

from __future__ import annotations

import json
import math

import pandas as pd

from churn.contract.loader import contract_for
from churn.features.builder import build_features
from churn.features.registry import FEATURE_COLUMNS
from churn.model.score import score_frame
from fixtures import subscribers as fx


def test_every_edge_case_subscriber_scored(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    fx.load(wh, fx.edge_cases())
    contract = fx.fixture_contract(contract_for(settings))

    feats = build_features(wh, settings, contract, fx.AS_OF)
    expected = {fx.subscriber_key(cid, settings): cat for cat, cid in fx.EDGE.items()}
    assert set(feats["subscriber_key"]) == set(expected)

    scores = score_frame(wh, settings, feats, "t-edge", fx.AS_OF)
    assert set(scores["subscriber_key"]) == set(expected)
    for _, row in scores.iterrows():
        assert 0.0 <= row["churn_prob"] <= 1.0 and not math.isnan(row["churn_prob"]), expected[row["subscriber_key"]]
        assert len(json.loads(row["top_factors"])) >= 1, expected[row["subscriber_key"]]


def test_missing_domains_give_indicators_and_nulls(settings):
    from churn.warehouse.duckdb import DuckDBWarehouse

    wh = DuckDBWarehouse(":memory:")
    fx.load(wh, fx.edge_cases())
    f = build_features(wh, settings, fx.fixture_contract(contract_for(settings)), fx.AS_OF)
    f["cat"] = f["subscriber_key"].map({fx.subscriber_key(c, settings): k for k, c in fx.EDGE.items()})
    f = f.set_index("cat")
    assert f.loc["no_quiz", "has_quiz"] == 0 and pd.isna(f.loc["no_quiz", "days_since_last_quiz"])
    assert f.loc["no_email", "has_email"] == 0 and pd.isna(f.loc["no_email", "email_open_rate_delta_30_90"])
    assert f.loc["no_tickets", "has_support"] == 0 and f.loc["no_tickets", "tickets_90"] == 0
    assert f.loc["single_order", "orders_total"] == 1 and f.loc["single_order", "has_orders"] == 1
    assert f.loc["new", "has_orders"] == 0 and f.loc["new", "tenure_days"] == 3
    assert pd.isna(f.loc["new", "days_since_last_order"]) and f.loc["new", "orders_90"] == 0
    assert f.loc["partial", "has_email"] == 1 and pd.isna(f.loc["partial", "email_open_rate_delta_30_90"])
    assert pd.isna(f.loc["partial", "price_usd"]) and pd.isna(f.loc["partial", "interval_days"])
    assert pd.isna(f.loc["new", "skip_count_trend"]) and pd.isna(f.loc["new", "orders_trend_60"])

    # with no domain views in the contract at all, nothing errors: counts 0, rates NULL, indicators 0
    bare_contract = contract_for(settings).model_copy(update={"schema_name": fx.SCHEMA})
    bare = build_features(wh, settings, bare_contract, fx.AS_OF)
    assert len(bare) == len(f)
    for col in ("has_email", "has_support", "has_quiz", "has_subscription_events"):
        assert (bare[col] == 0).all()
    assert bare["email_open_rate_delta_30_90"].isna().all()
    assert (bare["tickets_90"] == 0).all()
    assert set(FEATURE_COLUMNS) <= set(bare.columns)
    wh.close()
