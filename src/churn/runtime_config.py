"""Runtime config: CHURN_OPS.AGENT_CONFIG rows -> validated RuntimeConfig, pinned per run.

Fail-safe rules (ARCHITECTURE 4.3):
- gate thresholds missing/invalid  -> gate_mode "approve_all" (run DEGRADED, ConfigError)
- tiers/guardrails/holdout/outcomes missing/invalid -> agent_halt (scores are still written)
- a rerun of the same run id reuses its CONFIG_SNAPSHOTS row, not the live table.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from churn.settings import Settings, config_dir
from churn.warehouse.base import Warehouse

TIER_ORDER = ("low", "medium", "high", "critical")


class ConfigError(RuntimeError):
    pass


class TierConfig(BaseModel):
    boundaries: dict[str, float]
    at_risk_from: str = "medium"

    @model_validator(mode="after")
    def _check(self) -> TierConfig:
        if set(self.boundaries) != set(TIER_ORDER[1:]):
            raise ValueError("boundaries must define exactly medium, high and critical")
        values = [self.boundaries[t] for t in TIER_ORDER[1:]]
        if not all(0 < v < 1 for v in values) or any(a >= b for a, b in itertools.pairwise(values)):
            raise ValueError("boundaries must be strictly increasing and inside (0, 1)")
        if self.at_risk_from not in TIER_ORDER[1:]:
            raise ValueError("at_risk_from must be medium, high or critical")
        return self

    def at_risk_tiers(self) -> list[str]:
        return list(TIER_ORDER[TIER_ORDER.index(self.at_risk_from):])


class GateConfig(BaseModel):
    t_risk: float = Field(gt=0, le=1)
    t_cost_usd: float = Field(ge=0)
    t_discount_pct: float = Field(ge=0, le=100)
    always_approve: list[str] = Field(default_factory=list)


class GuardrailConfig(BaseModel):
    contact_cap_14d: int = Field(ge=1)
    contact_cap_90d: int = Field(ge=1)
    max_discount_pct: float = Field(ge=0, le=100)
    offer_catalogue: list[str] = Field(min_length=1)
    approval_expiry_h: float = Field(gt=0)
    approval_grace_h: float = Field(default=24, ge=0)  # answered rows are honoured this long after the deadline

    @model_validator(mode="after")
    def _caps(self) -> GuardrailConfig:
        if self.contact_cap_14d > self.contact_cap_90d:
            raise ValueError("contact_cap_14d cannot exceed contact_cap_90d")
        return self


class HoldoutConfig(BaseModel):
    share: float = Field(ge=0, le=1)
    salt_version: str = "v1"


class OutcomesConfig(BaseModel):
    window_days: int = Field(gt=0)
    response_window_days: int = Field(gt=0)


# section key -> (model, failure effect)
SECTIONS: dict[str, tuple[type[BaseModel], Literal["approve_all", "halt"]]] = {
    "gate": (GateConfig, "approve_all"),
    "tiers": (TierConfig, "halt"),
    "guardrails": (GuardrailConfig, "halt"),
    "holdout": (HoldoutConfig, "halt"),
    "outcomes": (OutcomesConfig, "halt"),
}


class RuntimeConfig(BaseModel):
    """Effective config. A section that is missing or invalid is None and named in ``errors``."""

    tiers: TierConfig | None = None
    gate: GateConfig | None = None
    guardrails: GuardrailConfig | None = None
    holdout: HoldoutConfig | None = None
    outcomes: OutcomesConfig | None = None
    gate_mode: Literal["normal", "approve_all"] = "approve_all"
    agent_halt: bool = True
    errors: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def degraded(self) -> bool:
        return self.gate_mode == "approve_all" or self.agent_halt


def parse(rows: dict[str, str]) -> RuntimeConfig:
    """Validate raw AGENT_CONFIG rows (key -> value_json); never raises."""
    sections: dict[str, BaseModel | None] = {}
    errors: list[str] = []
    for key, (model, _) in SECTIONS.items():
        raw = rows.get(key)
        if raw is None:
            sections[key] = None
            errors.append(f"{key}: missing")
            continue
        try:
            sections[key] = model.model_validate(json.loads(raw))
        except (ValueError, TypeError, ValidationError) as exc:
            sections[key] = None
            errors.append(f"{key}: {' '.join(str(exc).split())}")
    bad = {k for k, v in sections.items() if v is None}
    return RuntimeConfig(
        **sections,
        gate_mode="approve_all" if any(SECTIONS[k][1] == "approve_all" for k in bad) else "normal",
        agent_halt=any(SECTIONS[k][1] == "halt" for k in bad),
        errors=errors,
    )


def load(wh: Warehouse, settings: Settings) -> RuntimeConfig:
    """Read AGENT_CONFIG as it is now and validate it."""
    df = wh.query(f"SELECT key, value_json FROM {settings.schemas.ops}.AGENT_CONFIG")
    return parse({str(k): v for k, v in zip(df["key"], df["value_json"], strict=True) if v is not None})


def load_pinned(wh: Warehouse, settings: Settings, run_id: str) -> RuntimeConfig:
    """The run's snapshot if one exists (rerun), otherwise load, validate and pin a new one."""
    ops = settings.schemas.ops
    existing = wh.query(f"SELECT config_json FROM {ops}.CONFIG_SNAPSHOTS WHERE run_id = :r", {"r": run_id})
    if len(existing):
        return RuntimeConfig.model_validate_json(existing.iloc[0]["config_json"])
    cfg = load(wh, settings)
    snapshot = pd.DataFrame(
        [
            {
                "run_id": run_id,
                "config_json": cfg.model_dump_json(),
                "valid": cfg.valid,
                "errors": json.dumps(cfg.errors),
                "gate_mode": cfg.gate_mode,
                "agent_halt": cfg.agent_halt,
                "created_at": datetime.now(),
            }
        ]
    )
    wh.merge_frame(f"{ops}.CONFIG_SNAPSHOTS", snapshot, keys=["run_id"])
    return cfg


def defaults(settings: Settings | None = None) -> dict[str, dict]:
    return yaml.safe_load((config_dir() / "runtime_defaults.yaml").read_text()) or {}


def seed(wh: Warehouse, settings: Settings, overwrite: bool = False, updated_by: str = "bootstrap") -> int:
    """Insert the defaults into AGENT_CONFIG. Existing keys (possibly edited) are kept unless overwrite."""
    rows = pd.DataFrame(
        [
            {"key": k, "value_json": json.dumps(v), "updated_by": updated_by, "updated_at": datetime.now()}
            for k, v in defaults(settings).items()
        ]
    )
    return wh.merge_frame(f"{settings.schemas.ops}.AGENT_CONFIG", rows, keys=["key"], insert_only=not overwrite)


def assign_tiers(probs: Sequence[float], tiers: TierConfig) -> list[str]:
    """low below the medium boundary; each boundary is the inclusive lower bound of its tier."""
    b = tiers.boundaries
    return [
        "critical" if p >= b["critical"] else "high" if p >= b["high"] else "medium" if p >= b["medium"] else "low"
        for p in probs
    ]
