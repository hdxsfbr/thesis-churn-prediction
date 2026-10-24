"""Catch-up runs once, and the retrain cron is the one in settings."""

from datetime import datetime

from apscheduler.triggers.cron import CronTrigger

from churn.orchestration.runner import RunFailed
from churn.orchestration.scheduler import Worker, _load_retrain


def test_catch_up_once_when_todays_run_missing(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    day = datetime(2025, 4, 15, 6, 0)
    worker = Worker(wh, settings, clock=lambda: day)
    first = worker.catch_up()
    assert first is not None
    assert first.status in {"SUCCEEDED", "DEGRADED"}
    assert first.run_id == "scheduled-20250415"
    assert worker.catch_up() is None
    rows = wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.RUNS WHERE run_id = 'scheduled-20250415'")
    assert int(rows.iloc[0]["n"]) == 1


def test_retrain_job_registered_from_settings(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    worker = Worker(wh, settings, clock=lambda: datetime(2025, 6, 30, 6, 0))
    job = worker.scheduler.get_job("retrain")
    assert job is not None
    expected = CronTrigger.from_crontab(settings.schedule.retrain_cron, timezone="UTC")
    assert str(job.trigger) == str(expected)
    assert settings.schedule.retrain_cron == "0 6 * * 1"
    assert worker.scheduler.get_job("cycle") is not None


def test_failed_catch_up_still_starts(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    worker = Worker(wh, settings, clock=lambda: datetime(2025, 6, 30, 6, 0))

    def boom():
        raise RunFailed("source down")

    monkeypatch.setattr(worker, "catch_up", boom)
    started = []
    monkeypatch.setattr(worker.scheduler, "start", lambda: started.append("started"))
    worker.start()
    assert started == ["started"]


def test_retrain_import_error_is_logged(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    worker = Worker(wh, settings, clock=lambda: datetime(2025, 6, 30, 6, 0))
    events = []

    class Logger:
        def error(self, event, **_kw):
            events.append(event)

    def missing():
        raise ImportError("retrain unavailable")

    monkeypatch.setattr("churn.orchestration.scheduler._load_retrain", missing)
    monkeypatch.setattr("churn.orchestration.scheduler.get_logger", lambda *_a, **_k: Logger())
    assert worker.retrain() is None
    assert events == ["retrain_import_failed"]
    assert _load_retrain()[0].__name__ == "retrain"
