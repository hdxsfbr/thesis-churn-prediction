"""T11 / R4.3: formulation uses quiz data when present, purchase history otherwise."""

from __future__ import annotations

from datetime import datetime

from churn.agent.context import AgentContext
from churn.interventions.formulation import Formulation


def ctx(**kw) -> AgentContext:
    base = {"run_id": "r1", "subscriber_key": "s1", "risk": 0.6, "tier": "high", "as_of": datetime(2025, 6, 30)}
    base.update(kw)
    return AgentContext(**base)


def test_with_quiz_uses_quiz_source():
    a = Formulation().action(
        ctx(has_quiz=True, quiz_goals=["sleep", "focus"], recommended_sku="SKU-SLEEP", top_blend_goal="energy")
    )
    assert a.params["data_source"] == "quiz"
    assert a.params["goals"] == ["sleep", "focus"]
    assert a.params["recommended_sku"] == "SKU-SLEEP"


def test_without_quiz_uses_purchase_history_source():
    context = ctx(has_quiz=False, top_blend_goal="energy", goal_email_engagement=0.4)
    a = Formulation().action(context)
    assert a.params["data_source"] == "purchase_history"
    assert a.params["goals"] == ["energy"]
    assert a.params["goal_email_engagement"] == 0.4
    assert "overall email engagement" in Formulation().template(context).body


def test_no_quiz_and_no_history_still_produces_action():
    a = Formulation().action(ctx())
    assert a.params["data_source"] == "purchase_history"
    assert a.params["goals"] == []
