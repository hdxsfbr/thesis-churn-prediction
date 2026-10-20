"""R2.1: each of the five leading indicators against a hand-built fixture with a known value."""

from __future__ import annotations

import math

import pytest

from churn.warehouse.duckdb import DuckDBWarehouse
from fixtures import subscribers as fx


@pytest.fixture(scope="module")
def full(settings, tmp_path_factory):
    wh = DuckDBWarehouse(":memory:")
    fx.build_source().write(wh)
    contract = fx.write_contract(tmp_path_factory.mktemp("contract") / "c.yaml")
    frame = fx.feature_frame_by_customer(wh, settings, contract)
    yield frame.loc["c_full"]
    wh.close()


def test_email_open_rate_delta_30_90(full):
    # 30 days: 1 open / 4 sends; 90 days: 7 opens / 12 sends.
    assert full["email_open_rate_30"] == pytest.approx(1 / 4)
    assert full["email_open_rate_90"] == pytest.approx(7 / 12)
    assert full["email_open_rate_delta_30_90"] == pytest.approx(1 / 4 - 7 / 12)
    assert full["has_email"] == 1


def test_skip_rate_60_vs_prior_60(full):
    # skips / (skips + orders): last 60 days 4/(4+2), the 60 days before 1/(1+2).
    assert full["skips_60"] == 4
    assert full["skip_rate_60"] == pytest.approx(4 / 6)
    assert full["skip_rate_prior_60"] == pytest.approx(1 / 3)
    assert full["skip_rate_delta_60"] == pytest.approx(4 / 6 - 1 / 3)


def test_ticket_escalation_score(full):
    # escalated in 90d = 2; 30d tickets 2 vs a third of 90d tickets (4/3); sentiment 30d -0.3 vs 90d 0.1.
    assert full["tickets_30"] == 2
    assert full["tickets_90"] == 4
    assert full["escalated_90"] == 2
    assert full["sentiment_delta_30_90"] == pytest.approx(-0.4)
    assert full["ticket_escalation_score"] == pytest.approx(2 + (2 - 4 / 3) + 0.4)
    assert full["has_support"] == 1


def test_days_since_last_quiz(full):
    assert full["days_since_last_quiz"] == 40
    assert full["quiz_retake_count_365"] == 2
    assert full["quiz_sessions_90"] == 1
    assert full["has_quiz"] == 1


def test_blend_switch_count_90(full):
    # switches 30, 60 and 85 days ago count; the one 200 days ago only counts in the 365-day window.
    assert full["blend_switch_count_90"] == 3
    assert full["blend_switch_count_365"] == 4


def test_other_known_values(full):
    assert full["orders_total"] == 6
    assert full["orders_60"] == 2 and full["orders_prior_60"] == 2
    assert full["orders_90"] == 3 and full["orders_365"] == 6
    assert full["days_since_last_order"] == 20
    assert full["discount_share_365"] == pytest.approx(9 / 180)
    assert full["refund_count_365"] == 1
    assert full["paid_to_list_price_90"] == pytest.approx(28 / 30)
    assert full["pause_events_180"] == 1
    assert full["orders_ratio_90_365"] == pytest.approx(3 / 6)
    assert full["quiz_session_share_90_365"] == pytest.approx(1 / 3)
    assert not math.isnan(full["overdue_ratio"])
