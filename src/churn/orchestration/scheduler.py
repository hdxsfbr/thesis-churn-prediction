"""Daily cycle and weekly retrain. A missed day is caught up once at start."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from churn.obs.logging import get_logger
from churn.orchestration.runner import RunResult, default_run_id, run_cycle
from churn.settings import Settings
from churn.warehouse.base import Warehouse

DONE = {"SUCCEEDED", "DEGRADED"}


def _now(clock: Callable[[], datetime] | None) -> datetime:
    if clock is None:
        return datetime.now()
    return clock()


def _day(moment: datetime | date) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def finished(wh: Warehouse, settings: Settings, run_id: str) -> bool:
    ops = settings.schemas.ops
    rows = wh.query(f"SELECT status FROM {ops}.RUNS WHERE run_id = :r", {"r": run_id})
    return not rows.empty and str(rows.iloc[0]["status"]) in DONE


class Worker:
    """BlockingScheduler plus one catch-up. Tests fire `cycle` directly with a stand-in clock."""

    def __init__(self, wh: Warehouse, settings: Settings, clock: Callable[[], datetime] | None = None) -> None:
        self.wh = wh
        self.settings = settings
        self.clock = clock
        zone = "UTC"
        self.scheduler = BlockingScheduler(timezone=zone)
        self.scheduler.add_job(
            self.cycle,
            CronTrigger.from_crontab(settings.schedule.cron, timezone=zone),
            id="cycle",
        )
        self.scheduler.add_job(
            self.retrain,
            CronTrigger.from_crontab(settings.schedule.retrain_cron, timezone=zone),
            id="retrain",
        )

    def cycle(self) -> RunResult:
        day = _day(_now(self.clock))
        result = run_cycle(self.wh, self.settings, day, kind="scheduled")
        self.retrain(force=False)
        return result

    def retrain(self, force: bool = True) -> tuple[str, str] | None:
        """Weekly cron retrains. A daily cycle retrains only when enough new outcomes have closed."""
        try:
            retrain, retrain_due = _load_retrain()
        except ImportError as exc:
            get_logger("worker").error("retrain_import_failed", error_msg=str(exc))
            return None
        if not hasattr(self.settings, "retrain"):
            return None
        if not force and not retrain_due(self.wh, self.settings):
            return None
        return retrain(self.wh, self.settings, train_end=_day(_now(self.clock)))

    def catch_up(self) -> RunResult | None:
        """Run today's scheduled cycle once when that run is missing or not finished."""
        day = _day(_now(self.clock))
        run_id = default_run_id(day, "scheduled")
        if finished(self.wh, self.settings, run_id):
            return None
        return self.cycle()

    def start(self) -> None:
        try:
            self.catch_up()
        except Exception as exc:  # noqa: BLE001 — a failed catch-up must not stop the scheduler
            get_logger("worker").error("catch_up_failed", error_msg=str(exc))
        self.scheduler.start()


def _load_retrain():
    from churn.feedback.retrain import retrain, retrain_due
    return retrain, retrain_due
