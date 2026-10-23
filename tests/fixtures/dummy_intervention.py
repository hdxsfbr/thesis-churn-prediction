"""A throwaway email arm used only to prove a type can be added through config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from churn.interventions.base import Action, Content, InterventionType

if TYPE_CHECKING:
    from churn.agent.context import AgentContext


class DummyEmail(InterventionType):
    name = "dummy_email"
    channel = "email"

    def action(self, context: AgentContext) -> Action:
        return Action(params={"note": "config-only"})

    def template(self, context: AgentContext) -> Content:
        return Content(
            subject="A note for {{first_name}}",
            body="Hello {{first_name}}. This is the config-only arm.",
            source="template",
            ref="dummy_email/default",
        )
