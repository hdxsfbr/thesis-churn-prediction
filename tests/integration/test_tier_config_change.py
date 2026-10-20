"""Acceptance tests for T5: tiers come from AGENT_CONFIG and the snapshot is pinned per run."""

from __future__ import annotations

import json
from datetime import date

import pytest

from churn import runtime_config as rc
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)
EARLIER = date(2025, 5, 31)


def set_tiers(wh, medium, high, critical):
    value = {"boundaries": {"medium": medium, "high": high, "critical": critical}, "at_risk_from": "medium"}
    wh.execute(
        "UPDATE CHURN_OPS.AGENT_CONFIG SET value_json = :v WHERE key = 'tiers'", {"v": json.dumps(value)}
    )


def tier_counts(wh, run_id):
    df = wh.query(
        "SELECT tier, COUNT(*) AS n FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r GROUP BY tier", {"r": run_id}
    )
    return dict(zip(df["tier"], df["n"], strict=True))


@pytest.fixture()
def seeded(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    rc.seed(wh, settings, overwrite=True)
    yield wh, settings
    rc.seed(wh, settings, overwrite=True)


def test_changing_boundary_changes_tier_next_run(seeded):
    wh, settings = seeded
    run_cycle(wh, settings, AS_OF, run_id="t-tier-before")
    before = wh.query(
        "SELECT subscriber_key, churn_prob, tier FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-tier-before'"
    )
    assert before["tier"].notna().all()
    for _, r in before.iterrows():  # default boundaries 0.20 / 0.45 / 0.70
        p = r["churn_prob"]
        assert r["tier"] == ("low" if p < 0.2 else "medium" if p < 0.45 else "high" if p < 0.7 else "critical")

    # Push every boundary to the floor: everyone with prob >= 0.001 is critical.
    set_tiers(wh, 0.0005, 0.0006, 0.001)
    result = run_cycle(wh, settings, AS_OF, run_id="t-tier-after")
    assert result.status == "SUCCEEDED"
    after = tier_counts(wh, "t-tier-after")
    assert after.get("critical", 0) > tier_counts(wh, "t-tier-before").get("critical", 0)
    assert after.get("critical", 0) >= len(before) * 0.8

    # Same probabilities, different tiers: only the config changed.
    joined = wh.query(
        """SELECT a.churn_prob = b.churn_prob AS same_prob FROM CHURN_OPS.RISK_SCORES a
           JOIN CHURN_OPS.RISK_SCORES b ON a.subscriber_key = b.subscriber_key
           WHERE a.run_id = 't-tier-before' AND b.run_id = 't-tier-after'"""
    )
    assert joined["same_prob"].all()


def test_rerun_reuses_pinned_snapshot(seeded):
    wh, settings = seeded
    run_cycle(wh, settings, AS_OF, run_id="t-pin")
    first = tier_counts(wh, "t-pin")
    snap1 = wh.query("SELECT config_json FROM CHURN_OPS.CONFIG_SNAPSHOTS WHERE run_id = 't-pin'")
    assert len(snap1) == 1

    set_tiers(wh, 0.0005, 0.0006, 0.001)  # edit after the first execution
    run_cycle(wh, settings, AS_OF, run_id="t-pin")
    assert tier_counts(wh, "t-pin") == first

    snap2 = wh.query("SELECT config_json FROM CHURN_OPS.CONFIG_SNAPSHOTS WHERE run_id = 't-pin'")
    assert len(snap2) == 1 and snap2.iloc[0]["config_json"] == snap1.iloc[0]["config_json"]
    run = wh.query("SELECT config_snapshot_id FROM CHURN_OPS.RUNS WHERE run_id = 't-pin'").iloc[0]
    assert run["config_snapshot_id"] == "t-pin"

    # A different run id picks up the edit.
    run_cycle(wh, settings, EARLIER, run_id="t-pin-next")
    assert tier_counts(wh, "t-pin-next") != first
