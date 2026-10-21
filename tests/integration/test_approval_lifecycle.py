"""Acceptance tests for T14: approver edits on CHURN_OPS.APPROVALS are processed at the start of each cycle."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from churn import runtime_config as rc
from churn.agent.cycle import run_agent
from churn.contract.loader import contract_for
from churn.execution.approvals import process
from churn.execution.outbox import dispatch
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)
OPS = "CHURN_OPS"


@pytest.fixture()
def env(pipeline):
    """Gate threshold so low that every at-risk decision needs approval; config restored afterwards."""
    wh, settings = pipeline["wh"], pipeline["settings"]
    rc.seed(wh, settings, overwrite=True)
    gate = {"t_risk": 0.0001, "t_cost_usd": 15, "t_discount_pct": 20, "always_approve": []}
    wh.execute("UPDATE CHURN_OPS.AGENT_CONFIG SET value_json = :v WHERE key = 'gate'", {"v": json.dumps(gate)})
    yield wh, settings
    rc.seed(wh, settings, overwrite=True)


def approvals(wh, run_id) -> pd.DataFrame:
    return wh.query(f"SELECT * FROM {OPS}.APPROVALS WHERE run_id = :r ORDER BY decision_id", {"r": run_id})


def log_of(wh, decision_id) -> dict:
    return wh.query(f"SELECT * FROM {OPS}.INTERVENTION_LOG WHERE decision_id = :d", {"d": decision_id}).iloc[0].to_dict()


def outbox_count(wh, decision_id) -> int:
    return int(wh.query(f"SELECT COUNT(*) AS n FROM {OPS}.OUTBOX WHERE outbox_id = :d", {"d": decision_id}).iloc[0]["n"])


def events(wh, decision_id) -> list[str]:
    return [e["event"] for e in json.loads(log_of(wh, decision_id)["guardrail_events"])]


def edit(wh, decision_id, **fields):
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    wh.execute(f"UPDATE {OPS}.APPROVALS SET {sets} WHERE decision_id = :d", {**fields, "d": decision_id})


def pending(env_, run_id):
    wh, settings = env_
    result = run_cycle(wh, settings, AS_OF, run_id=run_id)
    rows = approvals(wh, run_id)
    assert len(rows) == result.at_risk > 0
    return rows, result


def test_pending_rows_have_no_outbox_and_carry_expiry_and_hash(env):
    rows, result = pending(env, "t-appr-pending")
    wh = env[0]
    assert (rows["decision"] == "PENDING").all()
    assert rows["payload_hash"].str.len().eq(64).all()
    assert rows["processed_at"].isna().all()
    hours = (pd.to_datetime(rows["expires_at"]) - pd.to_datetime(rows["created_at"])).dt.total_seconds() / 3600
    assert (hours.round(3) == 72).all()
    for did in rows["decision_id"]:
        assert outbox_count(wh, did) == 0
        row = log_of(wh, did)
        assert row["approval_status"] == "PENDING" and row["payload_hash"] in set(rows["payload_hash"])
    assert result.decided == result.at_risk


def test_approved_creates_exactly_one_outbox_row_next_cycle(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-ok-1")
    did = rows.iloc[0]["decision_id"]
    edit(wh, did, decision="  approved ", decided_by="ana@thesis.example", decided_at=datetime(2025, 7, 1, 9, 0))
    assert outbox_count(wh, did) == 0  # nothing happens until the next cycle

    run_cycle(wh, settings, AS_OF, run_id="t-appr-ok-2")
    assert outbox_count(wh, did) == 1
    out = wh.query(f"SELECT * FROM {OPS}.OUTBOX WHERE outbox_id = :d", {"d": did}).iloc[0]
    assert out["status"] == "READY" and out["subscriber_key"] == rows.iloc[0]["subscriber_key"]
    assert out["subject"] == rows.iloc[0]["subject"] and out["body"] == rows.iloc[0]["body"]
    row = approvals(wh, "t-appr-ok-1").set_index("decision_id").loc[did]
    assert row["processed_status"] == "APPROVED" and pd.notna(row["processed_at"])
    log = log_of(wh, did)
    assert log["approval_status"] == "APPROVED" and log["execution_status"] == "DISPATCHED"
    assert log["outbox_id"] == did and log["decided_by"] == "ana@thesis.example"
    assert pd.Timestamp(log["approved_at"]) == pd.Timestamp("2025-07-01 09:00")
    # Other rows stay pending, and a third cycle (or a replay of the processing) adds nothing.
    run_cycle(wh, settings, AS_OF, run_id="t-appr-ok-3")
    process(wh, settings)
    assert outbox_count(wh, did) == 1
    others = rows.iloc[1:]["decision_id"]
    assert all(outbox_count(wh, d) == 0 for d in others)


def test_rejected_creates_none_and_logs_approver(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-rej-1")
    did = rows.iloc[0]["decision_id"]
    edit(wh, did, decision="Rejected", decided_by="bo", decided_at=datetime(2025, 7, 1, 10, 0), note="too generous")
    run_cycle(wh, settings, AS_OF, run_id="t-appr-rej-2")
    assert outbox_count(wh, did) == 0
    log = log_of(wh, did)
    assert log["approval_status"] == "REJECTED" and log["execution_status"] == "NOT_DISPATCHED"
    assert log["decided_by"] == "bo" and log["approval_note"] == "too generous"
    assert pd.Timestamp(log["approved_at"]) == pd.Timestamp("2025-07-01 10:00")
    assert approvals(wh, "t-appr-rej-1").set_index("decision_id").loc[did]["processed_status"] == "REJECTED"


def test_unknown_approver_and_time_are_logged_as_unknown_and_processing_time(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-anon")
    did = rows.iloc[0]["decision_id"]
    edit(wh, did, decision="REJECTED")
    now = datetime(2025, 7, 2, 12, 0)
    process(wh, settings, now=now)
    log = log_of(wh, did)
    assert log["decided_by"] == "unknown" and pd.Timestamp(log["approved_at"]) == pd.Timestamp(now)


def test_expired_after_expiry_no_outbox(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-exp-1")
    expired, answered_late, fresh = rows.iloc[0]["decision_id"], rows.iloc[1]["decision_id"], rows.iloc[2]["decision_id"]
    process(wh, settings, now=datetime.now() + timedelta(hours=71))  # before the deadline: nothing expires
    assert log_of(wh, expired)["approval_status"] == "PENDING"
    edit(wh, answered_late, decision="APPROVED", decided_at=datetime.now())  # answered after the deadline
    late = datetime.now() + timedelta(hours=73)  # time moves forward; the rows are not touched
    process(wh, settings, now=late)
    for did in (expired, answered_late):
        assert outbox_count(wh, did) == 0
        assert log_of(wh, did)["approval_status"] == "EXPIRED"
        assert "EXPIRED" in events(wh, did)
    assert approvals(wh, "t-appr-exp-1").set_index("decision_id").loc[expired]["processed_status"] == "EXPIRED"
    assert log_of(wh, fresh)["approval_status"] == "EXPIRED"  # unanswered rows expire too


def test_edited_expiry_and_backdated_decision_cannot_defeat_deadline(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-exp-tamper")
    ids = list(rows["decision_id"][:2])
    far = datetime.now() + timedelta(days=365)
    edit(wh, ids[0], expires_at=far, decision="APPROVED")
    # Backdated decided_at (before the real deadline) plus a pushed expiry.
    edit(wh, ids[1], expires_at=far, decision="APPROVED", decided_at=datetime.now() - timedelta(hours=1))
    process(wh, settings, now=datetime.now() + timedelta(hours=100))
    for did in ids:
        assert outbox_count(wh, did) == 0
        assert log_of(wh, did)["approval_status"] == "EXPIRED"


def test_invalid_value_stays_pending_logged(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-inv-1")
    did = rows.iloc[0]["decision_id"]
    edit(wh, did, decision="maybe")
    process(wh, settings)
    process(wh, settings)  # the same bad value is logged once
    assert outbox_count(wh, did) == 0
    assert events(wh, did) == ["INVALID_VALUE"]
    row = approvals(wh, "t-appr-inv-1").set_index("decision_id").loc[did]
    assert pd.isna(row["processed_at"])
    assert log_of(wh, did)["approval_status"] == "PENDING"

    # Still pending: a corrected value is honoured, and an unanswered one expires.
    edit(wh, did, decision="APPROVED")
    process(wh, settings)
    assert outbox_count(wh, did) == 1
    other = rows.iloc[1]["decision_id"]
    edit(wh, other, decision="nope")
    process(wh, settings, now=datetime.now() + timedelta(hours=73))
    assert log_of(wh, other)["approval_status"] == "EXPIRED"


def test_late_edit_ignored(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-late-1")
    did = rows.iloc[0]["decision_id"]
    edit(wh, did, decision="APPROVED", decided_by="ana")
    process(wh, settings)
    assert outbox_count(wh, did) == 1
    edit(wh, did, decision="REJECTED", decided_by="mallory")
    process(wh, settings)
    process(wh, settings)
    assert outbox_count(wh, did) == 1
    log = log_of(wh, did)
    assert log["approval_status"] == "APPROVED" and log["decided_by"] == "ana"
    assert events(wh, did) == ["LATE_EDIT"]  # logged once

    # Same for a row that was rejected and then flipped to approved.
    rej = rows.iloc[1]["decision_id"]
    edit(wh, rej, decision="REJECTED")
    process(wh, settings)
    edit(wh, rej, decision="APPROVED")
    process(wh, settings)
    assert outbox_count(wh, rej) == 0 and "LATE_EDIT" in events(wh, rej)


def test_tampered_context_treated_as_rejected(env):
    wh, settings = env
    rows, _ = pending(env, "t-appr-tamper-1")
    body_edit, discount_edit, rehash = (rows.iloc[i]["decision_id"] for i in range(3))
    edit(wh, body_edit, decision="APPROVED", body="Send everyone a free year")
    edit(wh, discount_edit, decision="APPROVED", discount_pct=90.0)
    forged = rows.set_index("decision_id").loc[rehash]
    edit(wh, rehash, decision="APPROVED", offer_code="PCT_25_NEXT_2", payload_hash=forged["payload_hash"][::-1])
    process(wh, settings)
    for did in (body_edit, discount_edit, rehash):
        assert outbox_count(wh, did) == 0
        log = log_of(wh, did)
        assert log["approval_status"] == "TAMPERED" and log["execution_status"] == "NOT_DISPATCHED"
        assert "TAMPERED" in events(wh, did)
    assert approvals(wh, "t-appr-tamper-1").set_index("decision_id").loc[body_edit]["processed_status"] == "TAMPERED"


def _sub_table(settings):
    return contract_for(settings).physical("subscriptions")


def _customer_id(wh, settings, subscriber_key):
    df = wh.query(f"SELECT customer_id FROM {settings.schemas.private}.KEY_MAP WHERE subscriber_key = :k",
                  {"k": subscriber_key})
    return df.iloc[0]["customer_id"]


@pytest.fixture()
def restore_subscriptions(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    table = _sub_table(settings)
    saved = wh.query(f"SELECT * FROM {table}")
    yield table
    wh.execute(f"DELETE FROM {table}")
    wh.insert_frame(table, saved)


def _cancel(wh, table, customer_id):
    wh.execute(
        f"UPDATE {table} SET status = 'CANCELLED', cancelled_at = :t WHERE customer_id = :c",
        {"t": datetime.now() - timedelta(hours=1), "c": customer_id},
    )


def test_cancelled_between_scoring_and_dispatch_not_sent(pipeline, restore_subscriptions):
    wh, settings = pipeline["wh"], pipeline["settings"]
    rc.seed(wh, settings, overwrite=True)
    run_id = "t-appr-cancel"
    run_cycle(wh, settings, AS_OF, run_id=run_id)  # scores, decides and dispatches once; we redo the tail below
    wh.execute(f"DELETE FROM {OPS}.OUTBOX WHERE outbox_id IN "
               f"(SELECT decision_id FROM {OPS}.INTERVENTION_LOG WHERE run_id = :r)", {"r": run_id})
    wh.execute(f"DELETE FROM {OPS}.INTERVENTION_LOG WHERE run_id = :r", {"r": run_id})
    wh.execute(f"DELETE FROM {OPS}.APPROVALS WHERE run_id = :r", {"r": run_id})
    cfg = rc.load_pinned(wh, settings, run_id)
    decisions = [d for d in run_agent(wh, settings, run_id, cfg) if not d.gate.needs_approval]
    assert len(decisions) > 1
    gone, kept = decisions[0], decisions[1:]
    _cancel(wh, restore_subscriptions, _customer_id(wh, settings, gone.context.subscriber_key))

    dispatch(wh, settings, decisions)

    assert outbox_count(wh, gone.decision_id) == 0
    log = log_of(wh, gone.decision_id)
    assert log["execution_status"] == "CANCELLED_STALE" and pd.isna(log["outbox_id"])
    assert "CANCELLED_STALE" in events(wh, gone.decision_id)
    assert all(outbox_count(wh, d.decision_id) == 1 for d in kept)


def test_approved_but_cancelled_before_execution_is_cancelled_stale(env, restore_subscriptions):
    wh, settings = env
    rows, _ = pending(env, "t-appr-stale-1")
    row = rows.iloc[0]
    edit(wh, row["decision_id"], decision="APPROVED")
    _cancel(wh, restore_subscriptions, _customer_id(wh, settings, row["subscriber_key"]))
    process(wh, settings)
    assert outbox_count(wh, row["decision_id"]) == 0
    log = log_of(wh, row["decision_id"])
    assert log["approval_status"] == "APPROVED" and log["execution_status"] == "CANCELLED_STALE"
    assert "CANCELLED_STALE" in events(wh, row["decision_id"])
    status = approvals(wh, "t-appr-stale-1").set_index("decision_id").loc[row["decision_id"]]["processed_status"]
    assert status == "CANCELLED_STALE"
