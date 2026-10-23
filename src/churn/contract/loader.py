"""Load the source contract (config/source_contract.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from churn.settings import Settings


class ContractError(RuntimeError):
    """The mapped source does not satisfy the contract."""


class Column(BaseModel):
    source: str
    type: str  # varchar | number | timestamp | timestamp_tz | boolean
    tz: str | None = None  # source time zone of a naive `timestamp` column (default: already UTC)


class View(BaseModel):
    pii: bool = False
    object: str
    dedup_key: str
    loaded_at: str
    columns: dict[str, Column]


class Contract(BaseModel):
    schema_name: str
    views: dict[str, View]

    def physical(self, view: str) -> str:
        return self.views[view].object.replace("{schema}", self.schema_name)


def load_contract(path: Path | str) -> Contract:
    raw = yaml.safe_load(Path(path).read_text())
    return Contract(schema_name=raw["schema"], views=raw["views"])


def contract_for(settings: Settings) -> Contract:
    return load_contract(settings.contract_file)
