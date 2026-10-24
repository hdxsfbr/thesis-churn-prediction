"""Per-run coverage, autonomy, audit completeness, and the treated-versus-control rate (R5.5, R6.1, R6.5)."""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from churn.settings import Settings
from churn.warehouse.base import Warehouse

# R6.1. response and outcome may stay PENDING until the window closes.
AUDIT_FIELDS = (
    "subscriber_key",
    "run_id",
    "risk",
    "tier",
    "archetype",
    "top_factors",
    "intervention_type",
    "rationale",
    "approval_status",
    "execution_status",
    "decided_at",
    "response",
    "outcome",
)
EMAIL_ARMS = ("checkin_email", "formulation", "retention_offer")


def coverage(dispatched: int, queued: int, at_risk: int, holdout: int) -> float | None:
    """(dispatched + queued) / (at-risk − holdout). Holdout is not a missed intervention."""
    denominator = at_risk - holdout
    if denominator <= 0:
        return None
    return (dispatched + queued) / denominator


def autonomy(autonomous: int, approved_dispatched: int) -> float | None:
    """Autonomous dispatches over autonomous plus approved-and-dispatched."""
    denominator = autonomous + approved_dispatched
    if denominator <= 0:
        return None
    return autonomous / denominator


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return not (isinstance(value, str) and not value.strip())


def audit_completeness(frame: pd.DataFrame) -> float | None:
    """Share of log rows whose R6.1 fields are filled. PENDING counts for response and outcome."""
    if frame.empty:
        return None
    complete = 0
    for row in frame.itertuples(index=False):
        if all(_present(getattr(row, column)) for column in AUDIT_FIELDS):
            complete += 1
    return complete / len(frame)


def _churn_rate(frame: pd.DataFrame) -> float | None:
    closed = frame[frame["outcome"].isin(["retained", "churned"])]
    if closed.empty:
        return None
    return float((closed["outcome"] == "churned").mean())


def compute_run_metrics(log: pd.DataFrame, at_risk: int, duration_s: float | None = None) -> dict[str, float]:
    """Metrics that are defined. An empty closed set omits the churn rates."""
    if log.empty:
        holdout = dispatched = queued = autonomous = approved = llm_calls = fallbacks = 0
    else:
        holdout = int((log["intervention_type"] == "HOLDOUT").sum())
        dispatched_rows = log[log["execution_status"] == "DISPATCHED"]
        dispatched = len(dispatched_rows)
        queued = int((log["execution_status"] == "PENDING_APPROVAL").sum())
        # Escalation is the human hand-off, so it is in neither side of the autonomy rate.
        contact = dispatched_rows[dispatched_rows["intervention_type"] != "escalation"]
        autonomous = int((contact["approval_status"] == "NOT_REQUIRED").sum())
        approved = int((contact["approval_status"] == "APPROVED").sum())
        llm_calls = int((log["content_source"] == "llm").sum()) if "content_source" in log.columns else 0
        email = log["intervention_type"].isin(EMAIL_ARMS) if "intervention_type" in log.columns else False
        fallbacks = int(((log["content_source"] == "template") & email).sum()) if "content_source" in log.columns else 0
    out: dict[str, float] = {}
    covered = coverage(dispatched, queued, at_risk, holdout)
    if covered is not None:
        out["coverage"] = covered
    own = autonomy(autonomous, approved)
    if own is not None:
        out["autonomy_rate"] = own
    complete = audit_completeness(log)
    if complete is not None:
        out["audit_completeness"] = complete
    out["llm_calls"] = float(llm_calls)
    out["llm_fallbacks"] = float(fallbacks)
    if duration_s is not None:
        out["duration_s"] = float(duration_s)
    if not log.empty:
        treated = _churn_rate(log[log["execution_status"] == "DISPATCHED"])
        control = _churn_rate(log[log["intervention_type"] == "HOLDOUT"])
        if treated is not None:
            out["treated_churn_rate"] = treated
        if control is not None:
            out["control_churn_rate"] = control
    return out


def record_run_metrics(
    wh: Warehouse, settings: Settings, run_id: str, *, at_risk: int, duration_s: float | None = None
) -> dict[str, float]:
    """Merge this run's metrics into RUN_METRICS. A second call replaces the same keys."""
    ops = settings.schemas.ops
    log = wh.query(f"SELECT * FROM {ops}.INTERVENTION_LOG WHERE run_id = :r", {"r": run_id})
    metrics = compute_run_metrics(log, at_risk, duration_s)
    if not metrics:
        return metrics
    now = datetime.now()
    frame = pd.DataFrame(
        [{"run_id": run_id, "metric": name, "value": value, "recorded_at": now} for name, value in metrics.items()]
    )
    wh.merge_frame(f"{ops}.RUN_METRICS", frame, keys=["run_id", "metric"])
    return metrics
