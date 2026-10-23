"""Recovery after a source outage, a mid-batch write, an LLM failure, and a kill between steps."""

from datetime import date

import pytest

from churn.obs.errors import LLMError
from churn.orchestration.runner import RunFailed, run_cycle
from fixtures.faulty_warehouse import FaultyWarehouse

AS_OF = date(2025, 6, 30)


def test_source_unavailable_then_rerun_recovers(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    faulty = FaultyWarehouse(wh)
    faulty.source_down = True
    with pytest.raises(RunFailed):
        run_cycle(faulty, settings, AS_OF, run_id="t20-source")
    assert faulty.source_attempts == 3
    row = wh.query(
        "SELECT status, error_class FROM CHURN_OPS.RUNS WHERE run_id = 't20-source'"
    ).iloc[0]
    assert row["status"] == "FAILED"
    assert row["error_class"] == "SourceUnavailable"
    scores = wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't20-source'")
    assert int(scores.iloc[0]["n"]) == 0

    faulty.source_down = False
    result = run_cycle(faulty, settings, AS_OF, run_id="t20-source")
    assert result.status == "SUCCEEDED"
    assert result.scored > 0


def test_write_failure_mid_batch_no_lost_no_duplicate_rows(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    faulty = FaultyWarehouse(wh)
    faulty.fail_merge_table = "RISK_SCORES"
    faulty.fail_merge_on = 2
    with pytest.raises(RunFailed):
        run_cycle(faulty, settings, AS_OF, run_id="t20-write", shards=2)
    first = wh.query("SELECT subscriber_key FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't20-write'")
    assert len(first) > 0
    assert first["subscriber_key"].is_unique

    faulty.fail_merge_table = None
    result = run_cycle(faulty, settings, AS_OF, run_id="t20-write", shards=2)
    assert result.status == "SUCCEEDED"
    assert result.scored == result.active_in_source
    second = wh.query("SELECT subscriber_key FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't20-write'")
    assert second["subscriber_key"].is_unique
    assert set(first["subscriber_key"]) <= set(second["subscriber_key"])
    assert len(second) == result.scored


def test_llm_outage_mid_run_recovers(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    faulty = FaultyWarehouse(wh)

    def boom(name, shard):
        if name == "agent" and shard == 1:
            raise LLMError("provider down")

    faulty.before_step = boom
    with pytest.raises(RunFailed):
        run_cycle(faulty, settings, AS_OF, run_id="t20-llm", shards=2)
    row = wh.query("SELECT status, error_class FROM CHURN_OPS.RUNS WHERE run_id = 't20-llm'").iloc[0]
    assert row["status"] == "FAILED"
    assert row["error_class"] == "LLMError"
    first = wh.query(
        "SELECT decision_id, content_ref FROM CHURN_OPS.INTERVENTION_LOG "
        "WHERE run_id = 't20-llm' ORDER BY decision_id"
    )

    faulty.before_step = None
    result = run_cycle(faulty, settings, AS_OF, run_id="t20-llm", shards=2)
    assert result.status == "SUCCEEDED"
    second = wh.query(
        "SELECT decision_id, content_ref FROM CHURN_OPS.INTERVENTION_LOG "
        "WHERE run_id = 't20-llm' ORDER BY decision_id"
    )
    assert second["decision_id"].is_unique
    assert len(second) >= len(first)
    assert result.decided == len(second)
    if len(first):
        kept = second.set_index("decision_id")["content_ref"]
        for saved in first.itertuples(index=False):
            assert kept.loc[saved.decision_id] == saved.content_ref


def test_kill_between_steps_resumes_from_checkpoint(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    faulty = FaultyWarehouse(wh)

    def kill(name, _shard):
        if name == "outcomes":
            raise SystemExit("killed between steps")

    faulty.before_step = kill
    with pytest.raises(SystemExit):
        run_cycle(faulty, settings, AS_OF, run_id="t20-kill")
    status = wh.query("SELECT status FROM CHURN_OPS.RUNS WHERE run_id = 't20-kill'").iloc[0]["status"]
    assert status == "RUNNING"
    steps = set(wh.query("SELECT step FROM CHURN_OPS.RUN_STEPS WHERE run_id = 't20-kill'")["step"])
    assert "agent" in steps
    assert "outcomes" not in steps
    keys = wh.query(
        "SELECT decision_id FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't20-kill' ORDER BY decision_id"
    )
    assert len(keys) > 0

    faulty.before_step = None
    result = run_cycle(faulty, settings, AS_OF, run_id="t20-kill")
    assert result.status == "SUCCEEDED"
    again = wh.query(
        "SELECT decision_id FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't20-kill' ORDER BY decision_id"
    )
    assert list(keys["decision_id"]) == list(again["decision_id"])


def test_cycle_twice_identical_counts_and_keys(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    first = run_cycle(wh, settings, AS_OF, run_id="t20-twice")
    keys = wh.query(
        "SELECT decision_id FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't20-twice' ORDER BY decision_id"
    )
    second = run_cycle(wh, settings, AS_OF, run_id="t20-twice")
    again = wh.query(
        "SELECT decision_id FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't20-twice' ORDER BY decision_id"
    )
    assert first.scored == second.scored
    assert first.decided == second.decided
    assert first.dispatched == second.dispatched
    assert first.queued == second.queued
    assert list(keys["decision_id"]) == list(again["decision_id"])
    assert again["decision_id"].is_unique
