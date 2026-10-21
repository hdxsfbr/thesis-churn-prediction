"""T11: the four intervention types are built from suitable profiles; offers respect the discount cap."""

from __future__ import annotations

from datetime import datetime

import pytest

from churn.agent.content import build_content
from churn.agent.context import AgentContext
from churn.interventions.base import load_registry

AS_OF = datetime(2025, 6, 30)


def ctx(**kw) -> AgentContext:
    base = {"run_id": "r1", "subscriber_key": "s1", "risk": 0.6, "tier": "high", "as_of": AS_OF}
    base.update(kw)
    return AgentContext(**base)


def test_registry_lists_four_types():
    assert set(load_registry()) == {"checkin_email", "formulation", "retention_offer", "escalation"}


def test_each_of_four_types_built_from_suitable_profile():
    reg = load_registry()
    profiles = {
        "checkin_email": ctx(archetype="fatigue"),
        "formulation": ctx(archetype="efficacy", has_quiz=True, quiz_goals=["sleep"], recommended_sku="SKU-SLEEP"),
        "retention_offer": ctx(archetype="price"),
        "escalation": ctx(archetype="life_change", risk=0.9, tier="critical"),
    }
    channels = {"checkin_email": "email", "formulation": "email", "retention_offer": "email",
                "escalation": "human_queue"}
    for name, c in profiles.items():
        t = reg[name]
        assert t.channel == channels[name]
        action = t.action(c)
        content = build_content(t, c)
        assert content.subject and content.body and content.ref.startswith(name)
        assert t.cost_usd(c) >= 0
        if name == "retention_offer":
            assert action.offer_code and action.discount_pct is not None
        if name == "escalation":
            assert action.escalation_reason
            assert action.params["priority"] in {"normal", "high", "urgent"}
        if name == "formulation":
            assert action.params["data_source"] == "quiz"


@pytest.mark.parametrize("cap", [0, 5, 10, 20, 25])
@pytest.mark.parametrize("risk", [0.2, 0.5, 0.95])
def test_offer_never_exceeds_max_discount(cap, risk):
    offer = load_registry()["retention_offer"]
    c = ctx(risk=risk, max_discount_pct=cap)
    action = offer.action(c)
    assert action.discount_pct is not None and action.discount_pct <= cap
    assert action.offer_code in c.offer_catalogue
    assert offer.cost_usd(c) >= 0


def test_offer_cap_clips_even_if_catalogue_entry_is_larger():
    offer = load_registry()["retention_offer"]
    c = ctx(risk=0.95, max_discount_pct=12, offer_catalogue=("PCT_25_NEXT_2",))
    assert offer.action(c).discount_pct == 12


def test_escalation_body_keeps_merge_token():
    esc = load_registry()["escalation"]
    body = esc.template(ctx(tier="critical", risk=0.9)).body
    assert "{{subscriber_key}}" in body


def test_offer_catalogue_of_unknown_codes_raises():
    offer = load_registry()["retention_offer"]
    c = ctx(offer_catalogue=("NOPE", "TYPO"))
    with pytest.raises(ValueError, match="NOPE"):
        offer.action(c)
