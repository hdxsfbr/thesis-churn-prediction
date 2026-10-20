"""Known-value tests for the leading indicators (ARCHITECTURE §6.2) on the hand-built fixture."""

from __future__ import annotations

import math

import pytest

from churn.features.builder import build_features
from churn.warehouse.duckdb import DuckDBWarehouse
from fixtures import subscribers as fx


@pytest.fixture(scope="module")
def feats(settings):
    wh = DuckDBWarehouse(":memory:")
    contract = fx.load(wh)
    df = build_features(wh, settings, contract, fx.AS_OF).set_index("subscription_id")
    yield df
    wh.close()


def val(feats, name: str, col: str):
    return feats.loc[fx.SUBS[name], col]


def test_email_open_rate_delta_30_90(feats):
    # last 30 days: 3 opens / 4 sends = 0.75; days 31-90: 4 opens / 10 sends = 0.40 (disjoint windows)
    assert val(feats, "alpha", "email_open_rate_delta_30_90") == pytest.approx(0.75 - 0.40)


def test_open_rate_delta_null_when_a_window_has_no_sends(feats):
    assert math.isnan(val(feats, "short_tenure", "email_open_rate_delta_30_90"))  # nothing in days 31-90
    assert math.isnan(val(feats, "no_email", "email_open_rate_delta_30_90"))


def test_email_click_slope_90_full_window(feats):
    # n = 13. Clicks: 2 in week 12 (age 3d), 1 in week 10 (age 20d), 4 in week 1 (age 80d).
    expected = (2 * (12 - 6) + 1 * (10 - 6) + 4 * (1 - 6)) / 182
    assert val(feats, "alpha", "email_click_slope_90") == pytest.approx(expected)


def test_email_click_slope_90_truncated_to_tenure(feats):
    # tenure 30 days -> n = 4 whole weeks: clicks 2 in week 3, 1 in week 2, 3 in week 0.
    expected = (2 * (3 - 1.5) + 1 * (2 - 1.5) + 3 * (0 - 1.5)) / (4 * (4 * 4 - 1) / 12)
    got = val(feats, "short_tenure", "email_click_slope_90")
    assert expected == pytest.approx(-0.2)
    assert got == pytest.approx(expected)
    # the same clicks fitted over 13 weeks would give a different value
    thirteen = (2 * (12 - 6) + 1 * (11 - 6) + 3 * (9 - 6)) / 182
    assert abs(got - thirteen) > 0.1


def test_email_click_slope_90_null_when_tenure_under_14_days(feats):
    # tenure 10 days -> n = 1 < 2, even though the subscriber clicked
    assert math.isnan(val(feats, "new", "email_click_slope_90"))


def test_email_click_slope_90_null_without_email_events(feats):
    assert math.isnan(val(feats, "no_email", "email_click_slope_90"))
    assert math.isnan(val(feats, "no_data", "email_click_slope_90"))


def test_skip_rate_60_vs_prior_60(feats):
    # skips / (skips + orders): recent 3 / (3 + 2) = 0.6; prior 1 / (1 + 3) = 0.25
    assert val(feats, "alpha", "skip_rate_60_vs_prior_60") == pytest.approx(0.6 - 0.25)


def test_skip_count_trend(feats):
    # skips per 60 days: 3 recent vs 1 prior
    assert val(feats, "alpha", "skip_count_trend") == pytest.approx(2.0)
    assert math.isnan(val(feats, "short_tenure", "skip_count_trend"))  # no prior 60-day window yet


def test_ticket_escalation_score(feats):
    # tickets_30 = 2, escalated_90 = 2, sentiment delta = avg(-0.5, -0.1) - avg(0.2, 0.4, 0.0) = -0.5
    # score = tickets_30 + 2 * escalated_90 - sentiment_delta = 2 + 4 + 0.5
    assert val(feats, "alpha", "tickets_30") == 2
    assert val(feats, "alpha", "tickets_90") == 5
    assert val(feats, "alpha", "escalated_tickets_90") == 2
    assert val(feats, "alpha", "ticket_sentiment_delta_30_90") == pytest.approx(-0.5)
    assert val(feats, "alpha", "ticket_escalation_score") == pytest.approx(6.5)
    assert val(feats, "no_tickets", "ticket_escalation_score") == 0


def test_days_since_last_quiz(feats):
    assert val(feats, "alpha", "days_since_last_quiz") == 40
    assert val(feats, "alpha", "has_quiz") == 1
    assert val(feats, "alpha", "quiz_retake_count_365") == 1
    assert math.isnan(val(feats, "no_quiz", "days_since_last_quiz"))
    assert val(feats, "no_quiz", "has_quiz") == 0
    assert val(feats, "no_quiz", "quiz_retake_count_365") == 0


def test_blend_switch_count_90(feats):
    assert val(feats, "alpha", "blend_switch_count_90") == 2
    assert val(feats, "alpha", "blend_switch_count_365") == 4
    assert val(feats, "no_data", "blend_switch_count_90") == 0


def test_blend_switch_rate_change(feats):
    # switches per 90 days: 2 recent vs 1 prior
    assert val(feats, "alpha", "blend_switch_rate_change") == pytest.approx(1.0)
    assert math.isnan(val(feats, "short_tenure", "blend_switch_rate_change"))


def test_default_contract_without_domain_views_gives_nulls_not_errors(settings):
    """The shipped contract has no email/support/quiz/event views: the builder must still produce rows."""
    wh = DuckDBWarehouse(":memory:")
    contract = fx.load(wh)
    partial = contract.model_copy(update={"views": {k: contract.views[k] for k in ("subscriptions", "orders")}})
    df = build_features(wh, settings, partial, fx.AS_OF).set_index("subscription_id")
    assert val(df, "alpha", "has_email") == 0
    assert math.isnan(val(df, "alpha", "email_open_rate_delta_30_90"))
    assert val(df, "alpha", "blend_switch_count_90") == 0
    wh.close()
