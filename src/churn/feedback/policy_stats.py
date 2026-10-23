"""Fold closed treated outcomes into POLICY_STATS Beta posteriors (R6.4)."""

from __future__ import annotations

import json

import pandas as pd

from churn.runtime_config import RuntimeConfig
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def fold_beta(prior: tuple[float, float], outcomes: list[str], decay: float = 1.0) -> tuple[float, float]:
    """Add retained to alpha and churned to beta, oldest first.

    ``decay`` of 1 leaves earlier evidence intact. A smaller decay multiplies the posterior
    before each new outcome, so older outcomes weigh less. Unknown values are ignored.
    """
    alpha, beta = float(prior[0]), float(prior[1])
    for outcome in outcomes:
        if decay != 1.0:
            alpha *= decay
            beta *= decay
        if outcome == "retained":
            alpha += 1.0
        elif outcome == "churned":
            beta += 1.0
    return alpha, beta


def bucket_of(policy_scores: str | None, intervention_type: str) -> str | None:
    """The tier:tenure bucket stored on the chosen arm, else any arm in the same decision."""
    if not isinstance(policy_scores, str) or not policy_scores.strip():
        return None
    try:
        scores = json.loads(policy_scores)
    except json.JSONDecodeError:
        return None
    if not isinstance(scores, dict):
        return None
    entry = scores.get(intervention_type)
    if isinstance(entry, dict) and entry.get("bucket"):
        return str(entry["bucket"])
    for value in scores.values():
        if isinstance(value, dict) and value.get("bucket"):
            return str(value["bucket"])
    return None


def _prior(cfg: RuntimeConfig, archetype: str, intervention: str) -> tuple[float, float] | None:
    policy = cfg.policy
    if policy is None:
        return None
    pair = policy.priors.get(archetype, {}).get(intervention)
    if pair is None:
        pair = policy.priors.get("default", {}).get(intervention)
    if pair is None:
        return None
    return float(pair[0]), float(pair[1])


def update_posteriors(wh: Warehouse, settings: Settings, run_id: str, cfg: RuntimeConfig) -> int:
    """Recompute treated posteriors from every closed outcome. A second call with the same rows is a no-op.

    Holdout rows are not treated. The stored alpha/beta include the configured prior, because selection
    uses a POLICY_STATS row in place of that prior. This run's decide step has already loaded the
    previous table, so the write is what the next run sees.
    """
    if cfg.policy is None:
        return 0
    ops = settings.schemas.ops
    rows = wh.query(
        f"""
        SELECT decision_id, archetype, intervention_type, policy_scores, outcome, outcome_at
        FROM {ops}.INTERVENTION_LOG
        WHERE execution_status = 'DISPATCHED'
          AND outcome IN ('retained', 'churned')
          AND intervention_type <> 'HOLDOUT'
          AND archetype IS NOT NULL
        """
    )
    if rows.empty:
        return 0
    rows = rows.copy()
    rows["bucket"] = [
        bucket_of(scores, kind)
        for scores, kind in zip(rows["policy_scores"], rows["intervention_type"], strict=True)
    ]
    rows = rows.dropna(subset=["bucket"])
    if rows.empty:
        return 0
    rows["outcome_at"] = pd.to_datetime(rows["outcome_at"], errors="coerce")
    rows = rows.sort_values(["outcome_at", "decision_id"], kind="mergesort", na_position="first")
    rows = rows.drop_duplicates(subset=["decision_id"], keep="last")
    decay = cfg.policy.decay
    built = []
    for (archetype, intervention, bucket), group in rows.groupby(
        ["archetype", "intervention_type", "bucket"], sort=True
    ):
        prior = _prior(cfg, str(archetype), str(intervention))
        if prior is None:
            continue
        alpha, beta = fold_beta(prior, group["outcome"].tolist(), decay)
        built.append(
            {
                "archetype": archetype,
                "intervention_type": intervention,
                "context_bucket": bucket,
                "alpha": alpha,
                "beta": beta,
                "n": len(group),
                "last_updated_run": run_id,
            }
        )
    if not built:
        return 0
    wh.merge_frame(
        f"{ops}.POLICY_STATS",
        pd.DataFrame(built),
        keys=["archetype", "intervention_type", "context_bucket"],
    )
    return len(rows)
