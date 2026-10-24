"""A new arm listed in interventions.yaml, plus one POLICY_STATS row, is chosen and sent."""

import json
from datetime import date

import pandas as pd

from churn.model.archetype import ARCHETYPES
from churn.orchestration.runner import run_cycle
from churn.settings import config_dir

AS_OF = date(2025, 6, 30)
RUN_ID = "t26-dummy"
ARM = "dummy_email"
LINE = "  - tests.fixtures.dummy_intervention.DummyEmail\n"


def test_dummy_intervention_dispatched_via_config_only(pipeline, tmp_path, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    ops = settings.schemas.ops
    path = config_dir() / "interventions.yaml"
    original_yaml = path.read_text()
    copy_dir = tmp_path / "config"
    copy_dir.mkdir()
    monkeypatch.setattr("churn.interventions.base.config_dir", lambda: copy_dir)
    original_policy = wh.query(
        f"SELECT value_json FROM {ops}.AGENT_CONFIG WHERE key = 'policy'"
    ).iloc[0, 0]
    policy = json.loads(original_policy)
    policy["priors"]["default"][ARM] = [1, 99]
    rows = [
        {
            "archetype": archetype,
            "intervention_type": ARM,
            "context_bucket": f"{tier}:{band}",
            "alpha": 1000.0,
            "beta": 1.0,
            "n": 0,
            "last_updated_run": RUN_ID,
        }
        for archetype in (*ARCHETYPES, "PENDING")
        for tier in ("low", "medium", "high", "critical")
        for band in ("<90", "90-365", ">365")
    ]
    try:
        (copy_dir / "interventions.yaml").write_text(original_yaml + LINE)
        wh.execute(
            f"UPDATE {ops}.AGENT_CONFIG SET value_json = :v WHERE key = 'policy'",
            {"v": json.dumps(policy)},
        )
        wh.merge_frame(f"{ops}.POLICY_STATS", pd.DataFrame(rows),
                       keys=["archetype", "intervention_type", "context_bucket"])
        run_cycle(wh, settings, AS_OF, run_id=RUN_ID)
        log = wh.query(
            f"SELECT decision_id, intervention_type FROM {ops}.INTERVENTION_LOG WHERE run_id = :r",
            {"r": RUN_ID},
        )
        chosen = log[log["intervention_type"] == ARM]
        assert not chosen.empty
        sent = wh.query(f"SELECT intervention_type FROM {ops}.OUTBOX")
        assert (sent["intervention_type"] == ARM).any()
    finally:
        wh.execute(
            f"UPDATE {ops}.AGENT_CONFIG SET value_json = :v WHERE key = 'policy'",
            {"v": original_policy},
        )
        wh.execute(f"DELETE FROM {ops}.POLICY_STATS WHERE intervention_type = '{ARM}'")
        ids = wh.query(
            f"SELECT decision_id FROM {ops}.INTERVENTION_LOG WHERE run_id = :r", {"r": RUN_ID}
        )
        wh.execute(f"DELETE FROM {ops}.COVERAGE_GAPS WHERE run_id = :r", {"r": RUN_ID})
        wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": RUN_ID})
        if not ids.empty:
            for decision_id in ids["decision_id"]:
                wh.execute(f"DELETE FROM {ops}.OUTBOX WHERE outbox_id = :d", {"d": decision_id})
                wh.execute(f"DELETE FROM {ops}.APPROVALS WHERE decision_id = :d", {"d": decision_id})
                wh.execute(f"DELETE FROM {ops}.INTERVENTION_LOG WHERE decision_id = :d", {"d": decision_id})
