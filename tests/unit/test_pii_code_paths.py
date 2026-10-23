"""Pipeline SQL and code never select the customers_pii view."""

from pathlib import Path

ROOT = Path("src/churn")
SQL = Path("sql")


def test_customers_pii_never_selected_by_pipeline_sql_or_code():
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if "synth" in path.parts:
            continue
        if "customers_pii" in path.read_text():
            hits.append(str(path))
    for path in sorted(SQL.rglob("*.sql")):
        if "synth" in path.parts:
            continue
        if "customers_pii" in path.read_text():
            hits.append(str(path))
    assert hits == []
