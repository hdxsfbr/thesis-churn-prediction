"""Check-in email: a soft, personal note asking how the subscriber is getting on."""

from __future__ import annotations

from typing import TYPE_CHECKING

from churn.interventions.base import Action, Content, InterventionType

if TYPE_CHECKING:
    from churn.agent.context import AgentContext

SUBJECT = "How are your blends going, {{first_name}}?"
BODY = (
    "Hi {{first_name}},\n\n"
    "We noticed it has been a little while and wanted to check in. How are your blends working for you? "
    "If the timing, the delivery schedule or the blends themselves are not quite right, "
    "just reply to this email and we will happily adjust your plan.\n\n"
    "Warm regards,\nThe tea team"
)


class CheckinEmail(InterventionType):
    name = "checkin_email"
    channel = "email"

    def action(self, context: AgentContext) -> Action:
        return Action(params={"suggestion": "cadence_check"})

    def template(self, context: AgentContext) -> Content:
        return Content(subject=SUBJECT, body=BODY, source="template", ref="checkin_email/default")
