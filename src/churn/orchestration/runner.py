"""Cycle runner: score every active subscriber into RISK_SCORES and record a RUNS row.

All writes are MERGEs on deterministic keys, so re-running the same run id cannot duplicate rows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from churn.contract.loader import contract_for
from churn.contract.validate import validate
from churn.contract.views import populate_key_map
from churn.features.builder import as_of_ts, build_features, count_active
from churn.model.registry import load_promoted
from churn.model.score import score_frame
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


def default_run_id(as_of: date | datetime, kind: str = "scheduled") -> str:
    return f"{kind}-{as_of_ts(as_of):%Y%m%d}"


def _write_run(wh: Warehouse, ops: str, **fields) -> None:
    wh.merge_frame(f"{ops}.RUNS", pd.DataFrame([fields]), keys=["run_id"])


def run_cycle(
    wh: Warehouse,
    settings: Settings,
    as_of: date | datetime,
    run_id: str | None = None,
    kind: str = "scheduled",
) -> RunResult:
    ops = settings.schemas.ops
    run_id = run_id or default_run_id(as_of, kind)
    as_of_dt = as_of_ts(as_of)
    started = datetime.now()
    t0 = time.monotonic()
    base = {"run_id": run_id, "kind": kind, "as_of": as_of_dt, "started_at": started}
    _write_run(wh, ops, **base, status="RUNNING", model_version=None, active_in_source=None,
               scored=None, ended_at=None, duration_s=None, error_msg=None)

    active: int | None = None
    scored: int | None = None
    version: str | None = None
    try:
        contract = contract_for(settings)
        validate(wh, contract)
        version, _ = load_promoted(wh, settings)
        active = count_active(wh, settings, contract, as_of_dt)
        features = build_features(wh, settings, contract, as_of_dt)
        frame = score_frame(wh, settings, features, run_id, as_of_dt)

        with wh.transaction():
            populate_key_map(wh, contract, settings.key_pepper, settings.schemas.private)
            wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
            wh.merge_frame(f"{ops}.RISK_SCORES", frame, keys=["run_id", "subscriber_key"])
            scored = int(
                wh.query(f"SELECT COUNT(*) AS n FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id}).iloc[0]["n"]
            )
            # Known gap (R8.1): if an earlier run with this run_id SUCCEEDED, this rollback restores its
            # scores while RUNS says FAILED. Resolve with checkpoint/pinning work.
            if scored != active:  # rolls the scores back
                raise RunFailed(f"scored {scored} subscribers but {active} are active in the source", scored)
    except Exception as exc:
        if isinstance(exc, RunFailed) and exc.scored is not None:
            scored = exc.scored
        _write_run(wh, ops, **base, status="FAILED", model_version=version, active_in_source=active,
                   scored=scored, ended_at=datetime.now(), duration_s=time.monotonic() - t0,
                   error_msg=f"{type(exc).__name__}: {exc}")
        if isinstance(exc, RunFailed):
            raise
        raise RunFailed(f"{type(exc).__name__}: {exc}") from exc

    _write_run(wh, ops, **base, status="SUCCEEDED", model_version=version, active_in_source=active,
               scored=scored, ended_at=datetime.now(), duration_s=time.monotonic() - t0, error_msg=None)
    return RunResult(run_id, "SUCCEEDED", as_of_dt, version, active, scored)
