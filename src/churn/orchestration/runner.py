"""Cycle runner: score every active subscriber into RISK_SCORES and record a RUNS row.

All writes are MERGEs on deterministic keys, so re-running the same run id cannot duplicate rows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from churn.agent.cycle import run_agent, shard_of
from churn.contract.loader import contract_for
from churn.contract.validate import validate
from churn.contract.views import populate_key_map
from churn.execution.approvals import process as process_approvals
from churn.execution.outbox import dispatch
from churn.features.builder import as_of_ts, build_features, count_active
from churn.feedback.metrics import record_run_metrics
from churn.feedback.outcomes import close_outcomes
from churn.feedback.policy_stats import update_posteriors
from churn.model.registry import load_promoted
from churn.model.score import score_frame
from churn.obs.errors import SourceUnavailable, classify, runbook_hint
from churn.obs.logging import get_logger
from churn.orchestration.lock import acquire, release
from churn.runtime_config import RuntimeConfig, load_pinned
from churn.settings import Settings
from churn.warehouse.base import Warehouse


class RunFailed(RuntimeError):
    def __init__(self, message: str, scored: int | None = None) -> None:
        super().__init__(message)
        self.scored = scored


@dataclass
class RunResult:
    run_id: str
    status: str
    as_of: datetime
    model_version: str
    active_in_source: int
    scored: int
    gate_mode: str = "normal"
    agent_halted: bool = False
    at_risk: int = 0
    decided: int = 0
    dispatched: int = 0
    queued: int = 0


def default_run_id(as_of: date | datetime, kind: str = "scheduled") -> str:
    return f"{kind}-{as_of_ts(as_of):%Y%m%d}"


class _Steps:
    """Logs step start/end/failure and remembers the current step for failure classification."""

    def __init__(self, log) -> None:
        self.log = log
        self.current: str | None = None

    def __call__(self, name: str):
        steps = self

        class _Ctx:
            def __enter__(self_inner):
                steps.current = name
                steps.log.info("step_start", step=name)
                self_inner.t0 = time.monotonic()

            def __exit__(self_inner, *exc):
                if exc[0] is None:
                    steps.log.info("step_end", step=name, duration_s=round(time.monotonic() - self_inner.t0, 3))
                return False

        return _Ctx()


def _write_run(wh: Warehouse, ops: str, **fields) -> None:
    wh.merge_frame(f"{ops}.RUNS", pd.DataFrame([fields]), keys=["run_id"])


def _retry_source(fn, /, *args, **kwargs):
    """Three attempts, then the source error. The wait stays short so a failed read does not stall a test."""
    wrapped = retry(
        retry=retry_if_exception_type(SourceUnavailable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.01, min=0.01, max=0.05),
        reraise=True,
    )(fn)
    return wrapped(*args, **kwargs)


def _done(wh: Warehouse, ops: str, run_id: str, step: str, shard: int = 0) -> bool:
    rows = wh.query(
        f"SELECT status FROM {ops}.RUN_STEPS WHERE run_id = :r AND step = :s AND shard = :h",
        {"r": run_id, "s": step, "h": shard},
    )
    return not rows.empty and rows.iloc[0]["status"] == "done"


def _mark(wh: Warehouse, ops: str, run_id: str, step: str, shard: int = 0, rows: int | None = None) -> None:
    wh.merge_frame(
        f"{ops}.RUN_STEPS",
        pd.DataFrame([{
            "run_id": run_id,
            "step": step,
            "shard": shard,
            "status": "done",
            "rows_written": rows,
            "attempt": 1,
            "error_class": None,
            "error_msg": None,
            "updated_at": datetime.now(),
        }]),
        keys=["run_id", "step", "shard"],
    )


def _log_counts(wh: Warehouse, ops: str, run_id: str) -> tuple[int, int, int]:
    row = wh.query(
        f"""SELECT COUNT(*) AS decided,
                   COALESCE(SUM(CASE WHEN approval_status = 'PENDING' THEN 1 ELSE 0 END), 0) AS queued,
                   COALESCE(SUM(CASE WHEN execution_status = 'DISPATCHED' THEN 1 ELSE 0 END), 0) AS dispatched
            FROM {ops}.INTERVENTION_LOG WHERE run_id = :r""",
        {"r": run_id},
    ).iloc[0]
    return int(row["decided"]), int(row["queued"]), int(row["dispatched"])


def _before(wh: Warehouse, name: str, shard: int = 0) -> None:
    hook = getattr(wh, "before_step", None)
    if hook is not None:
        hook(name, shard)


def run_cycle(
    wh: Warehouse,
    settings: Settings,
    as_of: date | datetime,
    run_id: str | None = None,
    kind: str = "scheduled",
    shards: int = 1,
) -> RunResult:
    run_id = run_id or default_run_id(as_of, kind)
    as_of_dt = as_of_ts(as_of)
    if not acquire(wh, settings, run_id):
        return RunResult(run_id, "SKIPPED", as_of_dt, "", 0, 0)
    try:
        return _execute(wh, settings, as_of, run_id, kind, shards)
    finally:
        release(wh, settings, run_id)


def _execute(
    wh: Warehouse,
    settings: Settings,
    as_of: date | datetime,
    run_id: str | None = None,
    kind: str = "scheduled",
    shards: int = 1,
) -> RunResult:
    ops = settings.schemas.ops
    run_id = run_id or default_run_id(as_of, kind)
    as_of_dt = as_of_ts(as_of)
    prev = wh.query(f"SELECT status FROM {ops}.RUNS WHERE run_id = :r", {"r": run_id})
    resume = not prev.empty and str(prev.iloc[0]["status"]) in {"FAILED", "RUNNING"}
    started = datetime.now()
    t0 = time.monotonic()
    base = {"run_id": run_id, "kind": kind, "as_of": as_of_dt, "started_at": started}
    _write_run(wh, ops, **base, status="RUNNING", model_version=None, active_in_source=None,
               scored=None, ended_at=None, duration_s=None, error_msg=None,
               config_snapshot_id=None, error_class=None,
               at_risk=None, decided=None, dispatched=None, queued=None)  # clear counts of an earlier run with this id

    log = get_logger("runner", run_id=run_id)
    step = _Steps(log)
    log.info("run_start", kind=kind, as_of=as_of_dt.isoformat())
    active: int | None = None
    scored: int | None = None
    version: str | None = None
    cfg: RuntimeConfig | None = None
    at_risk = decided = dispatched = queued = 0
    try:
        with step("config"):
            cfg = load_pinned(wh, settings, run_id)  # pins CONFIG_SNAPSHOTS(run_id); a rerun reuses it
        with step("contract"):
            contract = contract_for(settings)
            validate(wh, contract)
        with step("model"):
            version, _ = load_promoted(wh, settings)
        with step("count_active"):
            active = _retry_source(count_active, wh, settings, contract, as_of_dt)
        if active == 0:
            log.warning("zero_active_subscribers", step="count_active", as_of=as_of_dt.isoformat())
            with step("write_scores"):
                wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
            scored = 0
            return _finish(wh, ops, base, t0, log, cfg, version, active, scored, 0, 0, 0, 0)
        scores_done = resume and all(_done(wh, ops, run_id, "write_scores", s) for s in range(shards))
        if scores_done:
            scored = int(
                wh.query(f"SELECT COUNT(*) AS n FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id}).iloc[0]["n"]
            )
        elif shards == 1:
            with step("features"):
                features = build_features(wh, settings, contract, as_of_dt, current_status=True)
            with step("score"):
                frame = score_frame(wh, settings, features, run_id, as_of_dt, cfg)
            with step("write_scores"), wh.transaction():
                populate_key_map(wh, contract, settings.key_pepper, settings.schemas.private)
                wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
                wh.merge_frame(f"{ops}.RISK_SCORES", frame, keys=["run_id", "subscriber_key"])
                scored = int(
                    wh.query(
                        f"SELECT COUNT(*) AS n FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id}
                    ).iloc[0]["n"]
                )
                # A mismatch rolls the delete and the merge back together. The checkpoint stays unmarked.
                if scored != active:
                    raise RunFailed(f"scored {scored} subscribers but {active} are active in the source", scored)
            _mark(wh, ops, run_id, "write_scores", 0, scored)
        else:
            with step("features"):
                features = build_features(wh, settings, contract, as_of_dt, current_status=True)
            with step("score"):
                frame = score_frame(wh, settings, features, run_id, as_of_dt, cfg)
            with step("write_scores"):
                populate_key_map(wh, contract, settings.key_pepper, settings.schemas.private)
                if not any(_done(wh, ops, run_id, "write_scores", s) for s in range(shards)):
                    wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
                for s in range(shards):
                    if resume and _done(wh, ops, run_id, "write_scores", s):
                        continue
                    part = frame.loc[frame["subscriber_key"].map(lambda key, s=s: shard_of(key, shards) == s)]
                    if not part.empty:
                        wh.merge_frame(f"{ops}.RISK_SCORES", part, keys=["run_id", "subscriber_key"])
                    _mark(wh, ops, run_id, "write_scores", s, len(part))
                scored = int(
                    wh.query(
                        f"SELECT COUNT(*) AS n FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id}
                    ).iloc[0]["n"]
                )
                if scored != active:
                    raise RunFailed(f"scored {scored} subscribers but {active} are active in the source", scored)

        # Process approver edits, then decide, gate and dispatch. Skipped when the agent is halted (invalid
        # tiers/guardrails). In approve_all mode (invalid gate) the gate sends every decision to approval.
        # The at-risk count is the coverage denominator, so it is counted whether or not the agent runs.
        # With invalid tiers (cfg.tiers is None) there is no at-risk definition: it stays 0.
        if cfg.tiers is not None:
            with step("reconcile"):
                marks = ", ".join(f":t{i}" for i, _ in enumerate(cfg.tiers.at_risk_tiers()))
                params = {"r": run_id, **{f"t{i}": t for i, t in enumerate(cfg.tiers.at_risk_tiers())}}
                at_risk = int(wh.query(
                    f"SELECT COUNT(*) AS n FROM {ops}.RISK_SCORES WHERE run_id = :r AND tier IN ({marks})", params
                ).iloc[0]["n"])
        if not cfg.agent_halt:
            with step("approvals"):
                process_approvals(wh, settings)
            agent_done = shards == 1 and resume and _done(wh, ops, run_id, "agent", 0)
            if agent_done:
                with step("agent"):
                    decided, queued, dispatched = _log_counts(wh, ops, run_id)
            elif shards == 1:
                with step("agent"):
                    _before(wh, "agent", 0)
                    decisions = run_agent(wh, settings, run_id, cfg)
                    holdout_n = int(wh.query(
                        f"SELECT COUNT(*) AS n FROM {ops}.INTERVENTION_LOG "
                        "WHERE run_id = :r AND intervention_type = 'HOLDOUT'",
                        {"r": run_id},
                    ).iloc[0]["n"])
                    decided = len(decisions) + holdout_n
                    queued = sum(d.needs_approval for d in decisions)
                    dispatched = dispatch(wh, settings, decisions)  # rows written to the outbox
                _mark(wh, ops, run_id, "agent", 0, decided)
            else:
                with step("agent"):
                    for s in range(shards):
                        if resume and _done(wh, ops, run_id, "agent", s):
                            continue
                        _before(wh, "agent", s)
                        decisions = run_agent(wh, settings, run_id, cfg, shard=s, n_shards=shards)
                        dispatch(wh, settings, decisions)
                        _mark(wh, ops, run_id, "agent", s, len(decisions))
                    decided, queued, dispatched = _log_counts(wh, ops, run_id)
            with step("outcomes"):
                _before(wh, "outcomes", 0)
                close_outcomes(wh, settings, as_of_dt, cfg)
            # Decide already read POLICY_STATS. Folding after that pins this run's posteriors.
            with step("policy_update"):
                update_posteriors(wh, settings, run_id, cfg)
        with step("metrics"):
            record_run_metrics(wh, settings, run_id, at_risk=at_risk, duration_s=time.monotonic() - t0)
    except Exception as exc:
        error_class = classify(exc)
        hint = runbook_hint(error_class) if error_class == "ModelError" else None
        log.error("step_failed", step=step.current, error_class=error_class, error_msg=str(exc))
        if isinstance(exc, RunFailed) and exc.scored is not None:
            scored = exc.scored
        _write_run(wh, ops, **base, status="FAILED", model_version=version, active_in_source=active,
                   scored=scored, ended_at=datetime.now(), duration_s=time.monotonic() - t0,
                   error_msg=f"{type(exc).__name__}: {exc}" + (f" ({hint})" if hint else ""),
                   config_snapshot_id=run_id if cfg else None, error_class=error_class,
                   at_risk=at_risk, decided=decided, dispatched=dispatched, queued=queued)
        if isinstance(exc, RunFailed):
            raise
        raise RunFailed(f"{type(exc).__name__}: {exc}" + (f" ({hint})" if hint else "")) from exc

    return _finish(wh, ops, base, t0, log, cfg, version, active, scored, at_risk, decided, dispatched, queued)


def _finish(wh, ops, base, t0, log, cfg, version, active, scored, at_risk, decided, dispatched, queued=0) -> RunResult:
    # Invalid gate thresholds -> approve_all; invalid tiers/guardrails -> agent halts. Either way DEGRADED.
    status = "DEGRADED" if cfg.degraded else "SUCCEEDED"
    _write_run(wh, ops, **base, status=status, model_version=version, active_in_source=active,
               scored=scored, ended_at=datetime.now(), duration_s=time.monotonic() - t0,
               error_msg="ConfigError: " + "; ".join(cfg.errors) if cfg.degraded else None,
               config_snapshot_id=base["run_id"], error_class="ConfigError" if cfg.degraded else None,
               at_risk=at_risk, decided=decided, dispatched=dispatched, queued=queued)
    log.info("run_end", status=status, scored=scored, active_in_source=active)
    return RunResult(base["run_id"], status, base["as_of"], version, active, scored, cfg.gate_mode, cfg.agent_halt,
                     at_risk, decided, dispatched, queued)
