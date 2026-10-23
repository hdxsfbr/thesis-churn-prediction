"""Import directions from ARCHITECTURE §3.1. AST only, so a lazy import counts too."""

import ast
from pathlib import Path

ROOT = Path("src/churn")


def _imports(package: Path):
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield path, alias.name, None
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    yield path, module, alias.name


def _forbidden(package: str, banned: tuple[str, ...]) -> None:
    for path, module, name in _imports(ROOT / package):
        full = f"{module}.{name}" if name else module
        hit = module.startswith(banned) or full.startswith(banned)
        assert not hit, f"{path} imports {full}"


def test_model_does_not_import_agent_or_execution():
    _forbidden("model", ("churn.agent", "churn.execution"))


def test_agent_does_not_import_execution_outbox():
    _forbidden("agent", ("churn.execution.outbox",))


def test_execution_does_not_import_agent_policy():
    _forbidden("execution", ("churn.agent.policy",))


def test_synth_imports_only_warehouse_and_contract():
    # §3.1 also allows settings. The generator already imports it, and this ticket does not own that file.
    allowed = ("churn.warehouse", "churn.contract", "churn.settings", "churn.synth")
    for path, module, name in _imports(ROOT / "synth"):
        if not module.startswith("churn"):
            continue
        assert module.startswith(allowed), f"{path} imports {module}.{name}"
