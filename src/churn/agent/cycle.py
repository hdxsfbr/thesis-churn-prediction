"""Agent cycle: at-risk scores of one run -> deterministic decisions. Never writes the outbox."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime

import pandas as pd

from churn.agent.content import build_content
from churn.agent.context import PENDING, AgentContext, build_contexts
from churn.agent.gate import GateResult, apply_gate, cost_of
from churn.agent.guardrails import facts_for_contexts, filter_arms, gap_reason
from churn.agent.policy import PolicyConfig, PolicyContext, Selection, select
from churn.agent.rationale import build as build_rationale
from churn.execution.approvals import payload_hash
from churn.interventions.base import Action, Content, InterventionType, load_registry
from churn.runtime_config import RuntimeConfig, defaults
from churn.settings import Settings
from churn.warehouse.base import Warehouse

THIN_PATH_TYPE = "checkin_email"


def decision_id(run_id: str, subscriber_key: str) -> str:
    return hashlib.sha256(f"{run_id}|{subscriber_key}".encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    decision_id: str
    context: AgentContext
    intervention: InterventionType
    action: Action
    content: Content
    gate: GateResult | None = None  # None: not gated (autonomous)
    policy: Selection | None = None
    guardrail_events: tuple = ()  # removals that still left at least one arm

    @property
    def needs_approval(self) -> bool:
        return self.gate is not None and self.gate.needs_approval

    def payload(self) -> dict:
        """The proposed action: what the outbox would carry and what the approver sees. Hashed for tamper checks."""
        return {
            "subscriber_key": self.context.subscriber_key,
            "channel": self.intervention.channel,
            "intervention_type": self.intervention.name,
            "subject": self.content.subject,
            "body": self.content.body,
            "offer_code": self.action.offer_code,
            "discount_pct": self.action.discount_pct,
            "escalation_reason": self.action.escalation_reason,
            "cost_usd": cost_of(self),
        }

    def rationale(self) -> dict:
        return build_rationale(self)

    def log_row(self, decided_at: datetime, stale_reason: str | None = None) -> dict:
        """INTERVENTION_LOG row. ``stale_reason`` marks an autonomous decision dropped at dispatch."""
        c = self.context
        pending = self.needs_approval
        dropped = not pending and stale_reason is not None
        sent = not pending and not dropped
        events = list(self.guardrail_events)
        if dropped:
            events.append({"event": "CANCELLED_STALE", "reason": stale_reason, "at": decided_at.isoformat()})
        return {
            "decision_id": self.decision_id,
            "subscriber_key": c.subscriber_key,
            "run_id": c.run_id,
            "risk": c.risk,
            "tier": c.tier,
            "archetype": c.archetype,
            "archetype_conf": c.archetype_conf,
            "top_factors": json.dumps(c.top_factors),
            "intervention_type": self.intervention.name,
            "params": json.dumps(self.action.params, sort_keys=True),
            "content_ref": self.content.ref,
            "content_source": self.content.source,
            "rationale": json.dumps(self.rationale(), sort_keys=True),
            "policy_scores": json.dumps(self.policy.scores if self.policy else {}, sort_keys=True),
            "guardrail_events": json.dumps(events),
            "gate_reason": self.gate.text if self.gate else "autonomous: not gated",
            "approval_status": "PENDING" if pending else "NOT_REQUIRED",
            "execution_status": "PENDING_APPROVAL" if pending else "DISPATCHED" if sent else "CANCELLED_STALE",
            "outbox_id": self.decision_id if sent else None,
            "response": PENDING,
            "outcome": PENDING,
            "decided_at": decided_at,
            "approved_at": None,
            "dispatched_at": decided_at if sent else None,
            "payload_hash": payload_hash(self.payload()),
            "decided_by": None,
            "approval_note": None,
            "outcome_at": None,
        }

    def outbox_row(self, created_at: datetime) -> dict:
        return {
            "outbox_id": self.decision_id,
            "channel": self.intervention.channel,
            "intervention_type": self.intervention.name,
            "subject": self.content.subject,
            "body": self.content.body,
            "offer_code": self.action.offer_code,
            "discount_pct": self.action.discount_pct,
            "escalation_reason": self.action.escalation_reason,
            "subscriber_key": self.context.subscriber_key,
            "created_at": created_at,
            "status": "READY",
            "cancelled_reason": None,
        }


def decide(contexts: list[AgentContext], registry: dict[str, InterventionType],
           policy_config: PolicyConfig | None = None, stats: dict | None = None, *,
           arms_for=None) -> list[Decision]:
    """Stable key order and a hard per-run exploration budget over the subscribers who still have an arm.

    ``arms_for(context) -> (registry, removal events)`` drops ineligible arms first. An empty registry
    produces no decision (the caller writes the coverage gap).
    """
    cfg = policy_config or PolicyConfig(**defaults()['policy'])
    prepared = []
    for c in sorted(contexts, key=lambda c: (c.run_id, c.subscriber_key)):
        kept, removed = (registry, ()) if arms_for is None else arms_for(c)
        if not kept:
            continue
        low = (getattr(c, "archetype_low_conf", False) or c.archetype_conf is None
               or c.archetype_conf < cfg.low_confidence)
        if low and c.tier == "critical" and "escalation" not in kept:
            continue
        prepared.append((c, kept, removed))
    budgets = {r: math.floor(sum(c.run_id == r for c, _, _ in prepared) * cfg.max_explore_share)
               for r in {c.run_id for c, _, _ in prepared}}
    decisions = []
    for c, kept, removed in prepared:
        selection = select(c, kept, cfg, stats or {}, allow_explore=budgets[c.run_id] > 0)
        budgets[c.run_id] -= int(selection.explore)
        if removed:
            selection = replace(selection, removed=tuple(removed))
        intervention = kept[selection.chosen]
        decisions.append(Decision(decision_id(c.run_id, c.subscriber_key), c, intervention,
                                  intervention.action(c), build_content(intervention, c), policy=selection,
                                  guardrail_events=tuple(removed)))
    return decisions


def run_agent(wh: Warehouse, settings: Settings, run_id: str, cfg: RuntimeConfig) -> list[Decision]:
    """Read this run's scores, build contexts for the at-risk tiers, decide and gate (approval vs outbox)."""
    scores = wh.query(f"SELECT * FROM {settings.schemas.ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
    if cfg.agent_halt:
        return []
    contexts = build_contexts(scores, cfg.tiers.at_risk_tiers())
    # Reuse the same point-in-time feature builder as scoring; no raw identifiers enter policy.
    from churn.contract.loader import contract_for
    from churn.features.builder import build_features
    enriched = []
    feature_rows: dict[str, dict] = {}
    for as_of, group in scores.groupby('as_of'):
        features = build_features(wh, settings, contract_for(settings), pd.Timestamp(as_of).to_pydatetime())
        by_key = features.set_index('subscriber_key').to_dict('index')
        score_rows = group.set_index('subscriber_key').to_dict('index')
        for c in contexts:
            if c.subscriber_key not in score_rows:
                continue
            row, f = score_rows[c.subscriber_key], by_key.get(c.subscriber_key, {})
            data = asdict(c)
            data['archetype'] = row.get('archetype') or 'PENDING'
            conf = row.get('archetype_conf')
            data['archetype_conf'] = float(conf) if pd.notna(conf) else None
            low_conf = row.get('archetype_low_conf')
            data['archetype_low_conf'] = bool(low_conf) if pd.notna(low_conf) else False
            probs = row.get('archetype_probs')
            if cfg.guardrails is not None:
                data["max_discount_pct"] = cfg.guardrails.max_discount_pct
                data["offer_catalogue"] = tuple(cfg.guardrails.offer_catalogue)
            rate = f.get('email_open_rate_90')
            enriched.append(PolicyContext(**data, tenure_days=f.get('tenure_days', 0),
                email_open_rate=float(rate) if pd.notna(rate) else None,
                archetype_probs=json.loads(probs) if isinstance(probs, str) else {}))
            feature_rows[c.subscriber_key] = f
    rows = wh.query(f'SELECT * FROM {settings.schemas.ops}.POLICY_STATS')
    stats = {(r.archetype, r.intervention_type, r.context_bucket): (r.alpha, r.beta)
             for r in rows.itertuples(index=False)}
    registry = load_registry()
    facts = facts_for_contexts(wh, settings, enriched, feature_rows)
    events_by_key: dict[str, list] = {}

    def arms_for(ctx: AgentContext):
        kept, events = filter_arms(ctx, registry, cfg.guardrails, facts.get(ctx.subscriber_key))
        # select() raises when a critical low-confidence subscriber has no escalation arm left.
        # Drop the decision here so the subscriber is recorded as a coverage gap instead.
        threshold = cfg.policy.low_confidence if cfg.policy is not None else 1.0
        low = (getattr(ctx, "archetype_low_conf", False) or ctx.archetype_conf is None
               or ctx.archetype_conf < threshold)
        if low and ctx.tier == "critical" and "escalation" not in kept:
            events = [*events, {"event": "REMOVED", "arm": "escalation", "reason": "no_escalation"}]
            kept = {}
        events_by_key[ctx.subscriber_key] = events
        return kept, events

    decisions = decide(enriched, registry, cfg.policy, stats, arms_for=arms_for)
    decided = {d.context.subscriber_key for d in decisions}
    gaps = []
    for ctx in enriched:
        if ctx.subscriber_key in decided:
            continue
        events = events_by_key.get(ctx.subscriber_key, [])
        gaps.append({
            "run_id": ctx.run_id,
            "subscriber_key": ctx.subscriber_key,
            "reason": gap_reason(events),
            "guardrail_events": json.dumps(events),
            "created_at": ctx.as_of,
        })
    ops = settings.schemas.ops
    wh.execute(f"DELETE FROM {ops}.COVERAGE_GAPS WHERE run_id = :r", {"r": run_id})
    if gaps:
        wh.merge_frame(f"{ops}.COVERAGE_GAPS", pd.DataFrame(gaps), keys=["run_id", "subscriber_key"])
    return apply_gate(decisions, cfg)
