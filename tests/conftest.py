"""Shared fixtures: an in-memory DuckDB warehouse, migrated, seeded with tiny synthetic data and a model."""

from __future__ import annotations

from pathlib import Path

import pytest

from churn.model.train import train
from churn.settings import Settings
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations


def make_settings(artefacts: Path) -> Settings:
    return Settings.load(
        overrides={
            "warehouse": {"path": ":memory:"},
            "artefacts_dir": str(artefacts),
        }
    )


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    return make_settings(tmp_path_factory.mktemp("artefacts"))


@pytest.fixture()
def fresh_wh():
    wh = DuckDBWarehouse(":memory:")
    yield wh
    wh.close()


@pytest.fixture(scope="session")
def pipeline(settings):
    """Migrated warehouse with tiny synthetic data and one promoted model."""
    wh = DuckDBWarehouse(":memory:")
    apply_migrations(wh, settings)
    generate(wh, settings, scale="tiny")
    version = train(wh, settings)
    yield {"wh": wh, "settings": settings, "model_version": version}
    wh.close()
