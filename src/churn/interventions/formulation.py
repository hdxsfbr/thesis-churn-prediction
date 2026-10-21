"""Formulation adjustment: suggest a blend change from quiz goals, else from purchase history (R4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from churn.interventions.base import Action, Content, InterventionType

if TYPE_CHECKING:
    from churn.agent.context import AgentContext

SUBJECT = "A blend tuned to what you want, {{first_name}}"
BODY = (
    "Hi {{first_name}},\n\n"
    "We think we can get your blend closer to what you are after{goal_clause}. "
    "{sku_clause}Reply to this email and we will adjust your next shipment.\n\n"
    "Warm regards,\nThe tea team"
)
COST_USD = 0.5  # email send plus a sample-sized formulation tweak


class Formulation(InterventionType):
    name = "formulation"
    channel = "email"

    def action(self, context: AgentContext) -> Action:
        if context.has_quiz:
            params = {
                "data_source": "quiz",
                "goals": list(context.quiz_goals),
                "recommended_sku": context.recommended_sku,
            }
        else:
            params = {
                "data_source": "purchase_history",
                "goals": [context.top_blend_goal] if context.top_blend_goal else [],
                "goal_email_engagement": context.goal_email_engagement,
            }
        return Action(params=params)

    def template(self, context: AgentContext) -> Content:
        params = self.action(context).params
        goals = params["goals"]
        goal_clause = f" ({', '.join(goals)})" if goals else ""
        sku = params.get("recommended_sku")
        sku_clause = f"Based on your quiz we suggest {sku}. " if sku else ""
        body = BODY.replace("{goal_clause}", goal_clause).replace("{sku_clause}", sku_clause)
        return Content(subject=SUBJECT, body=body, source="template", ref=f"formulation/{params['data_source']}")

    def cost_usd(self, context: AgentContext) -> float:
        return COST_USD
