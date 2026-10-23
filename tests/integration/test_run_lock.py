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
