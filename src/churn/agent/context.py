"""AgentContext: everything the agent knows about one at-risk subscriber for one run."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

# Note: TASKS T10 builds the context from scores and features. The thin path only fills template content,
# which needs scores alone, so features are intentionally not read here; they arrive with the policy work.
PENDING = "PENDING"


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    subscriber_key: str
    risk: float
    tier: str
    as_of: datetime
    top_factors: list[dict] = field(default_factory=list)
    archetype: str = PENDING  # no archetype is scored yet; filled in by the archetype ticket
    archetype_conf: float | None = None

    @property
    def factor_labels(self) -> list[str]:
        return [str(f.get("label", f.get("feature", ""))) for f in self.top_factors]


def build_contexts(scores: pd.DataFrame, at_risk_tiers: list[str]) -> list[AgentContext]:
    """One context per at-risk row of the run's RISK_SCORES, ordered by subscriber_key."""
    rows = scores[scores["tier"].isin(at_risk_tiers)].sort_values("subscriber_key")
    out: list[AgentContext] = []
    for r in rows.itertuples(index=False):
        factors = json.loads(r.top_factors) if isinstance(r.top_factors, str) and r.top_factors else []
        out.append(
            AgentContext(
                run_id=r.run_id,
                subscriber_key=r.subscriber_key,
                risk=float(r.churn_prob),
                tier=r.tier,
                as_of=pd.Timestamp(r.as_of).to_pydatetime(),
                top_factors=factors,
            )
        )
    return out
