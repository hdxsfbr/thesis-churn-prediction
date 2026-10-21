"""Acceptance tests for T9: the runner classifies failures and handles empty/missing-model cycles."""

from __future__ import annotations

import io
import json
from datetime import date

import pytest

from churn import runtime_config
from churn.obs.errors import ContractError
from churn.obs.logging import configure_logging
from churn.orchestration import runner
from churn.orchestration.runner import RunFailed, run_cycle
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations

AS_OF = date(2025, 6, 30)


def _runs(wh, run_id):
    return wh.query("SELECT * FROM CHURN_OPS.RUNS WHERE run_id = :r", {"r": run_id}).iloc[0]


def test_missing_model_fails_with_model_error(settings):
    wh = DuckDBWarehouse(":memory:")
    try:
        apply_migrations(wh, settings)
        runtime_config.seed(wh, settings)
        generate(wh, settings, scale="tiny")  # data but no trained model
        with pytest.raises(RunFailed):
            run_cycle(wh, settings, AS_OF, run_id="t-nomodel")
        row = _runs(wh, "t-nomodel")
        assert row["status"] == "FAILED"
        assert row["error_class"] == "ModelError"
        assert "runbook" in row["error_msg"]
    finally:
        wh.close()


def test_zero_active_subscribers_succeeds_with_warning(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    buf = io.StringIO()
    configure_logging(stream=buf)
    result = run_cycle(wh, settings, date(2000, 1, 1), run_id="t-zero")
    assert result.status == "SUCCEEDED"
    assert result.active_in_source == 0 and result.scored == 0
    row = _runs(wh, "t-zero")
    assert row["status"] == "SUCCEEDED"
    assert row["scored"] == 0
    recs = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    warnings = [r for r in recs if r["level"] == "warning"]
    assert warnings and all(r["run_id"] == "t-zero" for r in warnings)


def test_failed_step_records_error_class(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    buf = io.StringIO()
    configure_logging(stream=buf)

    def boom(*a, **k):
        raise ContractError("column missing")

    monkeypatch.setattr(runner, "build_features", boom)
    with pytest.raises(RunFailed):
        run_cycle(wh, settings, AS_OF, run_id="t-failstep")
    row = _runs(wh, "t-failstep")
    assert row["status"] == "FAILED"
    assert row["error_class"] == "ContractError"
    recs = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    failed = [r for r in recs if r["event"] == "step_failed"]
    assert failed and failed[0]["step"] == "features" and failed[0]["error_class"] == "ContractError"
    assert any(r["event"] == "step_start" and r["step"] == "features" for r in recs)
