"""Acceptance tests for T12: each guardrail removes the arm and records the reason."""

from datetime import datetime

from churn.agent.context import AgentContext
from churn.agent.guardrails import SubscriberFacts, filter_arms
from churn.interventions.base import load_registry
from churn.runtime_config import GuardrailConfig, defaults


def _cfg(**updates) -> GuardrailConfig:
    cfg = GuardrailConfig.model_validate(defaults()["guardrails"])
    return cfg.model_copy(update=updates) if updates else cfg


def _ctx(**kw) -> AgentContext:
    fields = {
        "run_id": "run", "subscriber_key": "s", "risk": 0.4, "tier": "medium",
        "as_of": datetime(2025, 6, 30), "archetype": "price_sensitive", "archetype_conf": 0.9,
    }
    fields.update(kw)
    return AgentContext(**fields)


def test_over_frequency_cap_blocked_with_reason():
    registry = load_registry()
    cfg = _cfg()
    for facts in (SubscriberFacts(contacts_14d=cfg.contact_cap_14d), SubscriberFacts(contacts_90d=cfg.contact_cap_90d)):
        kept, events = filter_arms(_ctx(), registry, cfg, facts)
        assert kept == {}
        assert events and {e["reason"] for e in events} == {"cap"}
        assert {e["arm"] for e in events} == set(registry)


def test_unsubscribed_gets_no_email_arm_escalation_still_allowed():
    registry = load_registry()
    kept, events = filter_arms(_ctx(), registry, _cfg(), SubscriberFacts(email_unsubscribed=True))
    assert set(kept) == {"escalation"}
    assert kept["escalation"].channel == "human_queue"
    removed = {e["arm"] for e in events}
    assert removed == set(registry) - {"escalation"}
    assert {e["reason"] for e in events} == {"unsubscribed"}


def test_offer_beyond_limit_clipped_or_blocked():
    registry = load_registry()
    legal = ("PCT_10_NEXT", "PCT_25_NEXT_2")
    ctx = _ctx(max_discount_pct=10, offer_catalogue=legal)
    kept, events = filter_arms(ctx, registry, _cfg(max_discount_pct=10, offer_catalogue=list(legal)), SubscriberFacts())
    assert "retention_offer" in kept
    assert not any(e["arm"] == "retention_offer" for e in events)
    assert kept["retention_offer"].action(ctx).discount_pct <= 10

    blocked = ["PCT_25_NEXT_2"]
    ctx = _ctx(max_discount_pct=10, offer_catalogue=tuple(blocked))
    kept, events = filter_arms(
        ctx, registry, _cfg(max_discount_pct=10, offer_catalogue=blocked), SubscriberFacts()
    )
    assert "retention_offer" not in kept
    assert any(e["arm"] == "retention_offer" and e["reason"] == "discount" for e in events)
    assert "checkin_email" in kept


def test_active_offer_blocks_new_offer():
    registry = load_registry()
    kept, events = filter_arms(_ctx(), registry, _cfg(), SubscriberFacts(active_offer=True))
    assert "retention_offer" not in kept
    assert "checkin_email" in kept and "escalation" in kept
    assert any(e["arm"] == "retention_offer" and e["reason"] == "active_offer" for e in events)


def test_pending_approval_blocks_duplicate():
    registry = load_registry()
    kept, events = filter_arms(_ctx(), registry, _cfg(), SubscriberFacts(pending_approval=True))
    assert kept == {}
    assert events and {e["reason"] for e in events} == {"DUPLICATE_PENDING"}
    assert {e["arm"] for e in events} == set(registry)
