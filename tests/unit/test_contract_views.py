"""Contract views: renames, dedup (latest loaded_at wins), UTC normalisation, pseudonymous key."""

from __future__ import annotations

from datetime import date

import pytest

from churn.contract.loader import Contract
from churn.contract.views import contract_ctes
from churn.orchestration.runner import run_cycle
from churn.warehouse.duckdb import DuckDBWarehouse


def _contract(columns: dict, dedup_key: str = "evt") -> Contract:
    return Contract(
        schema_name="SRC",
        views={
            "events": {
                "object": "{schema}.raw_events",
                "dedup_key": dedup_key,
                "loaded_at": "ingested",
                "columns": columns,
            }
        },
    )


BASIC = {
    "event_id": {"source": "evt", "type": "varchar"},
    "customer_id": {"source": "cust", "type": "varchar"},
    "amount": {"source": "amt", "type": "number"},
}


@pytest.fixture()
def wh():
    w = DuckDBWarehouse(":memory:")
    w.execute("CREATE SCHEMA SRC")
    yield w
    w.close()


def _select(wh, contract, pepper="p", cols="*", include_raw=False):
    ctes = contract_ctes(contract, pepper, include_raw_id=include_raw)
    return wh.query(f"WITH {ctes} SELECT {cols} FROM events ORDER BY 1")


def test_duplicate_events_deduped_latest_loaded_at_wins(wh):
    wh.execute(
        "CREATE TABLE SRC.raw_events (evt VARCHAR, cust VARCHAR, amt DOUBLE, ingested TIMESTAMP);"
        "INSERT INTO SRC.raw_events VALUES "
        "('e1','c1',10,'2025-01-01 00:00:00'),"
        "('e1','c1',12,'2025-01-03 00:00:00'),"
        "('e1','c1',11,'2025-01-02 00:00:00'),"
        "('e2','c2',5,'2025-01-01 00:00:00')"
    )
    df = _select(wh, _contract(BASIC), cols="event_id, amount")
    assert list(df["event_id"]) == ["e1", "e2"]
    assert list(df["amount"]) == [12, 5]  # renames applied, latest load wins


def test_timestamps_normalised_to_utc(wh):
    wh.execute(
        "CREATE TABLE SRC.raw_events (evt VARCHAR, cust VARCHAR, aware TIMESTAMPTZ, local_ts TIMESTAMP, "
        "naive TIMESTAMP, ingested TIMESTAMP);"
        "INSERT INTO SRC.raw_events VALUES "
        "('e1','c1','2025-03-01 10:00:00+05:00','2025-07-01 12:00:00','2025-01-01 08:00:00','2025-01-02 00:00:00')"
    )
    cols = {
        "event_id": {"source": "evt", "type": "varchar"},
        "aware_at": {"source": "aware", "type": "timestamp_tz"},
        "local_at": {"source": "local_ts", "type": "timestamp", "tz": "America/New_York"},
        "naive_at": {"source": "naive", "type": "timestamp"},
    }
    row = _select(wh, _contract(cols), cols="aware_at, local_at, naive_at, loaded_at").iloc[0]
    assert str(row["aware_at"]) == "2025-03-01 05:00:00"  # +05:00 -> UTC
    assert str(row["local_at"]) == "2025-07-01 16:00:00"  # New York summer time (UTC-4) -> UTC
    assert str(row["naive_at"]) == "2025-01-01 08:00:00"  # naive = already UTC
    assert str(row["loaded_at"]) == "2025-01-02 00:00:00"


def test_ops_tables_carry_subscriber_key_only(pipeline):
    wh = pipeline["wh"]
    run_cycle(wh, pipeline["settings"], date(2025, 6, 30), run_id="t-ops-keys")
    cols = wh.query(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema ILIKE 'CHURN_OPS'"
    )
    assert len(cols) > 0
    assert not cols["column_name"].str.lower().str.contains("customer").any()
    assert "subscriber_key" in set(cols["column_name"].str.lower())

    # Raw ids live only in the private key map, one row per subscriber_key.
    km = wh.query("SELECT subscriber_key, customer_id FROM CHURN_PRIVATE.KEY_MAP")
    assert len(km) > 100
    assert km["subscriber_key"].is_unique
    scored = wh.query("SELECT DISTINCT subscriber_key FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-ops-keys'")
    assert set(scored["subscriber_key"]) <= set(km["subscriber_key"])
    assert not set(scored["subscriber_key"]) & set(km["customer_id"])


def test_subscriber_key_depends_on_pepper(wh):
    wh.execute(
        "CREATE TABLE SRC.raw_events (evt VARCHAR, cust VARCHAR, amt DOUBLE, ingested TIMESTAMP);"
        "INSERT INTO SRC.raw_events VALUES ('e1','c1',1,'2025-01-01'), ('e2','c2',1,'2025-01-01')"
    )
    c = _contract(BASIC)
    a = _select(wh, c, pepper="pepper-a", cols="subscriber_key")["subscriber_key"].tolist()
    a2 = _select(wh, c, pepper="pepper-a", cols="subscriber_key")["subscriber_key"].tolist()
    b = _select(wh, c, pepper="pepper-b", cols="subscriber_key")["subscriber_key"].tolist()
    assert a == a2
    assert set(a).isdisjoint(b)
    assert len(set(a)) == 2
    assert all(len(k) == 64 for k in a)
    # The CTE does not expose the raw id unless the key map asks for it.
    assert "customer_id" not in _select(wh, c).columns
    assert "customer_id" in _select(wh, c, include_raw=True).columns
