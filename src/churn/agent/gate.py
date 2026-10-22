"""Gate: which decisions need a human (R5.1). Pure; reads thresholds from the run's config snapshot.

needs_approval = risk >= T_risk or cost >= T_cost or discount_pct >= T_discount or type in always_approve.
In approve_all mode (gate config missing or invalid) every decision goes to approval.
Escalation is itself a human hand-off (channel human_queue), so thresholds do not apply to it;
always_approve and approve_all still do.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from churn.runtime_config import RuntimeConfig

if TYPE_CHECKING:
    from churn.agent.cycle import Decision

HUMAN_QUEUE = "human_queue"
DEFAULT_GRACE_H = 24.0  # used only when the guardrails section is unavailable
DEFAULT_EXPIRY_H = 72.0  # ARCHITECTURE A6; used only when the guardrails section is unavailable


@dataclass(frozen=True)
class GateResult:
    needs_approval: bool
    reasons: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    expiry_h: float = DEFAULT_EXPIRY_H
    grace_h: float = DEFAULT_GRACE_H

    @property
    def text(self) -> str:
        if self.needs_approval:
            return "approval: " + ", ".join(self.reasons)
        return "autonomous: below approval thresholds"


def cost_of(decision: Decision) -> float:
    """Cost of the proposed action. Types without a cost function (T11 adds them) cost nothing."""
    fn = getattr(decision.intervention, "cost_usd", None)
    return float(fn(decision.action)) if callable(fn) else 0.0


def evaluate(decision: Decision, cfg: RuntimeConfig) -> GateResult:
    cost = cost_of(decision)
    expiry = cfg.guardrails.approval_expiry_h if cfg.guardrails else DEFAULT_EXPIRY_H
    grace = cfg.guardrails.approval_grace_h if cfg.guardrails else DEFAULT_GRACE_H
    gate = cfg.gate
    if cfg.gate_mode == "approve_all" or gate is None:
        return GateResult(True, ["approve_all: gate config invalid"], cost, expiry, grace)
    reasons: list[str] = []
    if decision.intervention.name in gate.always_approve:
        reasons.append(f"always_approve: {decision.intervention.name}")
    if decision.intervention.channel != HUMAN_QUEUE:
        if decision.context.risk >= gate.t_risk:
            reasons.append(f"risk {decision.context.risk:.2f} >= {gate.t_risk}")
        if cost >= gate.t_cost_usd:
            reasons.append(f"cost {cost:.2f} >= {gate.t_cost_usd}")
        discount = decision.action.discount_pct
        if discount is not None and discount >= gate.t_discount_pct:
            reasons.append(f"discount {discount:g}% >= {gate.t_discount_pct}%")
    return GateResult(bool(reasons), reasons, cost, expiry, grace)


def apply_gate(decisions: list[Decision], cfg: RuntimeConfig) -> list[Decision]:
    """Attach the gate result to every decision."""
    return [replace(d, gate=evaluate(d, cfg)) for d in decisions]
