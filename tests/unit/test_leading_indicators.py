"""R2.1: the five leading indicators (and the catalogue features ARCHITECTURE §6.2 names), on hand-built data."""

from __future__ import annotations

import pandas as pd
import pytest

from churn.contract.loader import contract_for
from churn.features.builder import build_features
from churn.warehouse.duckdb import DuckDBWarehouse
from fixtures import subscribers as fx


@pytest.fixture(scope="module")
def feats(settings) -> pd.DataFrame:
    wh = DuckDBWarehouse(":memory:")
    fx.load(wh, fx.known_values())
    contract = fx.fixture_contract(contract_for(settings))
    frame = build_features(wh, settings, contract, fx.AS_OF)
    wh.close()
    keys = {fx.subscriber_key(c, settings): c for c in ("alpha", "beta")}
    frame["customer"] = frame["subscriber_key"].map(keys)
    return frame.set_index("customer")


def test_email_open_rate_delta_30_90(feats):
    # §6.2: open rate over the last 30 days (3/10) minus the open rate over days 31-90 (4/20), disjoint windows.
    assert feats.loc["alpha", "email_open_rate_delta_30_90"] == pytest.approx(0.3 - 0.2)
    # an overlapping 30-vs-90 definition would give 0.3 - 7/30; make sure that is not what we compute
    assert feats.loc["alpha", "email_open_rate_delta_30_90"] != pytest.approx(0.3 - 7 / 30)
    assert pd.isna(feats.loc["beta", "email_open_rate_delta_30_90"])  # no email at all: NULL, not an error


def test_email_click_slope_90(feats):
    # 13 weekly buckets, clicks 1 / 0 / 2 (middle) / 3: slope = sum((week-6) * clicks) / sum((week-6)^2)
    assert feats.loc["alpha", "email_click_slope_90"] == pytest.approx((3 * 6 - 1 * 6) / 182)
    assert pd.isna(feats.loc["beta", "email_click_slope_90"])


def test_skip_rate_60_vs_prior_60(feats):
    # recent 60 days: 1 skip + 2 orders -> 1/3; prior 60 days: 2 skips + 3 orders -> 2/5
    assert feats.loc["alpha", "skip_rate_60_vs_prior_60"] == pytest.approx(1 / 3 - 2 / 5)
    assert feats.loc["beta", "skip_rate_60_vs_prior_60"] == pytest.approx(3 / 4 - 1 / 4)


def test_skip_count_trend(feats):
    assert feats.loc["alpha", "skip_count_trend"] == 1 - 2
    assert feats.loc["beta", "skip_count_trend"] == 3 - 1


def test_ticket_escalation_score(feats):
    row = feats.loc["alpha"]
    assert row["tickets_30"] == 2 and row["tickets_90"] == 5 and row["tickets_escalated_90"] == 2
    assert row["sentiment_delta_30_90"] == pytest.approx(-0.4 - 0.2)
    # tickets_30 + 2 * escalated_90 + 3 * max(0, -sentiment_delta)
    assert row["ticket_escalation_score"] == pytest.approx(2 + 2 * 2 + 3 * 0.6)
    assert feats.loc["beta", "ticket_escalation_score"] == 0  # no tickets: a count-style score is 0
    assert pd.isna(feats.loc["beta", "sentiment_delta_30_90"])


def test_days_since_last_quiz(feats):
    row = feats.loc["alpha"]
    assert row["days_since_last_quiz"] == 20
    assert row["has_quiz"] == 1
    assert row["quiz_retake_count_365"] == 2 and row["quiz_retake_count_90"] == 1
    assert pd.isna(feats.loc["beta", "days_since_last_quiz"])
    assert feats.loc["beta", "has_quiz"] == 0 and feats.loc["beta", "quiz_retake_count_365"] == 0


def test_blend_switch_count_90(feats):
    assert feats.loc["alpha", "blend_switch_count_90"] == 3
    assert feats.loc["alpha", "blend_switch_count_365"] == 5
    assert feats.loc["beta", "blend_switch_count_90"] == 0


@pytest.fixture(scope="module")
def gamma(settings) -> pd.Series:
    wh = DuckDBWarehouse(":memory:")
    fx.load(wh, fx.short_tenure())
    frame = build_features(wh, settings, fx.fixture_contract(contract_for(settings)), fx.AS_OF)
    wh.close()
    assert len(frame) == 1
    return frame.iloc[0]


def test_tenure_truncated_windows(gamma):
    # 100 days of tenure: recent 90 days are fully covered, the prior 275-day window covers only 10 days
    assert gamma["tenure_days"] == 100
    assert gamma["blend_switch_rate_change"] == pytest.approx(2 / 3 - 1 / (10 / 30))
    # skips per 60 days: 1 over 60 covered days vs 2 over 40 covered days (a raw difference would say -1)
    assert gamma["skip_count_trend"] == pytest.approx(1 - 2 * 60 / 40)
    assert gamma["orders_trend_60"] == pytest.approx(2 - 1 * 60 / 40)
    # retakes per 90 days: 2 over 90 days vs 1 over the 10 covered days
    assert gamma["quiz_retake_trend_90"] == pytest.approx(2 - 1 * 90 / 10)
    assert gamma["quiz_retake_count_90"] == 2


def test_no_prior_window_gives_null_trend(settings):
    # a subscriber of 60 days or less has no prior 60-day window: NULL, not a false rise
    s = fx.Source()
    s.subscriber("young", 50)
    s.order("young", 10)
    s.sub_event("young", "skip", 5)
    wh = DuckDBWarehouse(":memory:")
    fx.load(wh, s)
    row = build_features(wh, settings, fx.fixture_contract(contract_for(settings)), fx.AS_OF).iloc[0]
    wh.close()
    assert pd.isna(row["orders_trend_60"]) and pd.isna(row["skip_count_trend"])
    assert pd.isna(row["blend_switch_rate_change"]) and pd.isna(row["quiz_retake_trend_90"])


def test_quiz_completion_is_per_session(feats):
    # three answers per session, still one completion each: days_since_last_quiz uses the latest session
    assert feats.loc["alpha", "days_since_last_quiz"] == 20


def test_blend_switch_rate_change(feats):
    # switches per 30 days: 3 in the last 90 days vs 2 in the 275 days before that (tenure 400 > 365)
    assert feats.loc["alpha", "blend_switch_rate_change"] == pytest.approx(3 / 3 - 2 / (275 / 30))
    # beta has events (skips) but no switches: both rates are 0
    assert feats.loc["beta", "blend_switch_rate_change"] == 0
