"""A5 scale on DuckDB. Marked `scale` so the default suite does not run it."""

from datetime import datetime

import pytest
from typer.testing import CliRunner

from churn import cli, runtime_config
from churn.contract.loader import contract_for
from churn.features.builder import count_active
from churn.model.train import train
from churn.settings import Settings
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations

pytestmark = pytest.mark.scale

INTERVAL_S = 24 * 60 * 60
AS_OF = datetime(2025, 6, 30)


@pytest.fixture(scope="module")
def full_scale(tmp_path_factory):
    settings = Settings.load(overrides={
        "warehouse": {"path": ":memory:"},
        "artefacts_dir": str(tmp_path_factory.mktemp("artefacts")),
    })
    wh = DuckDBWarehouse(":memory:")
    apply_migrations(wh, settings)
    runtime_config.seed(wh, settings)
    generate(wh, settings, scale="full")
    yield {"wh": wh, "settings": settings}
    wh.close()


def test_full_scale_volumes(full_scale):
    wh, settings = full_scale["wh"], full_scale["settings"]
    active = count_active(wh, settings, contract_for(settings), AS_OF)
    quiz = int(wh.query("SELECT COUNT(*) AS n FROM THESIS_SYNTH.quiz_responses").iloc[0]["n"])
    assert active >= 200_000, active
    assert quiz >= 7_500_000, quiz
    print(f"active={active} quiz={quiz}")


def test_full_cycle_within_interval_and_duration_recorded(full_scale, monkeypatch):
    wh, settings = full_scale["wh"], full_scale["settings"]
    train(wh, settings)
    monkeypatch.setattr(cli, "_open", lambda: (settings, wh))
    result = CliRunner().invoke(cli.app, ["cycle", "--as-of", "2025-06-30", "--timed", "--run-id", "t27-full"])
    assert result.exit_code == 0, result.output
    ops = settings.schemas.ops
    run = wh.query(f"SELECT duration_s, status FROM {ops}.RUNS WHERE run_id = 't27-full'").iloc[0]
    metric = wh.query(
        f"SELECT value FROM {ops}.RUN_METRICS WHERE run_id = 't27-full' AND metric = 'duration_s'"
    ).iloc[0]["value"]
    assert run["status"] in ("SUCCEEDED", "DEGRADED")
    assert run["duration_s"] == metric
    assert 0 < metric < INTERVAL_S
    print(f"duration_s={metric} status={run['status']}")
