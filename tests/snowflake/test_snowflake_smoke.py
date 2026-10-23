"""Live Snowflake smoke. Skipped unless SNOWFLAKE_* is set in the environment."""

import os
from datetime import date

import pytest

from churn.model.train import train
from churn.orchestration.runner import run_cycle
from churn.synth.generator import generate
from churn.warehouse.duckdb import open_warehouse
from churn.warehouse.migrate import apply_migrations

AS_OF = date(2025, 6, 30)


def _ready() -> bool:
    needed = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_DATABASE")
    if not all(os.environ.get(name) for name in needed):
        return False
    return bool(os.environ.get("SNOWFLAKE_PASSWORD") or os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"))


@pytest.mark.snowflake
def test_migrate_and_small_cycle(settings):
    if not _ready():
        pytest.skip("SNOWFLAKE_* is not set")
    settings = settings.model_copy(update={"backend": "snowflake"})
    wh = open_warehouse(settings)
    try:
        apply_migrations(wh, settings)
        from churn import runtime_config

        runtime_config.seed(wh, settings)
        generate(wh, settings, scale="tiny")
        train(wh, settings)
        result = run_cycle(wh, settings, AS_OF, run_id="t24-smoke")
    finally:
        wh.close()
    assert result.status == "SUCCEEDED"
    assert result.scored > 0
