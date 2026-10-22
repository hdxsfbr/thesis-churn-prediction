import json
from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from churn.agent.cycle import decide, run_agent
from churn.agent.policy import PolicyConfig, PolicyContext, select
from churn.interventions.base import load_registry
from churn.runtime_config import defaults, load_pinned, parse, seed
from churn.warehouse.migrate import apply_migrations


def context(**kw):
    return replace(PolicyContext(run_id='run', subscriber_key='key', risk=.6, tier='high',
        as_of=datetime(2025, 1, 1), archetype='price_sensitive', archetype_conf=.9,
        tenure_days=180, email_open_rate=.8), **kw)


def config(**kw):
    return PolicyConfig(**(defaults()['policy'] | kw))


def test_labelled_routing_scenarios_pick_expected_intervention():
    for archetype, tenure, expected in [('price_sensitive', 180, 'retention_offer'),
        ('efficacy_dissatisfied', 180, 'formulation'), ('fatigued', 180, 'checkin_email'),
        ('life_change', 500, 'escalation')]:
        assert select(context(archetype=archetype, tenure_days=tenure), load_registry(), config(), {}).chosen == expected


def test_same_score_different_archetype_or_tenure_different_action():
    choices = [select(context(archetype='life_change', tenure_days=d), load_registry(), config(), {}).chosen
               for d in [30, 500]]
    assert choices == ['checkin_email', 'escalation']


def test_low_confidence_critical_forces_escalation():
    result = select(context(tier='critical', archetype_conf=.2), load_registry(), config(), {})
    assert result.chosen == 'escalation'
    assert result.forced
    assert not result.explore


def test_not_a_score_threshold_rule():
    assert {select(context(risk=p), load_registry(), config(), {}).chosen for p in [.44, .46, .69, .71]} == {'retention_offer'}


def test_explore_share_bounded():
    contexts = [context(subscriber_key=str(i)) for i in range(200)]
    stats = {('price_sensitive', arm, 'high:90-365'): (1., 1.) for arm in load_registry()}
    decisions = decide(contexts, load_registry(), config(max_explore_share=.1), stats)
    assert sum(d.policy.explore for d in decisions) <= 20
    assert sum(d.policy.sampled_winner != d.policy.mean_winner for d in decisions) > 20
    assert not any(d.policy.explore for d in decide(contexts, load_registry(), config(max_explore_share=0), stats))


def test_mixture_uses_top_two_and_utility_terms():
    c = context(archetype_conf=.4, archetype_probs={'price_sensitive': .4, 'life_change': .35, 'fatigued': .25})
    result = select(c, load_registry(), config(), {})
    for terms in result.scores.values():
        assert terms['weights'] == {'price_sensitive': .4/.75, 'life_change': .35/.75}
        assert abs(terms['utility'] - (terms['reach'] * terms['uplift'] * c.risk * terms['value'] - terms['cost'])) < 1e-9


def test_policy_config_validated_and_pinned(fresh_wh, settings):
    apply_migrations(fresh_wh, settings)
    seed(fresh_wh, settings)
    first = load_pinned(fresh_wh, settings, 'pin-policy')
    fresh_wh.execute("UPDATE CHURN_OPS.AGENT_CONFIG SET value_json = '{}' WHERE key = 'policy'")
    assert load_pinned(fresh_wh, settings, 'pin-policy').policy == first.policy
    assert load_pinned(fresh_wh, settings, 'bad-policy').agent_halt
    rows = {k: json.dumps(v) for k, v in defaults().items() if k != 'policy'}
    assert parse(rows).agent_halt


@pytest.mark.parametrize('tier', ['critical', 'high'])
@pytest.mark.parametrize('confidence', [.52, .50])
def test_run_agent_honors_scored_uncertainty(fresh_wh, settings, monkeypatch, tier, confidence):
    apply_migrations(fresh_wh, settings)
    seed(fresh_wh, settings)
    c = context(tier=tier, archetype_conf=confidence)
    probs = {'price_sensitive': confidence, 'life_change': 1 - confidence}
    fresh_wh.merge_frame('CHURN_OPS.RISK_SCORES', pd.DataFrame([{
        'run_id': c.run_id, 'subscriber_key': c.subscriber_key, 'as_of': c.as_of,
        'churn_prob': c.risk, 'tier': tier, 'top_factors': '[]',
        'archetype': c.archetype, 'archetype_conf': confidence,
        'archetype_probs': json.dumps(probs), 'archetype_low_conf': True,
    }]), keys=['run_id', 'subscriber_key'])
    from churn.features import builder
    monkeypatch.setattr(builder, 'build_features', lambda *a, **kw: pd.DataFrame([
        {'subscriber_key': c.subscriber_key, 'tenure_days': 180, 'email_open_rate_90': .8}]))
    cfg = load_pinned(fresh_wh, settings, c.run_id)
    cfg.policy.low_confidence = .1  # must not override the classifier's margin flag
    decision = run_agent(fresh_wh, settings, c.run_id, cfg)[0]
    if tier == 'critical':
        assert decision.policy.forced
        assert decision.intervention.name == 'escalation'
        assert not decision.policy.explore
    else:
        assert not decision.policy.forced
    for terms in decision.policy.scores.values():
        assert terms['weights'] == probs


@pytest.mark.parametrize('missing', ['all', 'arm'])
def test_incomplete_priors_halt_runtime(missing):
    raw = defaults()
    if missing == 'all':
        raw['policy']['priors'] = {}
    else:
        for arms in raw['policy']['priors'].values():
            arms.pop('formulation')
    cfg = parse({k: json.dumps(v) for k, v in raw.items()})
    assert cfg.agent_halt
    assert cfg.policy is None
    assert any(error.startswith('policy:') for error in cfg.errors)


def test_explicit_default_prior_fills_missing_archetype_arm():
    raw = defaults()
    del raw['policy']['priors']['price_sensitive']['formulation']
    raw['policy']['priors']['default']['formulation'] = [23, 77]
    cfg = parse({k: json.dumps(v) for k, v in raw.items()})
    assert cfg.valid and not cfg.agent_halt
    result = select(context(), load_registry(), cfg.policy, {})
    assert result.scores['formulation']['posteriors']['price_sensitive'] == [23, 77]
    result = select(context(archetype='unknown'), load_registry(), cfg.policy, {})
    assert result.scores['formulation']['posteriors']['unknown'] == [23, 77]


def test_run_agent_reads_posteriors_scores_and_features(fresh_wh, settings, monkeypatch):
    apply_migrations(fresh_wh, settings)
    seed(fresh_wh, settings)
    c = context()
    fresh_wh.merge_frame('CHURN_OPS.RISK_SCORES', pd.DataFrame([{
        'run_id': c.run_id, 'subscriber_key': c.subscriber_key, 'as_of': c.as_of,
        'churn_prob': c.risk, 'tier': c.tier, 'top_factors': '[]',
        'archetype': c.archetype, 'archetype_conf': c.archetype_conf,
        'archetype_probs': json.dumps({c.archetype: 1.}),
    }]), keys=['run_id', 'subscriber_key'])
    from churn.features import builder
    monkeypatch.setattr(builder, 'build_features', lambda *a, **kw: pd.DataFrame([
        {'subscriber_key': c.subscriber_key, 'tenure_days': 180, 'email_open_rate_90': .8}]))
    for arm in load_registry():
        fresh_wh.execute('INSERT INTO CHURN_OPS.POLICY_STATS '
            '(archetype, intervention_type, context_bucket, alpha, beta) VALUES (:a, :i, :b, :x, :y)',
            {'a': c.archetype, 'i': arm, 'b': 'high:90-365',
             'x': 10000 if arm == 'formulation' else 1, 'y': 1 if arm == 'formulation' else 10000})
    decision = run_agent(fresh_wh, settings, c.run_id, load_pinned(fresh_wh, settings, c.run_id))[0]
    assert decision.intervention.name == 'formulation'  # overrides the price -> offer prior
    assert decision.context.archetype == c.archetype
    assert decision.policy.scores['formulation']['reach'] == .8
    assert decision.policy.scores['formulation']['bucket'] == 'high:90-365'
