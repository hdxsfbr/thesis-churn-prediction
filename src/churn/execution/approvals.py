"""Approvals: the CHURN_OPS.APPROVALS queue and its lifecycle (R5.1-R5.4, ARCHITECTURE 6.5).

``queue_rows`` builds the rows the gate sends to approval. ``process`` runs at the start of each agent step and
acts on the approver's edits. The agent trusts only ``decision``, ``decided_by`` and ``note`` from the row (and
``decided_at`` only for the audit trail); the action is checked against the payload hash kept in INTERVENTION_LOG.

The approval deadline is read from INTERVENTION_LOG.approval_expires_at, never from APPROVALS.expires_at (which the
approver can edit). Expiry is judged against the processing time ``now`` only: an approver-supplied ``decided_at``
cannot defeat it. Known limitation: an answer given in time but processed after the deadline counts as EXPIRED.

Eligibility re-check at execution: the subscriber must still be active in the source. Contact caps and email
unsubscribes are not enforced yet because the guardrail work that defines them is a separate ticket.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.settings import Settings
from churn.warehouse.base import Warehouse

if TYPE_CHECKING:
    from churn.agent.cycle import Decision

PAYLOAD_FIELDS = (
    "subscriber_key", "channel", "intervention_type", "subject", "body",
    "offer_code", "discount_pct", "escalation_reason", "cost_usd",
)
STALE_REASON = "subscriber no longer active in the source"


def _plain(v: Any) -> Any:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    return float(v) if isinstance(v, int | float) or hasattr(v, "dtype") else v


def payload_hash(payload: Mapping[str, Any]) -> str:
    """sha256 of the proposed action, over a fixed field list in canonical JSON."""
    canon = json.dumps({k: _plain(payload.get(k)) for k in PAYLOAD_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def approval_row(decision: Decision, created_at: datetime) -> dict:
    """The APPROVALS row for a decision the gate sent to approval (no outbox row exists for it)."""
    gate, ctx, payload = decision.gate, decision.context, decision.payload()
    return {
        "decision_id": decision.decision_id,
        "run_id": ctx.run_id,
        "subscriber_key": ctx.subscriber_key,
        "tier": ctx.tier,
        "risk": ctx.risk,
        "archetype": ctx.archetype,
        "summary": decision.rationale()["summary"],
        "gate_reason": gate.text,
        "channel": payload["channel"],
        "intervention_type": payload["intervention_type"],
        "subject": payload["subject"],
        "body": payload["body"],
        "offer_code": payload["offer_code"],
        "discount_pct": payload["discount_pct"],
        "escalation_reason": payload["escalation_reason"],
        "cost_usd": payload["cost_usd"],
        "payload_hash": payload_hash(payload),
        "created_at": created_at,
        "expires_at": created_at + timedelta(hours=gate.expiry_h),
        "decision": "PENDING",
        "decided_by": None,
        "decided_at": None,
        "note": None,
        "processed_at": None,
        "processed_status": None,
        "processed_decision": None,
        "last_seen_decision": None,
    }


def inactive_subscribers(wh: Warehouse, settings: Settings, keys: list[str], now: datetime) -> set[str]:
    """Which of ``keys`` are not active in the source right now (cancelled, or no subscription at all)."""
    if not keys:
        return set()
    statuses = ", ".join("'" + s.replace("'", "''") + "'" for s in settings.scoring.active_statuses)
    sql = (
        f"WITH {contract_ctes(contract_for(settings), settings.key_pepper, ['subscriptions'])} "
        "SELECT DISTINCT subscriber_key FROM subscriptions "
        "WHERE started_at <= CAST(:now AS TIMESTAMP) AND ("
        f"(cancelled_at IS NULL AND status IN ({statuses})) OR cancelled_at > CAST(:now AS TIMESTAMP))"
    )
    active = set(wh.query(sql, {"now": now})["subscriber_key"])
    return {k for k in keys if k not in active}


def _ts(v: Any) -> datetime | None:
    return None if v is None or pd.isna(v) else pd.Timestamp(v).to_pydatetime()


def _text(v: Any) -> str | None:
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)


def _set(wh: Warehouse, table: str, key: str, value: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    wh.execute(f"UPDATE {table} SET {sets} WHERE {key} = :key_", {**fields, "key_": value})


def process(wh: Warehouse, settings: Settings, now: datetime | None = None) -> Counter:
    """Act on approver edits. Returns a count per outcome (APPROVED, REJECTED, EXPIRED, ...)."""
    now = now or datetime.now()
    ops = settings.schemas.ops
    counts: Counter = Counter()
    rows = wh.query(f"SELECT * FROM {ops}.APPROVALS ORDER BY decision_id")
    if rows.empty:
        return counts
    rows.columns = [c.lower() for c in rows.columns]
    logs = wh.query(
        f"SELECT decision_id, payload_hash, approval_expires_at, guardrail_events FROM {ops}.INTERVENTION_LOG "
        f"WHERE decision_id IN (SELECT decision_id FROM {ops}.APPROVALS)"
    )
    logs.columns = [c.lower() for c in logs.columns]
    log_by_id = {r.decision_id: r for r in logs.itertuples(index=False)}
    log_events: dict[str, list[dict]] = {
        did: json.loads(r.guardrail_events) if isinstance(r.guardrail_events, str) and r.guardrail_events else []
        for did, r in log_by_id.items()
    }

    def note_event(did: str, event: str, **extra: Any) -> None:
        log_events[did].append({"event": event, "at": now.isoformat(), **extra})
        _set(wh, f"{ops}.INTERVENTION_LOG", "decision_id", did, guardrail_events=json.dumps(log_events[did]))

    def finish(r: Mapping[str, Any], status: str, decision: str, approval: str, execution: str, reason: str | None = None):
        did = r["decision_id"]
        decided_at = _ts(r["decided_at"]) or now
        _set(wh, f"{ops}.APPROVALS", "decision_id", did, processed_at=now, processed_status=status,
             processed_decision=decision, last_seen_decision=decision)
        if did in log_by_id:
            if status != "APPROVED" and status != "REJECTED":
                note_event(did, status, **({"reason": reason} if reason else {}))
            _set(wh, f"{ops}.INTERVENTION_LOG", "decision_id", did, approval_status=approval,
                 execution_status=execution, approved_at=decided_at,
                 decided_by=_text(r["decided_by"]) or "unknown", approval_note=_text(r["note"]),
                 **({"outbox_id": did, "dispatched_at": now} if execution == "DISPATCHED" else {}))
        counts[status] += 1

    candidates: list[Mapping[str, Any]] = []
    with wh.transaction():
        for r in rows.to_dict("records"):
            did = r["decision_id"]
            decision = (_text(r["decision"]) or "").strip().upper() or "PENDING"
            last_seen = _text(r["last_seen_decision"])
            if pd.notna(r["processed_at"]):  # final: later edits change nothing
                if decision not in (_text(r["processed_decision"]), last_seen) and did in log_by_id:
                    note_event(did, "LATE_EDIT", value=decision)
                    _set(wh, f"{ops}.APPROVALS", "decision_id", did, last_seen_decision=decision)
                    counts["LATE_EDIT"] += 1
                continue
            log = log_by_id.get(did)
            if log is None or _text(log.payload_hash) != payload_hash(r):
                finish(r, "TAMPERED", decision, "TAMPERED", "NOT_DISPATCHED")
                continue
            expires_at = _ts(log.approval_expires_at)  # trusted copy
            expired = expires_at is not None and now >= expires_at
            if decision in ("APPROVED", "REJECTED"):
                if expired:
                    finish(r, "EXPIRED", decision, "EXPIRED", "NOT_DISPATCHED")
                elif decision == "REJECTED":
                    finish(r, "REJECTED", decision, "REJECTED", "NOT_DISPATCHED")
                else:
                    candidates.append(r)
            elif expired:
                finish(r, "EXPIRED", decision, "EXPIRED", "NOT_DISPATCHED")
            elif decision != "PENDING" and decision != last_seen:
                note_event(did, "INVALID_VALUE", value=decision)
                _set(wh, f"{ops}.APPROVALS", "decision_id", did, last_seen_decision=decision)
                counts["INVALID_VALUE"] += 1

        stale = inactive_subscribers(wh, settings, [r["subscriber_key"] for r in candidates], now)
        outbox: list[dict] = []
        for r in candidates:
            if r["subscriber_key"] in stale:
                finish(r, "CANCELLED_STALE", "APPROVED", "APPROVED", "CANCELLED_STALE", STALE_REASON)
                continue
            outbox.append({
                "outbox_id": r["decision_id"], "channel": r["channel"], "intervention_type": r["intervention_type"],
                "subject": r["subject"], "body": r["body"], "offer_code": _text(r["offer_code"]),
                "discount_pct": _plain(r["discount_pct"]), "escalation_reason": _text(r["escalation_reason"]),
                "subscriber_key": r["subscriber_key"], "created_at": now, "status": "READY", "cancelled_reason": None,
            })
            finish(r, "APPROVED", "APPROVED", "APPROVED", "DISPATCHED")
        if outbox:  # insert-only on outbox_id: an approval can never produce a second row
            wh.merge_frame(f"{ops}.OUTBOX", pd.DataFrame(outbox), keys=["outbox_id"], insert_only=True)
    return counts
