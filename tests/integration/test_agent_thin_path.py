"""Acceptance tests for T10: at-risk subscriber -> check-in email -> OUTBOX and INTERVENTION_LOG."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

import pandas as pd

from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)
AT_RISK = ("medium", "high", "critical")


def _scores(wh, run_id) -> pd.DataFrame:
    return wh.query("SELECT * FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r", {"r": run_id})


def _log(wh, run_id) -> pd.DataFrame:
    return wh.query("SELECT * FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = :r", {"r": run_id})


def _outbox(wh) -> pd.DataFrame:
    return wh.query("SELECT * FROM CHURN_OPS.OUTBOX")


def test_at_risk_subscriber_gets_one_outbox_row_and_log_row(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    result = run_cycle(wh, settings, AS_OF, run_id="t-agent-1")
    scores = _scores(wh, "t-agent-1")
    at_risk = scores[scores["tier"].isin(AT_RISK)]
    assert len(at_risk) > 0

    log = _log(wh, "t-agent-1")
    assert len(log) == len(at_risk)
    assert log["decision_id"].is_unique
    assert set(log["subscriber_key"]) == set(at_risk["subscriber_key"])
    assert (log["intervention_type"] == "checkin_email").all()
    for key, did in zip(log["subscriber_key"], log["decision_id"], strict=True):
        assert did == hashlib.sha256(f"t-agent-1|{key}".encode()).hexdigest()

    out = wh.query(
        "SELECT * FROM CHURN_OPS.OUTBOX WHERE outbox_id IN (SELECT decision_id FROM CHURN_OPS.INTERVENTION_LOG "
        "WHERE run_id = 't-agent-1')"
    )
    assert len(out) == len(at_risk)
    assert out["outbox_id"].is_unique
    assert set(out["outbox_id"]) == set(log["decision_id"])
    assert (log["execution_status"] == "DISPATCHED").all()
    assert set(log["outbox_id"]) == set(out["outbox_id"])
    assert result.status == "SUCCEEDED"
    assert result.decided == result.dispatched == len(at_risk)


def test_log_row_has_all_r6_1_fields_or_pending(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_cycle(wh, settings, AS_OF, run_id="t-agent-2")
    log = _log(wh, "t-agent-2")
    assert len(log) > 0
    required = [
        "decision_id", "subscriber_key", "run_id", "risk", "tier", "archetype", "top_factors",
        "intervention_type", "params", "content_ref", "content_source", "rationale", "policy_scores",
        "guardrail_events", "gate_reason", "approval_status", "execution_status", "outbox_id",
        "response", "outcome", "decided_at", "dispatched_at",
    ]
    for col in required:
        assert col in log.columns, col
        assert log[col].notna().all(), f"{col} has nulls"
    assert (log["response"] == "PENDING").all()
    assert (log["outcome"] == "PENDING").all()
    assert log["content_source"].isin(["template", "llm"]).all()
    for raw in log["rationale"]:
        r = json.loads(raw)
        assert r["chosen"] == "checkin_email"
        assert "top_factors" in r
    for raw in log["top_factors"]:
        assert isinstance(json.loads(raw), list)
    scores = _scores(wh, "t-agent-2").set_index("subscriber_key")
    for _, row in log.iterrows():
        assert row["risk"] == scores.loc[row["subscriber_key"], "churn_prob"]
        assert row["tier"] == scores.loc[row["subscriber_key"], "tier"]


def test_outbox_row_contains_everything_sender_needs(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_cycle(wh, settings, AS_OF, run_id="t-agent-3")
    log = _log(wh, "t-agent-3")
    out = wh.query(
        "SELECT o.* FROM CHURN_OPS.OUTBOX o JOIN CHURN_OPS.INTERVENTION_LOG l ON l.decision_id = o.outbox_id "
        "WHERE l.run_id = 't-agent-3'"
    )
    assert len(out) == len(log) > 0
    keys = dict(zip(log["decision_id"], log["subscriber_key"], strict=True))
    for _, row in out.iterrows():
        assert row["channel"] == "email"
        assert row["intervention_type"] == "checkin_email"
        assert row["subject"].strip()
        assert "{{first_name}}" in row["body"]
        assert row["subscriber_key"] == keys[row["outbox_id"]]
        assert row["status"] == "READY"
        assert pd.notna(row["created_at"])
        # No raw PII: the only personal token is the merge field the sender fills in.
        assert not re.search(r"[\w.]+@[\w.]+\.\w+", row["subject"] + row["body"])
        assert re.sub(r"\{\{\w+\}\}", "", row["body"]).count("{{") == 0


def test_agent_consumes_same_runs_scores(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_cycle(wh, settings, AS_OF, run_id="t-agent-5a")
    run_cycle(wh, settings, date(2025, 5, 31), run_id="t-agent-5b")
    for run_id in ("t-agent-5a", "t-agent-5b"):
        scores = _scores(wh, run_id)
        expect = scores[scores["tier"].isin(AT_RISK)].set_index("subscriber_key")
        log = _log(wh, run_id).set_index("subscriber_key")
        assert set(log.index) == set(expect.index)
        assert (log["run_id"] == run_id).all()
        assert (log["risk"].sort_index() == expect["churn_prob"].sort_index()).all()
        assert (log["tier"].sort_index() == expect["tier"].sort_index()).all()
    a, b = _log(wh, "t-agent-5a"), _log(wh, "t-agent-5b")
    assert not set(a["decision_id"]) & set(b["decision_id"])


def _skipped_run_checks(wh, settings, run_id):
    result = run_cycle(wh, settings, AS_OF, run_id=run_id)
    assert result.status == "DEGRADED"
    assert len(_log(wh, run_id)) == 0
    joined = wh.query(
        "SELECT COUNT(*) AS n FROM CHURN_OPS.OUTBOX o JOIN CHURN_OPS.INTERVENTION_LOG l "
        "ON o.outbox_id = l.decision_id WHERE l.run_id = :r", {"r": run_id}
    )
    assert int(joined.iloc[0]["n"]) == 0
    assert result.decided == result.dispatched == 0
    return result


def _reset_cfg(wh, settings):
    from churn import runtime_config as rc

    rc.seed(wh, settings, overwrite=True)


def _set_cfg(wh, key, value):
    wh.execute("DELETE FROM CHURN_OPS.AGENT_CONFIG WHERE key = :k", {"k": key})
    wh.execute(
        "INSERT INTO CHURN_OPS.AGENT_CONFIG (key, value_json, updated_by, updated_at) "
        "VALUES (:k, :v, 'test', CURRENT_TIMESTAMP)", {"k": key, "v": json.dumps(value)}
    )


def test_skipped_agent_writes_nothing_and_counts_at_risk(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    try:
        # approve_all: invalid gate thresholds; tiers are valid so at_risk is still counted.
        _set_cfg(wh, "gate", {"t_risk": 5, "t_cost_usd": 1, "t_discount_pct": 5, "always_approve": []})
        res = _skipped_run_checks(wh, settings, "t-agent-skip-gate")
        assert res.gate_mode == "approve_all"
        scores = _scores(wh, "t-agent-skip-gate")
        expected = int(scores["tier"].isin(AT_RISK).sum())
        assert expected > 0 and res.at_risk == expected
        runs = wh.query("SELECT * FROM CHURN_OPS.RUNS WHERE run_id = 't-agent-skip-gate'").iloc[0]
        assert runs["at_risk"] == expected and runs["decided"] == 0 and runs["dispatched"] == 0

        # agent_halt: invalid tiers; no at-risk definition, so the count stays 0.
        _reset_cfg(wh, settings)
        _set_cfg(wh, "tiers", {"boundaries": {"medium": 0.5, "high": 0.4, "critical": 0.7}, "at_risk_from": "medium"})
        res = _skipped_run_checks(wh, settings, "t-agent-skip-tiers")
        assert res.agent_halted and res.at_risk == 0
    finally:
        _reset_cfg(wh, settings)


def test_failed_rerun_clears_counts_of_earlier_success(pipeline, monkeypatch):
    from churn.orchestration import runner

    wh, settings = pipeline["wh"], pipeline["settings"]
    first = run_cycle(wh, settings, AS_OF, run_id="t-agent-rerun")
    assert first.at_risk > 0
    monkeypatch.setattr(runner, "validate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        run_cycle(wh, settings, AS_OF, run_id="t-agent-rerun")
    except runner.RunFailed:
        pass
    else:
        raise AssertionError("expected RunFailed")
    row = wh.query("SELECT * FROM CHURN_OPS.RUNS WHERE run_id = 't-agent-rerun'").iloc[0]
    assert row["status"] == "FAILED"
    assert pd.isna(row["at_risk"]) or row["at_risk"] == 0
    assert pd.isna(row["decided"]) or row["decided"] == 0
