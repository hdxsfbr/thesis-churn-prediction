"""T28: every ops table, runtime key, and required document is written down."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "sql" / "migrations"
OUTPUT_TABLES = (ROOT / "docs" / "output-tables.md").read_text()
AGENT_LOGIC = (ROOT / "docs" / "agent-logic.md").read_text()

REQUIRED = (
    "docs/plans/churn-prediction/ARCHITECTURE.md",
    "docs/data-contract.md",
    "docs/model-card.md",
    "docs/agent-logic.md",
    "docs/output-tables.md",
    "docs/runbook.md",
    "docs/deployment.md",
    "docs/ai-usage-log.md",
    "docs/demo-script.md",
)


def _ops_columns() -> dict[str, list[str]]:
    text = "\n".join(path.read_text() for path in sorted(MIGRATIONS.glob("V*.sql")))
    tables: dict[str, list[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (CHURN_OPS\.\w+)\s*\((.*?)\);", text, re.DOTALL
    ):
        body = re.sub(r"PRIMARY KEY\s*\([^)]*\)", "", match.group(2), flags=re.IGNORECASE)
        columns: list[str] = []
        for part in body.split(","):
            token = part.strip().split()
            if not token:
                continue
            name = token[0].lower()
            if name in {"primary", "constraint", "unique", "foreign"}:
                continue
            columns.append(name)
        tables[match.group(1).split(".")[1]] = columns
    for match in re.finditer(
        r"ALTER TABLE CHURN_OPS\.(\w+) ADD COLUMN IF NOT EXISTS (\w+)", text, re.IGNORECASE
    ):
        tables[match.group(1)].append(match.group(2).lower())
    return tables


def _section(table: str) -> str:
    match = re.search(rf"^## {table}\n(.*?)(?=^## |\Z)", OUTPUT_TABLES, re.DOTALL | re.MULTILINE)
    assert match, table
    return match.group(1)


def test_every_migration_table_and_column_is_documented() -> None:
    tables = _ops_columns()
    assert tables
    for table, columns in tables.items():
        section = _section(table)
        for column in columns:
            assert column in section, f"{table}.{column}"


def test_every_runtime_config_key_is_documented() -> None:
    defaults = yaml.safe_load((ROOT / "config" / "runtime_defaults.yaml").read_text())
    for key in defaults:
        assert key in AGENT_LOGIC


def test_required_documents_exist() -> None:
    for relative in REQUIRED:
        path = ROOT / relative
        assert path.is_file() and path.stat().st_size > 0, relative
