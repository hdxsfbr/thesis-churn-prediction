"""One small-scale cycle, then 45 simulated days. Coverage and autonomy use the default thresholds."""

from datetime import date

import pytest

from churn.feedback.outcomes import advance
from churn.model.train import train
from churn.orchestration.runner import run_cycle
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations

AS_OF = date(2025, 6, 30)


@pytest.fixture(scope="module")
def evaluation_run(tmp_path_factory):
    from churn import runtime_config
    from conftest import make_settings

    settings = make_settings(tmp_path_factory.mktemp("artefacts"))
    wh = DuckDBWarehouse(":memory:")
    apply_migrations(wh, settings)
    runtime_config.seed(wh, settings)
    generate(wh, settings, scale="small")
    train(wh, settings)
    result = run_cycle(wh, settings, AS_OF, run_id="t19-eval")
    advance(wh, settings, AS_OF, 45)
    yield wh, settings, result
    wh.close()


def _metric(wh, settings, name):
    frame = wh.query(
        f"""
        SELECT value FROM {settings.schemas.ops}.RUN_METRICS
        WHERE run_id = 't19-eval' AND metric = :m
        """,
        {"m": name},
    )
    assert not frame.empty, name
    return float(frame.iloc[0]["value"])


@pytest.mark.slow
def test_coverage_above_85_percent(evaluation_run):
    wh, settings, result = evaluation_run
    assert result.status == "SUCCEEDED"
    assert _metric(wh, settings, "coverage") > 0.85


@pytest.mark.slow
def test_autonomy_above_70_percent_with_default_thresholds(evaluation_run):
    wh, settings, _result = evaluation_run
    assert _metric(wh, settings, "autonomy_rate") > 0.70
