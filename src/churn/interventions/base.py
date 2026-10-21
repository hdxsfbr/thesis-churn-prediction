"""Intervention types and the registry loaded from config/interventions.yaml."""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from churn.settings import config_dir

if TYPE_CHECKING:
    from churn.agent.context import AgentContext


@dataclass(frozen=True)
class Content:
    subject: str
    body: str  # keeps merge tokens such as {{first_name}} for the sender to fill
    source: str  # "template" | "llm"
    ref: str  # which template produced it


@dataclass(frozen=True)
class Action:
    params: dict[str, Any] = field(default_factory=dict)
    offer_code: str | None = None
    discount_pct: float | None = None
    escalation_reason: str | None = None


class InterventionType(ABC):
    name: str
    channel: str

    @abstractmethod
    def action(self, context: AgentContext) -> Action:
        """Concrete parameters for this subscriber."""

    @abstractmethod
    def template(self, context: AgentContext) -> Content:
        """Deterministic template content (fallback and thin-path content)."""


class RegistryError(ValueError):
    pass


def _import(path: str) -> type[InterventionType]:
    module, _, cls = path.rpartition(".")
    try:
        obj = getattr(importlib.import_module(module), cls)
    except (ImportError, AttributeError, ValueError) as exc:
        raise RegistryError(f"cannot load intervention {path!r}: {exc}") from exc
    if not (isinstance(obj, type) and issubclass(obj, InterventionType)):
        raise RegistryError(f"{path!r} is not an InterventionType")
    return obj


def load_registry() -> dict[str, InterventionType]:
    """name -> instance for every type listed in config/interventions.yaml."""
    data = yaml.safe_load((config_dir() / "interventions.yaml").read_text()) or {}
    registry: dict[str, InterventionType] = {}
    for path in data.get("interventions") or []:
        inst = _import(str(path))()
        if inst.name in registry:
            raise RegistryError(f"duplicate intervention type {inst.name!r}")
        registry[inst.name] = inst
    return registry
