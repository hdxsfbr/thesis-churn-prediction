"""Every SQL file is Snowflake dialect that sqlglot can write as DuckDB."""

import re
from pathlib import Path

import sqlglot

ROOT = Path("sql")
COMMENT = re.compile(r"--.*?$", re.MULTILINE)
TOKENS = {
    "{{as_of}}": "CAST(CURRENT_TIMESTAMP AS TIMESTAMP)",
    "{{active_statuses}}": "'ACTIVE'",
    "{{ops_schema}}": "CHURN_OPS",
    "{{contract_ctes}}": "contract_stub AS (SELECT 1 AS x)",
    "{{domain_ctes}}": "domain_stub AS (SELECT 1 AS x)",
}
STATEMENTS = {"CREATE", "WITH", "SELECT", "ALTER", "INSERT", "UPDATE", "GRANT", "MERGE", "DROP"}


def _prepare(text: str) -> str:
    cleaned = COMMENT.sub("", text)
    for token, value in TOKENS.items():
        cleaned = cleaned.replace(token, value)
    body = cleaned.strip().rstrip(";")
    head = body.lstrip().split(None, 1)[0].upper() if body else ""
    if head not in STATEMENTS:
        body = f"WITH {body}\nSELECT 1 AS transpiled"
    return body


def test_every_sql_file_transpiles_to_duckdb():
    paths = sorted(ROOT.rglob("*.sql"))
    assert paths
    failures = []
    for path in paths:
        try:
            rendered = sqlglot.transpile(_prepare(path.read_text()), read="snowflake", write="duckdb")
        except sqlglot.errors.SqlglotError as exc:
            failures.append(f"{path}: {exc}")
            continue
        if not rendered:
            failures.append(f"{path}: empty")
    assert failures == [], failures
    grants = (ROOT / "bootstrap" / "roles_and_grants.sql").read_text()
    approver = [
        line.strip()
        for line in grants.splitlines()
        if line.strip().upper().startswith("GRANT") and "CHURN_APPROVER" in line
    ]
    assert approver == [
        "GRANT USAGE ON SCHEMA CHURN_OPS TO ROLE CHURN_APPROVER;",
        "GRANT SELECT, UPDATE ON TABLE CHURN_OPS.APPROVALS TO ROLE CHURN_APPROVER;",
    ]
