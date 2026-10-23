"""Contextual Thompson policy. No warehouse writes or process-global random state."""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, model_validator

from churn.agent.context import AgentContext
from churn.interventions.base import load_registry


class PolicyConfig(BaseModel):
    max_explore_share: float = Field(ge=0, le=1)
    low_confidence: float = Field(ge=0, le=1)
    unknown_email_reach: float = Field(ge=0, le=1)
    value_by_tenure: dict[str, float]
    priors: dict[str, dict[str, tuple[float, float]]]
    decay: float = Field(default=1.0, gt=0, le=1)  # 1 keeps every outcome; lower shrinks older evidence

    @model_validator(mode='after')
    def check(self):
        if set(self.value_by_tenure) != {'<90', '90-365', '>365'}:
            raise ValueError('all tenure values required')
        if not all(math.isfinite(v) and v >= 0 for v in self.value_by_tenure.values()):
            raise ValueError('invalid margin')
        # Every enabled arm needs an explicit fallback, including unseen archetypes.
        missing = set(load_registry()) - self.priors.get('default', {}).keys()
        if missing:
            raise ValueError(f'default priors required for enabled arms: {sorted(missing)}')
        for arms in self.priors.values():
            for pair in arms.values():
                if not all(math.isfinite(v) and v > 0 for v in pair):
                    raise ValueError('Beta parameters must be positive and finite')
        return self


@dataclass(frozen=True)
class PolicyContext(AgentContext):
    tenure_days: float = 0
    email_open_rate: float | None = None
    archetype_probs: dict[str, float] = field(default_factory=dict)
    archetype_low_conf: bool = False


@dataclass(frozen=True)
class Selection:
    chosen: str
    scores: dict
    mean_winner: str
    sampled_winner: str
    explore: bool
    forced: bool = False
    removed: tuple = ()


def tenure_band(days):
    return '<90' if days < 90 else '90-365' if days <= 365 else '>365'


def select(context, registry, cfg, stats, *, allow_explore=True):
    """Registry contains eligible arms only. Missing observations use configured priors."""
    if not registry:
        raise ValueError('no eligible interventions')
    band = tenure_band(getattr(context, 'tenure_days', 0))
    bucket = f'{context.tier}:{band}'
    low = (getattr(context, 'archetype_low_conf', False)
           or context.archetype_conf is None or context.archetype_conf < cfg.low_confidence)
    weights = {context.archetype: 1.0}
    probs = getattr(context, 'archetype_probs', {})
    if low and probs:
        top = sorted(probs.items(), key=lambda p: (-p[1], p[0]))[:2]
        total = sum(v for _, v in top)
        if total > 0:
            weights = {k: v / total for k, v in top}
    scores = {}
    for name, arm in sorted(registry.items()):
        seed = hashlib.sha256(f'{context.run_id}|{context.subscriber_key}|{name}'.encode()).digest()
        rng = random.Random(int.from_bytes(seed, 'big'))
        sampled = mean = 0.0
        posteriors = {}
        for archetype, weight in sorted(weights.items()):
            prior = cfg.priors.get(archetype, {}).get(name)
            if prior is None:
                prior = cfg.priors.get('default', {}).get(name)
            if prior is None:
                raise ValueError(f'configured prior required for arm: {name}')
            alpha, beta = stats.get((archetype, name, bucket), prior)
            if not all(math.isfinite(v) and v > 0 for v in (alpha, beta)):
                raise ValueError('invalid POLICY_STATS posterior')
            sampled += weight * rng.betavariate(alpha, beta)
            mean += weight * alpha / (alpha + beta)
            posteriors[archetype] = [alpha, beta]
        rate = getattr(context, 'email_open_rate', None)
        reach = (cfg.unknown_email_reach if rate is None else max(0., min(1., rate))) if arm.channel == 'email' else 1.
        value = cfg.value_by_tenure[band]
        cost = arm.cost_usd(context)
        scores[name] = {'reach': reach, 'uplift': sampled, 'mean_uplift': mean, 'risk': context.risk,
                        'value': value, 'cost': cost, 'bucket': bucket, 'weights': weights,
                        'posteriors': posteriors, 'utility': reach * sampled * context.risk * value - cost,
                        'mean_utility': reach * mean * context.risk * value - cost}
    mean_winner = max(sorted(scores), key=lambda n: scores[n]['mean_utility'])
    sampled_winner = max(sorted(scores), key=lambda n: scores[n]['utility'])
    forced = low and context.tier == 'critical'
    if forced and 'escalation' not in registry:
        raise ValueError('critical uncertain context requires eligible escalation')
    chosen = 'escalation' if forced else sampled_winner if allow_explore else mean_winner
    return Selection(chosen, scores, mean_winner, sampled_winner,
                     not forced and chosen != mean_winner, forced)
