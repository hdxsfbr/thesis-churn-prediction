"""Error taxonomy (ARCHITECTURE §7). The class name is what lands in RUNS.error_class."""

from __future__ import annotations

RUNBOOK = "docs/runbook.md"


class ChurnError(RuntimeError):
    """Base of the taxonomy."""


class SourceUnavailable(ChurnError):
    """The source views cannot be read."""


class ContractError(ChurnError):
    """The source does not match the data contract."""


class ConfigError(ChurnError):
    """Runtime configuration is missing or invalid."""


class WarehouseWriteError(ChurnError):
    """A write to the warehouse failed."""


class LLMError(ChurnError):
    """The LLM call failed, timed out or ran out of budget."""


class GuardrailBlocked(ChurnError):
    """A guardrail blocked an action."""


class ModelError(ChurnError):
    """No usable model. Run `churn train`."""


TAXONOMY = (
    "SourceUnavailable",
    "ContractError",
    "ConfigError",
    "WarehouseWriteError",
    "LLMError",
    "GuardrailBlocked",
    "ModelError",
)


def classify(exc: BaseException) -> str:
    """Taxonomy name for an exception.

    Matches by class name through the MRO, so same-named errors defined elsewhere (e.g. in
    model.registry or contract.loader) classify identically. Unknown errors keep their type name.
    """
    for cls in type(exc).__mro__:
        if cls.__name__ in TAXONOMY:
            return cls.__name__
    return type(exc).__name__


def runbook_hint(error_class: str) -> str:
    if error_class == "ModelError":
        return f"runbook: {RUNBOOK}#model-error (run `churn train`)"
    return f"runbook: {RUNBOOK}"
