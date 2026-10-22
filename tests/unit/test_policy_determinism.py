from test_policy_routing import config, context

from churn.agent.cycle import decide
from churn.interventions.base import load_registry


def test_same_run_and_key_same_choice():
    cs = [context(subscriber_key=str(i)) for i in range(30)]
    a = decide(cs, load_registry(), config())
    b = decide(list(reversed(cs)), dict(reversed(list(load_registry().items()))), config())
    assert [(d.decision_id, d.rationale()) for d in a] == [(d.decision_id, d.rationale()) for d in b]
