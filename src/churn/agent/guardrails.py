"""Eligibility filter (R4.6). Removes arms before the policy runs, and re-checks an approval at execution.

Rules come from the run's pinned guardrail config. Contact history is counted from INTERVENTION_LOG rows
with execution_status DISPATCHED or PENDING_APPROVAL, in the 14- and 90-day windows ending at the score's
``as_of``. A contact dated after that ``as_of`` does not exist yet for this decision, so a replay stays stable.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from churn.agent.context import AgentContext
from churn.features.builder import build_features
from churn.interventions.base import InterventionType
from churn.interventions.retention_offer import OFFERS
from churn.runtime_config import GuardrailConfig, defaults, load_pinned
from churn.settings import Settings
from churn.warehouse.base import Warehouse

CONTACT_STATUSES = {"DISPATCHED", "PENDING_APPROVAL"}
WINDOW_14 = timedelta(days=14)
WINDOW_90 = timedelta(days=90)
CLOSED_OUTCOMES = {"retained", "churned"}


def as_naive(value: Any) -> datetime | None:
    """UTC-naive timestamp, or None for nulls."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.to_pydatetime()


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    out = frame.copy()
    out.columns = [str(c).lower() for c in out.columns]
    return out.to_dict("records")


def _when(row: dict) -> datetime | None:
    return as_naive(row.get("dispatched_at")) or as_naive(row.get("decided_at"))


def contact_counts(rows: list[dict], as_of: datetime, exclude_decision_id: str | None = None) -> tuple[int, int]:
    """(contacts in 14 days, contacts in 90 days) ending at ``as_of``, excluding one decision."""
    end = as_naive(as_of)
    if end is None:
        return 0, 0
    n14 = n90 = 0
    for row in rows:
        if exclude_decision_id and row.get("decision_id") == exclude_decision_id:
            continue
        if str(row.get("execution_status") or "") not in CONTACT_STATUSES:
            continue
        ts = _when(row)
        if ts is None or ts > end:
            continue
        if ts >= end - WINDOW_90:
            n90 += 1
        if ts >= end - WINDOW_14:
            n14 += 1
    return n14, n90


def offer_outstanding(rows: list[dict], as_of: datetime) -> bool:
    """A retention offer already dispatched or pending at ``as_of`` and not yet closed."""
    end = as_naive(as_of)
    if end is None:
        return False
    for row in rows:
        if row.get("intervention_type") != "retention_offer":
            continue
        if str(row.get("execution_status") or "") not in CONTACT_STATUSES:
            continue
        outcome = row.get("outcome")
        if isinstance(outcome, str) and outcome.lower() in CLOSED_OUTCOMES:
            continue
        ts = _when(row)
        if ts is not None and ts <= end:
            return True
    return False


def approval_pending(rows: list[dict], as_of: datetime) -> bool:
    """An unprocessed PENDING approval whose created_at is on or before ``as_of``."""
    end = as_naive(as_of)
    if end is None:
        return False
    for row in rows:
        if str(row.get("decision") or "").strip().upper() != "PENDING":
            continue
        if as_naive(row.get("processed_at")) is not None:
            continue
        created = as_naive(row.get("created_at"))
        if created is not None and created <= end:
            return True
    return False


class SubscriberFacts:
    """What the filter needs about one subscriber, already resolved at a single ``as_of``."""

    __slots__ = ("active_offer", "contacts_14d", "contacts_90d", "email_unsubscribed", "pending_approval")

    def __init__(
        self,
        email_unsubscribed: bool = False,
        contacts_14d: int = 0,
        contacts_90d: int = 0,
        pending_approval: bool = False,
        active_offer: bool = False,
    ) -> None:
        self.email_unsubscribed = email_unsubscribed
        self.contacts_14d = contacts_14d
        self.contacts_90d = contacts_90d
        self.pending_approval = pending_approval
        self.active_offer = active_offer


def _unsubscribed(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(int(value))


def _event(arm: str, reason: str) -> dict:
    return {"event": "REMOVED", "arm": arm, "reason": reason}


def offer_has_no_legal_code(guardrails: GuardrailConfig) -> bool:
    """True when every catalogue code this type knows is above ``max_discount_pct``."""
    legal = [c for c in guardrails.offer_catalogue if c in OFFERS and OFFERS[c][0] <= guardrails.max_discount_pct]
    return not legal


def filter_arms(
    context: AgentContext,
    registry: dict[str, InterventionType],
    guardrails: GuardrailConfig | None,
    facts: SubscriberFacts | None,
) -> tuple[dict[str, InterventionType], list[dict]]:
    """Return the arms still allowed, and one removal event per arm that was dropped.

    ``guardrails is None`` leaves the registry unchanged (the agent has already halted when the section is invalid).
    """
    if guardrails is None:
        return dict(registry), []
    facts = facts or SubscriberFacts()
    names = sorted(registry)
    if facts.pending_approval:
        return {}, [_event(name, "DUPLICATE_PENDING") for name in names]
    over_cap = (
        facts.contacts_14d >= guardrails.contact_cap_14d or facts.contacts_90d >= guardrails.contact_cap_90d
    )
    if over_cap:
        return {}, [_event(name, "cap") for name in names]
    block_offer = facts.active_offer or offer_has_no_legal_code(guardrails)
    offer_reason = "active_offer" if facts.active_offer else "discount"
    kept: dict[str, InterventionType] = {}
    removed: list[dict] = []
    for name in names:
        arm = registry[name]
        if facts.email_unsubscribed and arm.channel == "email":
            removed.append(_event(name, "unsubscribed"))
            continue
        if name == "retention_offer" and block_offer:
            removed.append(_event(name, offer_reason))
            continue
        kept[name] = arm
    return kept, removed


def gap_reason(events: list[dict]) -> str:
    """One reason for the coverage-gap row. Precedence matches the filter."""
    reasons = {e["reason"] for e in events}
    if "DUPLICATE_PENDING" in reasons:
        return "DUPLICATE_PENDING"
    if "cap" in reasons:
        return "cap"
    if reasons == {"unsubscribed"}:
        return "unsubscribed+no channel"
    if reasons == {"active_offer"}:
        return "active_offer"
    if reasons == {"discount"}:
        return "discount"
    if not reasons:
        return "no_eligible_arm"
    return "+".join(sorted(reasons))


def facts_for_contexts(
    wh: Warehouse, settings: Settings, contexts: list[AgentContext], feature_rows: dict[str, dict]
) -> dict[str, SubscriberFacts]:
    """One ``SubscriberFacts`` per subscriber, from the log, the approval queue and the feature row."""
    ops = settings.schemas.ops
    log = _records(wh.query(
        f"SELECT decision_id, subscriber_key, intervention_type, execution_status, decided_at, dispatched_at, outcome "
        f"FROM {ops}.INTERVENTION_LOG"
    ))
    approvals = _records(wh.query(
        f"SELECT subscriber_key, decision, processed_at, created_at FROM {ops}.APPROVALS"
    ))
    by_sub: dict[str, list[dict]] = defaultdict(list)
    by_approval: dict[str, list[dict]] = defaultdict(list)
    for row in log:
        by_sub[row.get("subscriber_key")].append(row)
    for row in approvals:
        by_approval[row.get("subscriber_key")].append(row)
    facts: dict[str, SubscriberFacts] = {}
    for ctx in contexts:
        history = by_sub.get(ctx.subscriber_key, [])
        n14, n90 = contact_counts(history, ctx.as_of)
        feat = feature_rows.get(ctx.subscriber_key, {})
        facts[ctx.subscriber_key] = SubscriberFacts(
            email_unsubscribed=_unsubscribed(feat.get("email_unsubscribed")),
            contacts_14d=n14,
            contacts_90d=n90,
            pending_approval=approval_pending(by_approval.get(ctx.subscriber_key, []), ctx.as_of),
            active_offer=offer_outstanding(history, ctx.as_of),
        )
    return facts


def execution_block(channel: str, facts: SubscriberFacts, guardrails: GuardrailConfig) -> str | None:
    """Why an already-approved action must not be sent, or None when it still may.

    Only the contact cap and email unsubscribe are re-checked here (ARCHITECTURE §6.5). The decision
    being executed is not counted as a contact against itself.
    """
    over_cap = facts.contacts_14d >= guardrails.contact_cap_14d or facts.contacts_90d >= guardrails.contact_cap_90d
    if over_cap:
        return "cap"
    if channel == "email" and facts.email_unsubscribed:
        return "unsubscribed"
    return None


def _guardrails_for(wh: Warehouse, settings: Settings, run_id: str) -> GuardrailConfig:
    pinned = load_pinned(wh, settings, run_id)
    if pinned.guardrails is not None:
        return pinned.guardrails
    return GuardrailConfig.model_validate(defaults()["guardrails"])


def blocked_approvals(wh: Warehouse, settings: Settings, candidates: list[dict], now: datetime) -> dict[str, str]:
    """decision_id -> reason, for APPROVED rows the cap or an email unsubscribe now forbids."""
    if not candidates:
        return {}
    ops = settings.schemas.ops
    log = _records(wh.query(
        f"SELECT decision_id, subscriber_key, intervention_type, execution_status, decided_at, dispatched_at, outcome "
        f"FROM {ops}.INTERVENTION_LOG"
    ))
    scores = _records(wh.query(f"SELECT run_id, subscriber_key, as_of FROM {ops}.RISK_SCORES"))
    as_of_of = {(r["run_id"], r["subscriber_key"]): as_naive(r.get("as_of")) for r in scores}
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for row in log:
        by_sub[row["subscriber_key"]].append(row)
    from churn.contract.loader import contract_for

    features = build_features(wh, settings, contract_for(settings), now)
    unsub: dict[str, bool] = {}
    if not features.empty and "email_unsubscribed" in features.columns and "subscriber_key" in features.columns:
        for rec in features[["subscriber_key", "email_unsubscribed"]].to_dict("records"):
            unsub[rec["subscriber_key"]] = _unsubscribed(rec["email_unsubscribed"])
    configs: dict[str, GuardrailConfig] = {}
    blocked: dict[str, str] = {}
    for row in candidates:
        run_id = row["run_id"]
        if run_id not in configs:
            configs[run_id] = _guardrails_for(wh, settings, run_id)
        as_of = as_of_of.get((run_id, row["subscriber_key"])) or as_naive(now)
        history = by_sub.get(row["subscriber_key"], [])
        n14, n90 = contact_counts(history, as_of, exclude_decision_id=row["decision_id"]) if as_of else (0, 0)
        facts = SubscriberFacts(
            email_unsubscribed=unsub.get(row["subscriber_key"], False),
            contacts_14d=n14,
            contacts_90d=n90,
        )
        reason = execution_block(str(row.get("channel") or ""), facts, configs[run_id])
        if reason:
            blocked[row["decision_id"]] = reason
    return blocked
