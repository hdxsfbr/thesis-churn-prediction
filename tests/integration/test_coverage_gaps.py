"""T12: a subscriber with no arm left is logged on COVERAGE_GAPS, and an approval is re-checked at execution."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from churn import runtime_config as rc
from churn.agent.cycle import run_agent
from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes, key_map_frame
from churn.execution.approvals import inactive_subscribers, payload_hash, process

AS_OF = datetime(2025, 6, 30)
OPS = "CHURN_OPS"


def _active_keys(wh, settings) -> list[str]:
    sql = (
        f"WITH {contract_ctes(contract_for(settings), settings.key_pepper, ['subscriptions'])} "
        "SELECT DISTINCT subscriber_key FROM subscriptions"
    )
    keys = wh.query(sql)["subscriber_key"].tolist()
    stale = inactive_subscribers(wh, settings, keys, datetime.now())
    return [k for k in keys if k not in stale]


def _queue_email(wh, run_id: str, key: str) -> None:
    """An APPROVED email whose deadline is still open, hashed the way process() checks it."""
    payload = {
        "subscriber_key": key,
        "channel": "email",
        "intervention_type": "checkin_email",
        "subject": "Hello",
        "body": "Hi {{first_name}}",
        "offer_code": None,
        "discount_pct": None,
        "escalation_reason": None,
        "cost_usd": 0.0,
    }
    digest = payload_hash(payload)
    wh.merge_frame(f"{OPS}.RISK_SCORES", pd.DataFrame([{
        "run_id": run_id, "subscriber_key": key, "as_of": AS_OF, "churn_prob": 0.5, "tier": "high",
    }]), keys=["run_id", "subscriber_key"])
    wh.merge_frame(f"{OPS}.INTERVENTION_LOG", pd.DataFrame([{
        "decision_id": run_id, "subscriber_key": key, "run_id": run_id, "payload_hash": digest,
        "approval_expires_at": datetime(2099, 1, 1), "approval_grace_until": datetime(2099, 1, 2),
        "guardrail_events": "[]", "execution_status": "PENDING_APPROVAL", "approval_status": "PENDING",
        "decided_at": datetime(2026, 1, 1),
    }]), keys=["decision_id"])
    wh.merge_frame(f"{OPS}.APPROVALS", pd.DataFrame([{
        **payload, "decision_id": run_id, "run_id": run_id, "payload_hash": digest, "decision": "APPROVED",
        "processed_at": None, "created_at": datetime(2026, 1, 1), "decided_by": "sam",
    }]), keys=["decision_id"])


def _wipe(wh, *decision_ids: str, run_id: str) -> None:
    """Remove only the rows this module inserted. Other runs' history stays."""
    wh.execute(f"DELETE FROM {OPS}.COVERAGE_GAPS WHERE run_id = :r", {"r": run_id})
    wh.execute(f"DELETE FROM {OPS}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
    for did in decision_ids:
        wh.execute(f"DELETE FROM {OPS}.APPROVALS WHERE decision_id = :d", {"d": did})
        wh.execute(f"DELETE FROM {OPS}.INTERVENTION_LOG WHERE decision_id = :d", {"d": did})
        wh.execute(f"DELETE FROM {OPS}.OUTBOX WHERE outbox_id = :d", {"d": did})


def test_no_eligible_arm_writes_coverage_gap_with_reason(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_id, key = "t-gap-12", "gap-key"
    rc.seed(wh, settings, overwrite=True)
    try:
        wh.merge_frame(f"{OPS}.RISK_SCORES", pd.DataFrame([{
            "run_id": run_id, "subscriber_key": key, "as_of": AS_OF, "churn_prob": 0.55, "tier": "high",
            "top_factors": "[]", "archetype": "fatigued", "archetype_conf": 0.8,
            "archetype_probs": "{}", "archetype_low_conf": False,
        }]), keys=["run_id", "subscriber_key"])
        wh.merge_frame(f"{OPS}.APPROVALS", pd.DataFrame([{
            "decision_id": "already-pending", "run_id": "earlier", "subscriber_key": key,
            "decision": "PENDING", "processed_at": None, "created_at": datetime(2025, 6, 1),
        }]), keys=["decision_id"])
        cfg = rc.load_pinned(wh, settings, run_id)
        assert not cfg.agent_halt
        decisions = run_agent(wh, settings, run_id, cfg)
        assert key not in {d.context.subscriber_key for d in decisions}
        gaps = wh.query(f"SELECT * FROM {OPS}.COVERAGE_GAPS WHERE run_id = :r AND subscriber_key = :k",
                        {"r": run_id, "k": key})
        assert len(gaps) == 1
        assert gaps.iloc[0]["reason"] == "DUPLICATE_PENDING"
        events = json.loads(gaps.iloc[0]["guardrail_events"])
        assert events and {e["reason"] for e in events} == {"DUPLICATE_PENDING"}
        sent = wh.query(f"SELECT COUNT(*) AS n FROM {OPS}.OUTBOX WHERE subscriber_key = :k", {"k": key})
        assert int(sent.iloc[0]["n"]) == 0
    finally:
        _wipe(wh, "already-pending", run_id=run_id)
        rc.seed(wh, settings, overwrite=True)


def _reason(wh, decision_id: str) -> str | None:
    raw = wh.query(
        f"SELECT guardrail_events, execution_status FROM {OPS}.INTERVENTION_LOG WHERE decision_id = :d",
        {"d": decision_id},
    ).iloc[0]
    assert raw["execution_status"] == "CANCELLED_STALE"
    events = json.loads(raw["guardrail_events"])
    reasons = [e.get("reason") for e in events]
    assert "CANCELLED_STALE" in {e["event"] for e in events}
    return reasons[-1]


def test_approved_over_contact_cap_is_cancelled_stale(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_id = "t12-cap"
    key = _active_keys(wh, settings)[0]
    rc.seed(wh, settings, overwrite=True)
    try:
        _queue_email(wh, run_id, key)
        wh.merge_frame(f"{OPS}.INTERVENTION_LOG", pd.DataFrame([{
            "decision_id": "t12-prior-contact", "subscriber_key": key, "run_id": "t12-prior",
            "execution_status": "DISPATCHED", "intervention_type": "checkin_email",
            "decided_at": datetime(2025, 6, 20), "dispatched_at": datetime(2025, 6, 20),
        }]), keys=["decision_id"])
        process(wh, settings)
        out = wh.query(f"SELECT COUNT(*) AS n FROM {OPS}.OUTBOX WHERE outbox_id = :d", {"d": run_id})
        assert int(out.iloc[0]["n"]) == 0
        assert _reason(wh, run_id) == "cap"
    finally:
        _wipe(wh, run_id, "t12-prior-contact", run_id=run_id)
        rc.seed(wh, settings, overwrite=True)


def test_approved_email_after_unsubscribe_is_cancelled_stale(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_id = "t12-unsub"
    key = _active_keys(wh, settings)[1]
    customer = key_map_frame(wh, contract_for(settings), settings.key_pepper)
    customer_id = customer.loc[customer["subscriber_key"] == key, "customer_id"].iloc[0]
    rc.seed(wh, settings, overwrite=True)
    try:
        _queue_email(wh, run_id, key)
        wh.execute(
            "INSERT INTO THESIS_SYNTH.email_events "
            "(customer_id, event_id, event_type, campaign_id, flow_id, event_ts, loaded_at) "
            "VALUES (:c, 't12-unsub', 'unsubscribe', 'test', 'test', "
            "TIMESTAMPTZ '2025-07-01 00:00:00+00', TIMESTAMP '2025-07-01 00:00:00')",
            {"c": customer_id},
        )
        process(wh, settings)
        out = wh.query(f"SELECT COUNT(*) AS n FROM {OPS}.OUTBOX WHERE outbox_id = :d", {"d": run_id})
        assert int(out.iloc[0]["n"]) == 0
        assert _reason(wh, run_id) == "unsubscribed"
    finally:
        wh.execute("DELETE FROM THESIS_SYNTH.email_events WHERE event_id = 't12-unsub'")
        _wipe(wh, run_id, run_id=run_id)
        rc.seed(wh, settings, overwrite=True)
