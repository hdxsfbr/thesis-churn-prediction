"""Content for an intervention. Thin path: deterministic template only (LLM generation comes later)."""

from __future__ import annotations

from churn.agent.context import AgentContext
from churn.interventions.base import Content, InterventionType


def build_content(intervention: InterventionType, context: AgentContext) -> Content:
    content = intervention.template(context)
    if not content.subject.strip() or not content.body.strip():
        raise ValueError(f"intervention {intervention.name!r} produced empty content")
    return content
