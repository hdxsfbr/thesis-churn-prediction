"""Two scheduled days run from the worker clock, and each agent reads that day's scores."""

from datetime import date, datetime, time

from churn.orchestration.scheduler import Worker

DAYS = (date(2025, 6, 29), date(2025, 6, 30))


def _fire(wh, settings):
    state = {"day": DAYS[0]}

    def clock():
        return datetime.combine(state["day"], time(6, 0))

    worker = Worker(wh, settings, clock=clock)
    results = []
    for day in DAYS:
        state["day"] = day
        results.append(worker.cycle())
    return results


def test_two_scheduled_runs_without_manual_action(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    results = _fire(wh, settings)
    assert [r.run_id for r in results] == ["scheduled-20250629", "scheduled-20250630"]
    assert all(r.status in {"SUCCEEDED", "DEGRADED"} for r in results)
    assert all(r.scored > 0 for r in results)


def test_agent_consumes_each_runs_scores(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    _fire(wh, settings)
    for run_id in ("scheduled-20250629", "scheduled-20250630"):
        scores = wh.query(
            "SELECT subscriber_key FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r", {"r": run_id}
        )
        acted = wh.query(
            "SELECT subscriber_key FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = :r", {"r": run_id}
        )
        assert len(scores) > 0
        assert len(acted) > 0
        assert set(acted["subscriber_key"]) <= set(scores["subscriber_key"])
