"""MERGE SQL is keyed, and applying it twice does not insert a second row."""

from pathlib import Path

import pandas as pd

from churn.warehouse.bulk import build_merge_sql
from churn.warehouse.snowflake import credentials_from_env


def test_generated_merge_is_keyed_and_idempotent(fresh_wh):
    fresh_wh.execute("CREATE TABLE ops_merge (id VARCHAR, v DOUBLE)")
    sql = build_merge_sql("ops_merge", "stage_merge", ["id", "v"], ["id"])
    assert "ON t.id = s.id" in sql
    assert "WHEN MATCHED THEN UPDATE SET v = s.v" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    frame = pd.DataFrame([{"id": "a", "v": 1.0}])
    fresh_wh.con.register("stage_merge", frame)
    fresh_wh.execute(sql)
    fresh_wh.con.register("stage_merge", pd.DataFrame([{"id": "a", "v": 2.0}]))
    fresh_wh.execute(sql)
    out = fresh_wh.query("SELECT id, v FROM ops_merge")
    assert len(out) == 1
    assert float(out.iloc[0]["v"]) == 2.0


def test_merge_casts_null_stage_columns_to_the_target_type():
    sql = build_merge_sql(
        "CHURN_OPS.RUNS", "CHURN_OPS.MERGE_STAGE_1", ["run_id", "ended_at", "error_msg"], ["run_id"],
        casts={"ended_at": "TIMESTAMP_NTZ", "error_msg": "TEXT"},
    )
    assert "ended_at = CAST(s.ended_at AS TIMESTAMP_NTZ)" in sql
    assert "CAST(s.error_msg AS TEXT)" in sql
    assert "ON t.run_id = s.run_id" in sql


def test_credentials_only_from_env(monkeypatch):
    names = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_PRIVATE_KEY_PATH",
        "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    try:
        credentials_from_env()
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret-from-env")
    monkeypatch.setenv("SNOWFLAKE_ROLE", "role")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "wh")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
    creds = credentials_from_env()
    assert creds == {
        "account": "acct",
        "user": "user",
        "role": "role",
        "warehouse": "wh",
        "database": "db",
        "password": "secret-from-env",
    }
    source = Path("src/churn/warehouse/snowflake.py").read_text()
    assert "secret-from-env" not in source
    assert "acct" not in source
