"""A failing or budgeted model never blocks the cycle, and a rerun does not call it again."""

from datetime import date

from churn.llm.client import LLMConfig, LLMTimeout, override_llm
from churn.llm.fake import FakeLLM
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)


def _run(pipeline, fake, config, run_id):
    with override_llm(fake, config):
        run_cycle(pipeline["wh"], pipeline["settings"], AS_OF, run_id=run_id)


def test_llm_timeout_falls_back_to_template_and_logs_source(pipeline):
    fake = FakeLLM(error=LLMTimeout("too slow"))
    config = LLMConfig(max_calls_per_run=5, retries=3, concurrency=1)
    _run(pipeline, fake, config, "t15-timeout")
    ops = pipeline["settings"].schemas.ops
    log = pipeline["wh"].query(f"SELECT content_source FROM {ops}.INTERVENTION_LOG WHERE run_id = 't15-timeout'")
    assert not log.empty
    assert set(log["content_source"]) == {"template"}
    # one transport failure is retried three times; distinct prompts each spend that budget
    assert fake.calls >= 3 and fake.calls % 3 == 0


def test_budget_exhausted_uses_template(pipeline):
    fake = FakeLLM()
    config = LLMConfig(max_calls_per_run=1, retries=1, concurrency=1)
    _run(pipeline, fake, config, "t15-budget")
    ops = pipeline["settings"].schemas.ops
    log = pipeline["wh"].query(
        f"SELECT content_source FROM {ops}.INTERVENTION_LOG WHERE run_id = 't15-budget'"
    )
    assert fake.calls == 1
    assert "template" in set(log["content_source"])


def test_rerun_reuses_persisted_content_no_new_llm_calls(pipeline):
    fake = FakeLLM()
    config = LLMConfig(max_calls_per_run=50, retries=1, concurrency=2)
    _run(pipeline, fake, config, "t15-rerun")
    first = fake.calls
    assert first > 0
    _run(pipeline, fake, config, "t15-rerun")
    assert fake.calls == first
