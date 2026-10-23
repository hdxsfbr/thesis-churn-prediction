"""Holdout assignment is a deterministic share of the at-risk population, and control is not sent."""

from datetime import datetime

import pandas as pd

from churn import runtime_config
from churn.agent.cycle import run_agent
from churn.execution.outbox import dispatch
from churn.feedback.holdout import assign, is_control

# k3 falls in the control bucket for share 0.10 and salt v1; k0 does not. The tiny scored
# population only has about a dozen at-risk subscribers, so a live cycle can draw no control member.
CONTROL_KEY = "k3"
TREATMENT_KEY = "k0"
RUN_ID = "t16-holdout-keys"
AS_OF = datetime(2025, 6, 30)


def test_holdout_share_matches_config_and_is_deterministic():
    keys = [f"s{i:05d}" for i in range(10_000)]
    first = assign(keys, 0.10, "v1")
    assert first == assign(list(reversed(keys)), 0.10, "v1")
    control = sum(group == "control" for group in first.values())
    assert abs(control / len(keys) - 0.10) < 0.015
    assert set(assign(keys, 0.0, "v1").values()) == {"treatment"}
    assert set(assign(keys, 1.0, "v1").values()) == {"control"}
    assert assign(keys, 0.10, "v1") != assign(keys, 0.10, "v2")


def test_control_members_get_no_outbox_row(pipeline):
    assert is_control(CONTROL_KEY, 0.10, "v1")
    assert not is_control(TREATMENT_KEY, 0.10, "v1")
    wh, settings = pipeline["wh"], pipeline["settings"]
    ops = settings.schemas.ops
    wh.merge_frame(
        f"{ops}.RISK_SCORES",
        pd.DataFrame(
            [
                {
                    "run_id": RUN_ID,
                    "subscriber_key": key,
                    "as_of": AS_OF,
                    "churn_prob": 0.5,
                    "tier": "high",
                    "top_factors": "[]",
                    "archetype": "fatigued",
                    "archetype_conf": 0.8,
                    "archetype_probs": "{}",
                    "archetype_low_conf": False,
                }
                for key in (CONTROL_KEY, TREATMENT_KEY)
            ]
        ),
        keys=["run_id", "subscriber_key"],
    )
    try:
        decisions = run_agent(wh, settings, RUN_ID, runtime_config.load(wh, settings))
        dispatch(wh, settings, decisions)
        log = wh.query(
            f"SELECT subscriber_key, intervention_type, execution_status, decision_id "
            f"FROM {ops}.INTERVENTION_LOG WHERE run_id = :r",
            {"r": RUN_ID},
        ).set_index("subscriber_key")
        assert log.loc[CONTROL_KEY, "intervention_type"] == "HOLDOUT"
        assert log.loc[CONTROL_KEY, "execution_status"] == "HOLDOUT"
        assert log.loc[TREATMENT_KEY, "intervention_type"] != "HOLDOUT"
        assert all(d.context.subscriber_key != CONTROL_KEY for d in decisions)
        sent = wh.query(
            f"SELECT subscriber_key FROM {ops}.OUTBOX WHERE outbox_id = :d",
            {"d": log.loc[CONTROL_KEY, "decision_id"]},
        )
        assert sent.empty
    finally:
        wh.execute(f"DELETE FROM {ops}.COVERAGE_GAPS WHERE run_id = :r", {"r": RUN_ID})
        wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": RUN_ID})
        wh.execute(f"DELETE FROM {ops}.INTERVENTION_LOG WHERE run_id = :r", {"r": RUN_ID})
        wh.execute(f"DELETE FROM {ops}.APPROVALS WHERE run_id = :r", {"r": RUN_ID})
        for key in (CONTROL_KEY, TREATMENT_KEY):
            wh.execute(f"DELETE FROM {ops}.OUTBOX WHERE subscriber_key = :k", {"k": key})
            wh.execute(f"DELETE FROM {ops}.HOLDOUT_ASSIGNMENTS WHERE subscriber_key = :k", {"k": key})
