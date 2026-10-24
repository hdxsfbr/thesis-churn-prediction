"""A live lock skips the cycle. A stale heartbeat is taken over."""

from datetime import date, datetime, timedelta

import pandas as pd

from churn.orchestration.lock import acquire, release
from churn.orchestration.runner import run_cycle
from fixtures.faulty_warehouse import FaultyWarehouse

AS_OF = date(2025, 6, 30)


def test_concurrent_cycle_skipped(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    assert acquire(wh, settings, "blocker")
    try:
        result = run_cycle(wh, settings, AS_OF, run_id="t20-skip")
        assert result.status == "SKIPPED"
        assert result.scored == 0
        rows = wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.RUNS WHERE run_id = 't20-skip'")
        assert int(rows.iloc[0]["n"]) == 0
    finally:
        release(wh, settings, "blocker")


def test_stale_lock_taken_over(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    wh.merge_frame(
        "CHURN_OPS.RUN_LOCKS",
        pd.DataFrame([{
            "lock_name": "cycle",
            "holder": "dead",
            "heartbeat_at": datetime.now() - timedelta(hours=2),
            "ttl_s": 60,
        }]),
        keys=["lock_name"],
    )
    seen: dict[str, str] = {}
    faulty = FaultyWarehouse(wh)

    def look(name, _shard):
        if name == "agent" and "holder" not in seen:
            row = wh.query("SELECT holder FROM CHURN_OPS.RUN_LOCKS WHERE lock_name = 'cycle'")
            seen["holder"] = row.iloc[0]["holder"]

    faulty.before_step = look
    result = run_cycle(faulty, settings, AS_OF, run_id="t20-stale")
    assert result.status == "SUCCEEDED"
    assert seen["holder"] == "t20-stale"


def test_heartbeat_refreshes_before_the_next_step(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    ops = settings.schemas.ops
    faulty = FaultyWarehouse(wh)
    seen: dict[str, datetime] = {}

    def look(name, _shard):
        if name == "agent":
            wh.execute(
                f"UPDATE {ops}.RUN_LOCKS SET heartbeat_at = :old WHERE lock_name = 'cycle'",
                {"old": datetime.now() - timedelta(seconds=200)},
            )
        if name == "outcomes":
            row = wh.query(f"SELECT heartbeat_at FROM {ops}.RUN_LOCKS WHERE lock_name = 'cycle'")
            seen["beat"] = pd.Timestamp(row.iloc[0]["heartbeat_at"]).to_pydatetime()

    faulty.before_step = look
    result = run_cycle(faulty, settings, AS_OF, run_id="submit-heartbeat")
    assert result.status in {"SUCCEEDED", "DEGRADED"}
    assert (datetime.now() - seen["beat"].replace(tzinfo=None)).total_seconds() < 30
