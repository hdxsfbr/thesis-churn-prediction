"""SQL is written in the Snowflake dialect and transpiled for other engines with sqlglot."""

from __future__ import annotations

import sqlglot


def render_many(sql: str, target: str) -> list[str]:
    """Render a script (one or more statements) for ``target``. Named params ``:p`` become ``$p`` on DuckDB."""
    if target == "snowflake":
        return [s.strip() for s in sql.split(";") if s.strip()]
    return [s for s in sqlglot.transpile(sql, read="snowflake", write=target) if s]


def render(sql: str, target: str) -> str:
    statements = render_many(sql, target)
    if len(statements) != 1:
        raise ValueError(f"expected exactly one statement, got {len(statements)}")
    return statements[0]
