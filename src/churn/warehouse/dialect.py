"""SQL is written in the Snowflake dialect and transpiled for other engines with sqlglot."""

from __future__ import annotations

import sqlglot


def _without_line_comments(sql: str) -> str:
    """Drop ``--`` comments so a semicolon inside one is not a statement boundary."""
    kept: list[str] = []
    for line in sql.splitlines():
        in_single = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                if in_single and i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                in_single = not in_single
            elif not in_single and ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                cut = i
                break
            i += 1
        kept.append(line[:cut])
    return "\n".join(kept)


def render_many(sql: str, target: str) -> list[str]:
    """Render a script (one or more statements) for ``target``. Named params ``:p`` become ``$p`` on DuckDB."""
    if target == "snowflake":
        return [s.strip() for s in _without_line_comments(sql).split(";") if s.strip()]
    return [s for s in sqlglot.transpile(sql, read="snowflake", write=target) if s]


def render(sql: str, target: str) -> str:
    statements = render_many(sql, target)
    if len(statements) != 1:
        raise ValueError(f"expected exactly one statement, got {len(statements)}")
    return statements[0]
