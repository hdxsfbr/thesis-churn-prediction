"""R2.4: a cycle over a fixture with one subscriber per edge-case category scores every one of them."""

from __future__ import annotations

import pytest

from churn.features.registry import FEATURE_COLUMNS
from churn.orchestration.runner import run_cycle
from fixtures import subscribers as fx


def test_every_edge_case_subscriber_scored(pipeline, tmp_path):
    wh, base = pipeline["wh"], pipeline["settings"]
    fx.build_source().write(wh)
    fx.write_contract(tmp_path / "fixture_contract.yaml")
    settings = base.model_copy(update={"contract_path": str(tmp_path / "fixture_contract.yaml")})

    result = run_cycle(wh, settings, fx.AS_OF, run_id="t-edge-cases")

    assert result.status == "SUCCEEDED"
    assert result.scored == result.active_in_source == len(fx.ACTIVE_CUSTOMERS)
    scores = wh.query(
        "SELECT subscriber_key, churn_prob, top_factors FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-edge-cases'"
    ).set_index("subscriber_key")
    for category, cid in fx.EDGE_CASES.items():
        key = fx.subscriber_key(settings, cid)
        assert key in scores.index, f"{category} ({cid}) was not scored"
        assert 0.0 <= scores.loc[key, "churn_prob"] <= 1.0, category
    for cid in fx.INACTIVE_CUSTOMERS:
        assert fx.subscriber_key(settings, cid) not in scores.index


def test_missing_domains_give_indicators_and_nulls(settings, fresh_wh, tmp_path):
    from churn.contract.loader import load_contract  # noqa: F401  (contract written by the fixture)

    fx.build_source().write(fresh_wh)
    frame = fx.feature_frame_by_customer(fresh_wh, settings, fx.write_contract(tmp_path / "c.yaml"))

    bare = frame.loc["c_bare"]
    assert (bare["has_email"], bare["has_support"], bare["has_quiz"], bare["has_subscription_events"]) == (0, 0, 0, 0)
    for name in ("email_open_rate_delta_30_90", "days_since_last_quiz", "days_since_last_order"):
        assert bare[name] != bare[name], f"{name} should be NULL"  # NaN
    assert bare["orders_total"] == 0 and bare["blend_switch_count_90"] == 0 and bare["ticket_escalation_score"] == 0
    assert frame.loc["c_no_quiz"]["has_quiz"] == 0 and frame.loc["c_no_email"]["has_email"] == 0
    assert frame.loc["c_no_tickets"]["has_support"] == 0

    # late-arriving rows and the duplicated email event are handled by as_of and dedup
    late = frame.loc["c_late"]
    assert late["orders_total"] == 1
    assert late["has_email"] == 1 and late["email_open_rate_30"] == 0.0

    # brand-new subscriber: tenure-truncated windows
    new = frame.loc["c_new"]
    assert new["tenure_days"] == 10
    assert new["orders_per_30d_90"] == pytest.approx(1 * 30 / 10)
    assert set(FEATURE_COLUMNS) <= set(frame.columns)


@pytest.mark.parametrize("domains", [("orders",), ("email_events", "quiz_responses"), ()])
def test_contract_without_domain_views_never_errors(settings, fresh_wh, tmp_path, domains):
    fx.build_source().write(fresh_wh)
    contract = fx.write_contract(tmp_path / "c.yaml", domains=domains)
    frame = fx.feature_frame_by_customer(fresh_wh, settings, contract)
    assert len(frame.loc[fx.ACTIVE_CUSTOMERS]) == len(fx.ACTIVE_CUSTOMERS)
    full = frame.loc["c_full"]
    if "email_events" not in domains:
        assert full["has_email"] == 0 and full["email_open_rate_90"] != full["email_open_rate_90"]
    if "quiz_responses" not in domains:
        assert full["has_quiz"] == 0
    if "orders" not in domains:
        assert full["orders_total"] == 0
