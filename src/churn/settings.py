"""Settings: config/settings.yaml, with env overrides for deployment values and secrets."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("CHURN_CONFIG_DIR", REPO_ROOT / "config"))


def project_root() -> Path:
    return config_dir().parent


class WarehouseSettings(BaseModel):
    path: str = "artefacts/local.duckdb"


class Schemas(BaseModel):
    synth: str = "THESIS_SYNTH"
    ops: str = "CHURN_OPS"


class Scoring(BaseModel):
    active_statuses: list[str] = Field(default_factory=lambda: ["ACTIVE", "PAUSED", "PAST_DUE"])


class Label(BaseModel):
    horizon_days: int = 30


class SynthSettings(BaseModel):
    seed: int = 1
    end_date: date = date(2025, 6, 30)
    history_months: int = 24
    scales: dict[str, int] = Field(default_factory=lambda: {"tiny": 1500})


class ModelSettings(BaseModel):
    seed: int = 7
    min_history_days: int = 60
    calibration_share: float = 0.25
    lightgbm: dict[str, Any] = Field(default_factory=dict)


class Settings(BaseModel):
    backend: str = "duckdb"
    warehouse: WarehouseSettings = WarehouseSettings()
    artefacts_dir: str = "artefacts"
    schemas: Schemas = Schemas()
    contract_path: str = "config/source_contract.yaml"
    scoring: Scoring = Scoring()
    label: Label = Label()
    synth: SynthSettings = SynthSettings()
    model: ModelSettings = ModelSettings()
    # Secret: env only, never read from YAML.
    key_pepper: str = ""

    @classmethod
    def load(cls, path: Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
        path = path or config_dir() / "settings.yaml"
        data = yaml.safe_load(Path(path).read_text()) or {}
        data = _deep_merge(data, overrides or {})
        if "CHURN_DUCKDB_PATH" in os.environ and not (overrides or {}).get("warehouse"):
            data.setdefault("warehouse", {})["path"] = os.environ["CHURN_DUCKDB_PATH"]
        data["key_pepper"] = os.environ.get("CHURN_KEY_PEPPER", "dev-pepper-not-secret")
        return cls.model_validate(data)

    def resolve(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else project_root() / p

    @property
    def contract_file(self) -> Path:
        return self.resolve(self.contract_path)

    @property
    def sql_dir(self) -> Path:
        return project_root() / "sql"

    @property
    def models_dir(self) -> Path:
        return self.resolve(self.artefacts_dir) / "models"

    @property
    def duckdb_path(self) -> str:
        p = self.warehouse.path
        return p if p == ":memory:" else str(self.resolve(p))


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
