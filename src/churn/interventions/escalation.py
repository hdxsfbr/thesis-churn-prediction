"""Escalation: hand the subscriber to a human via the human_queue channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from churn.interventions.base import Action, Content, InterventionType

if TYPE_CHECKING:
    from churn.agent.context import AgentContext

COST_USD = 12.0  # estimated staff time for one follow-up
SUBJECT = "Retention follow-up needed: {{subscriber_key}}"
BODY = (
    "Subscriber {{subscriber_key}} is at {tier} churn risk ({risk:.2f}). Reason: {reason}. "
    "Priority: {priority}. Please follow up personally."
)


class Escalation(InterventionType):
    name = "escalation"
    channel = "human_queue"

    def _reason_priority(self, context: AgentContext) -> tuple[str, str]:
        if context.archetype_conf is not None and context.archetype_conf < 0.5 and context.tier == "critical":
            reason = "critical risk with uncertain archetype"
        else:
            reason = f"{context.tier} churn risk ({context.risk:.2f}), archetype {context.archetype}"
        if context.tier == "critical" or context.risk >= 0.85:
            priority = "urgent"
        elif context.tier == "high":
            priority = "high"
        else:
            priority = "normal"
        return reason, priority

    def action(self, context: AgentContext) -> Action:
        reason, priority = self._reason_priority(context)
        return Action(params={"priority": priority}, escalation_reason=reason)

    def template(self, context: AgentContext) -> Content:
        reason, priority = self._reason_priority(context)
        body = (
            BODY.replace("{{subscriber_key}}", "\0")
            .format(tier=context.tier, risk=context.risk, reason=reason, priority=priority)
            .replace("\0", "{{subscriber_key}}")
        )
        return Content(subject=SUBJECT, body=body, source="template", ref=f"escalation/{priority}")

    def cost_usd(self, context: AgentContext) -> float:
        return COST_USD
