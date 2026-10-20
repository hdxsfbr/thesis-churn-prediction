"""Acceptance tests for T2: every domain has a populated contract view; the data profile proves the edge cases."""

from __future__ import annotations

import pytest

from churn.contract.loader import contract_for, validate
from churn.synth import edge_cases, profile
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations

# R1.1 domains -> contract views that carry them.
DOMAINS = {
    "subscriptions_and_orders": ["subscriptions", "orders", "subscription_events", "products"],
    "email": ["email_events"],
    "support": ["support_tickets"],
    "quiz": ["quiz_responses"],
    "pii": ["customers_pii"],
}


@pytest.fixture(scope="module")
def synth_wh(settings):
    wh = DuckDBWarehouse(":memory:")
    apply_migrations(wh, settings)
    generate(wh, settings, scale="tiny")
    yield wh
    wh.close()


def test_every_domain_has_view(synth_wh, settings):
    contract = contract_for(settings)
    validate(synth_wh, contract)
    for domain, views in DOMAINS.items():
        populated = []
        for v in views:
            assert v in contract.views, f"{domain}: view {v} missing from contract"
            n = int(synth_wh.query(f"SELECT COUNT(*) AS n FROM {contract.physical(v)}").iloc[0]["n"])
            if n > 0:
                populated.append(v)
        assert populated, f"domain {domain} has no populated view"
    assert len(contract.views) == 8


def test_edge_case_shares_above_zero(synth_wh, settings, tmp_path):
    p = profile.build_profile(synth_wh, settings)
    assert set(p.edge_cases) == set(edge_cases.EDGE_CATEGORIES)
    for name, share in p.edge_cases.items():
        assert share.count > 0 and share.share > 0, f"edge case {name} has share 0%"
    assert all(n > 0 for n in p.view_rows.values())

    doc = tmp_path / "data-profile.md"
    profile.write_profile(synth_wh, settings, p, doc)
    text = doc.read_text()
    for name in edge_cases.EDGE_CATEGORIES:
        assert name in text
    stored = synth_wh.query("SELECT section, name, rows, share FROM CHURN_OPS.DATA_PROFILE")
    assert set(stored[stored["section"] == "edge_case"]["name"]) == set(edge_cases.EDGE_CATEGORIES)
    assert (stored[stored["section"] == "edge_case"]["share"] > 0).all()
    assert set(stored[stored["section"] == "view_rows"]["name"]) == set(p.view_rows)

    # Rewriting is idempotent.
    profile.write_profile(synth_wh, settings, p, doc)
    again = synth_wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.DATA_PROFILE").iloc[0]["n"]
    assert again == len(stored)


def test_churn_events_span_label_and_eval_window(synth_wh, settings):
    p = profile.build_profile(synth_wh, settings)
    needed = settings.label.horizon_days + profile.HOLDOUT_DAYS
    assert p.voluntary_span_days >= needed
    assert p.voluntary_in_final_window > 0  # label horizon + 3-month holdout
    assert p.voluntary_before_final_window > 0  # training history
    assert p.involuntary_total > 0
