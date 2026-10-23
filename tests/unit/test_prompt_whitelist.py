"""The prompt dataclass cannot carry identifiers, and a captured request holds no synthetic PII."""

from datetime import datetime

from churn.agent.content import PromptContext, prompt_context, render_prompt, resolve_contents
from churn.agent.context import AgentContext
from churn.interventions.base import Action, load_registry
from churn.llm.client import LLMConfig, override_llm
from churn.llm.fake import FakeLLM

_FORBIDDEN = {
    "customer_id", "subscriber_key", "email", "phone", "first_name", "last_name",
    "address", "name", "body", "prompt", "free_text", "text",
}


def test_prompt_context_has_no_free_form_or_id_fields():
    fields = PromptContext.__dataclass_fields__
    assert not (set(fields) & _FORBIDDEN)
    for field in fields.values():
        annotation = str(field.type)
        assert "dict" not in annotation and "Any" not in annotation and "Mapping" not in annotation
    ctx = prompt_context(
        AgentContext(
            run_id="r", subscriber_key="secret-key", risk=0.4, tier="medium",
            as_of=datetime(2025, 6, 30), archetype="fatigued", top_factors=[{"label": "Opens down"}],
            quiz_goals=["sleep"], recommended_sku="calm-blend",
        ),
        Action(offer_code=None),
    )
    rendered = render_prompt(ctx)
    assert "secret-key" not in rendered
    assert "subscriber_key" not in rendered
    assert "customer_id" not in rendered


def test_no_raw_pii_fields_in_llm_request(pipeline):
    wh = pipeline["wh"]
    pii = wh.query("SELECT first_name, email, phone, address FROM THESIS_SYNTH.customers_pii")
    fake = FakeLLM()
    context = AgentContext(
        run_id="r", subscriber_key="not-sent", risk=0.4, tier="high", as_of=datetime(2025, 6, 30),
        archetype="price_sensitive", top_factors=[{"label": "Orders slowed"}],
        quiz_goals=["focus"], recommended_sku="focus-blend",
    )
    action = Action(offer_code="PCT_10_NEXT", discount_pct=10)
    with override_llm(fake, LLMConfig(max_calls_per_run=5, retries=1)):
        resolve_contents([(load_registry()["retention_offer"], context, action, "d1")], stored={}, settings=pipeline["settings"])
    assert fake.requests
    blob = "\n".join(fake.requests).lower()
    for column in ("first_name", "email", "phone", "address"):
        for value in pii[column].dropna().astype(str).unique():
            assert value.lower() not in blob, value
