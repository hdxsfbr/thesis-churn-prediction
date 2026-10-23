"""Assign the holdout control group and log a HOLDOUT decision with no action (R6.6)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import pandas as pd

from churn.agent.context import PENDING, AgentContext
from churn.runtime_config import RuntimeConfig
from churn.settings import Settings
from churn.warehouse.base import Warehouse

BUCKETS = 10_000


def _decision_id(run_id: str, subscriber_key: str) -> str:
    # Same digest as churn.agent.cycle.decision_id. Duplicated so this module does not import the cycle.
    return hashlib.sha256(f"{run_id}|{subscriber_key}".encode()).hexdigest()


def bucket(subscriber_key: str, salt_version: str) -> int:
    digest = hashlib.sha256(f"{salt_version}|{subscriber_key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


def is_control(subscriber_key: str, share: float, salt_version: str) -> bool:
    if share <= 0:
        return False
    if share >= 1:
        return True
    return bucket(subscriber_key, salt_version) < round(share * BUCKETS)


def assign(keys: list[str], share: float, salt_version: str) -> dict[str, str]:
    """subscriber_key -> 'control' or 'treatment'. The same key, share and salt always agree."""
    return {key: "control" if is_control(key, share, salt_version) else "treatment" for key in keys}


def _sticky(wh: Warehouse, ops: str, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    frame = wh.query(f"SELECT subscriber_key, arm_group FROM {ops}.HOLDOUT_ASSIGNMENTS")
    if frame.empty:
        return {}
    wanted = set(keys)
    return {row.subscriber_key: row.arm_group for row in frame.itertuples(index=False) if row.subscriber_key in wanted}


def apply_holdout(
    wh: Warehouse, settings: Settings, contexts: list[AgentContext], cfg: RuntimeConfig
) -> list[AgentContext]:
    """Persist assignments, log control members as HOLDOUT, and return the treatment contexts.

    An existing assignment sticks, so a later salt does not move someone who was already placed.
    """
    if not contexts or cfg.holdout is None:
        return contexts
    salt = cfg.holdout.salt_version
    groups = assign([c.subscriber_key for c in contexts], cfg.holdout.share, salt)
    ops = settings.schemas.ops
    stored = _sticky(wh, ops, list(groups))
    groups.update(stored)
    fresh = [c for c in contexts if c.subscriber_key not in stored]
    if fresh:
        now_groups = {c.subscriber_key: groups[c.subscriber_key] for c in fresh}
        wh.merge_frame(
            f"{ops}.HOLDOUT_ASSIGNMENTS",
            pd.DataFrame(
                {
                    "subscriber_key": list(now_groups),
                    "arm_group": list(now_groups.values()),
                    "salt_version": salt,
                    "assigned_at": [c.as_of for c in fresh],
                }
            ),
            keys=["subscriber_key"],
            insert_only=True,
        )
    control = [c for c in contexts if groups[c.subscriber_key] == "control"]
    _log_control(wh, ops, control)
    return [c for c in contexts if groups[c.subscriber_key] != "control"]


def _log_control(wh: Warehouse, ops: str, contexts: list[AgentContext]) -> None:
    if not contexts:
        return
    decided_at = datetime.now()
    rows = []
    for c in contexts:
        rows.append(
            {
                "decision_id": _decision_id(c.run_id, c.subscriber_key),
                "subscriber_key": c.subscriber_key,
                "run_id": c.run_id,
                "risk": c.risk,
                "tier": c.tier,
                "archetype": c.archetype,
                "archetype_conf": c.archetype_conf,
                "top_factors": json.dumps(c.top_factors),
                "intervention_type": "HOLDOUT",
                "params": "{}",
                "content_ref": "holdout",
                "content_source": "template",
                "rationale": json.dumps({"chosen": "HOLDOUT", "top_factors": c.top_factors}, sort_keys=True),
                "policy_scores": "{}",
                "guardrail_events": "[]",
                "gate_reason": "holdout: no action",
                "approval_status": "HOLDOUT",
                "execution_status": "HOLDOUT",
                "outbox_id": None,
                "response": PENDING,
                "outcome": PENDING,
                "decided_at": decided_at,
                "approved_at": None,
                "dispatched_at": decided_at,
                "outcome_at": None,
            }
        )
    wh.merge_frame(f"{ops}.INTERVENTION_LOG", pd.DataFrame(rows), keys=["decision_id"], insert_only=True)
