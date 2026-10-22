"""Agent cycle: at-risk scores of one run -> deterministic decisions. Never writes the outbox."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from churn.agent.content import build_content
from churn.agent.context import PENDING, AgentContext, build_contexts
from churn.agent.gate import GateResult, apply_gate, cost_of
from churn.execution.approvals import payload_hash
from churn.interventions.base import Action, Content, InterventionType, RegistryError, load_registry
from churn.runtime_config import RuntimeConfig
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
        c = self.context
        return {
            "top_factors": c.top_factors,
            "archetype": c.archetype,
            "archetype_conf": c.archetype_conf,
            "tier": c.tier,
            "risk": c.risk,
            "eligible": [self.intervention.name],
            "removed": [],
            "chosen": self.intervention.name,
            "explore": False,
            "gate": "approval" if self.needs_approval else "autonomous",
            "summary": f"{c.tier} risk ({c.risk:.2f}); thin path sends {self.intervention.name}",
        }

    def log_row(self, decided_at: datetime, stale_reason: str | None = None) -> dict:
        """INTERVENTION_LOG row. ``stale_reason`` marks an autonomous decision dropped at dispatch."""
        c = self.context
        pending = self.needs_approval
        dropped = not pending and stale_reason is not None
        sent = not pending and not dropped
        events = [{"event": "CANCELLED_STALE", "reason": stale_reason, "at": decided_at.isoformat()}] if dropped else []
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
            "policy_scores": json.dumps({}),
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


def decide(contexts: list[AgentContext], registry: dict[str, InterventionType]) -> list[Decision]:
    """Thin path: every at-risk subscriber gets the check-in email. Pure and deterministic."""
    if THIN_PATH_TYPE not in registry:
        raise RegistryError(f"thin path requires intervention type '{THIN_PATH_TYPE}' in config/interventions.yaml")
    intervention = registry[THIN_PATH_TYPE]
    return [
        Decision(
            decision_id=decision_id(c.run_id, c.subscriber_key),
            context=c,
            intervention=intervention,
            action=intervention.action(c),
            content=build_content(intervention, c),
        )
        for c in contexts
    ]


def run_agent(wh: Warehouse, settings: Settings, run_id: str, cfg: RuntimeConfig) -> list[Decision]:
    """Read this run's scores, build contexts for the at-risk tiers, decide and gate (approval vs outbox)."""
    scores = wh.query(f"SELECT * FROM {settings.schemas.ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
    contexts = build_contexts(scores, cfg.tiers.at_risk_tiers())
    return apply_gate(decide(contexts, load_registry()), cfg)
