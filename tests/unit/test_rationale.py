import json

from test_policy_routing import config, context

from churn.agent.cycle import decide
from churn.interventions.base import load_registry


def test_every_decision_has_structured_and_text_rationale():
    for d in decide([context(), context(subscriber_key='critical', tier='critical', archetype_conf=.2)],
                    load_registry(), config()):
        r = json.loads(d.log_row(d.context.as_of)['rationale'])
        assert r['chosen'] == d.intervention.name
        assert r['eligible'] and r['utility_terms'] and r['summary']
        assert r['top_factors'] == d.context.top_factors
        assert r['gate'] == 'autonomous'
        assert json.loads(d.log_row(d.context.as_of)['policy_scores']) == r['utility_terms']
