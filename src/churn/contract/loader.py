"""Load and validate the source contract (config/source_contract.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from churn.settings import Settings
from churn.warehouse.base import Warehouse


class ContractError(RuntimeError):
    """The mapped source does not satisfy the contract."""


class Column(BaseModel):
    source: str
    type: str


class View(BaseModel):
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


def validate(wh: Warehouse, contract: Contract) -> None:
    """Every mapped object must exist and carry every mapped source column."""
    problems: list[str] = []
    for name, view in contract.views.items():
        *_, schema, table = contract.physical(name).split(".")
        found = wh.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE UPPER(table_schema) = UPPER(:s) AND UPPER(table_name) = UPPER(:t)",
            {"s": schema, "t": table},
        )
        have = {c.upper() for c in found["column_name"]}
        if not have:
            problems.append(f"{name}: object {contract.physical(name)} not found")
            continue
        wanted = {c.source for c in view.columns.values()} | {view.loaded_at}
        missing = sorted(c for c in wanted if c.upper() not in have)
        if missing:
            problems.append(f"{name}: missing columns {missing}")
    if problems:
        raise ContractError("; ".join(problems))
