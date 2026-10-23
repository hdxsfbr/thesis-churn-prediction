"""Run API. Approval stays in the warehouse; this process only starts and reads runs."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime

import duckdb
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from churn.model.registry import ModelError, load_promoted
from churn.orchestration.runner import RunFailed, default_run_id, run_cycle
from churn.runtime_config import load as load_config
from churn.settings import Settings
from churn.warehouse.base import Warehouse
from churn.warehouse.migrate import SOURCES


class RunRequest(BaseModel):
    kind: str = "manual"
    as_of: date | None = None
    run_id: str | None = None


def _frame(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _migrations_pending(wh: Warehouse, settings: Settings) -> list[str]:
    ops = settings.schemas.ops
    recorded = set(wh.query(f"SELECT version FROM {ops}.SCHEMA_MIGRATIONS")["version"])
    pending = []
    for directory, prefix in SOURCES:
        for path in sorted((settings.sql_dir / directory).glob("V*__*.sql")):
            version = prefix + path.stem.split("__")[0]
            if version not in recorded:
                pending.append(version)
    return pending


def readiness(wh: Warehouse, settings: Settings) -> list[str]:
    """Reasons the process is not ready. Empty means the warehouse, migrations and model are in place."""
    reasons: list[str] = []
    try:
        wh.query("SELECT 1 AS ok")
    except (duckdb.Error, OSError, RuntimeError) as exc:
        return [f"warehouse unreachable: {exc}"]
    try:
        pending = _migrations_pending(wh, settings)
    except (duckdb.Error, OSError, RuntimeError) as exc:
        reasons.append(f"migrations unreadable: {exc}")
    else:
        if pending:
            reasons.append("migrations pending: " + ", ".join(pending))
    try:
        load_promoted(wh, settings)
    except ModelError:
        reasons.append("promoted model missing")
    return reasons


def create_app(settings: Settings, wh: Warehouse) -> FastAPI:
    app = FastAPI(title="churn")
    app.state.settings = settings
    app.state.wh = wh

    @app.middleware("http")
    async def require_api_key(request, call_next):
        expected = os.environ.get("CHURN_API_KEY", "")
        if not expected or request.headers.get("x-api-key") != expected:
            return JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        reasons = readiness(wh, settings)
        body = {"ready": not reasons, "reasons": reasons}
        return JSONResponse(body, status_code=200 if not reasons else 503)

    @app.post("/runs")
    def start_run(body: RunRequest) -> dict:
        as_of = datetime.combine(body.as_of, datetime.min.time()) if body.as_of else datetime.now()
        run_id = body.run_id or default_run_id(as_of, body.kind)

        def _go() -> None:
            try:
                run_cycle(wh, settings, as_of, run_id=run_id, kind=body.kind)
            except RunFailed:
                return

        threading.Thread(target=_go, name=f"run-{run_id}", daemon=True).start()
        return {"run_id": run_id}

    @app.get("/runs")
    def list_runs() -> list[dict]:
        ops = settings.schemas.ops
        rows = wh.query(f"SELECT * FROM {ops}.RUNS ORDER BY started_at")
        return _frame(rows)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        ops = settings.schemas.ops
        row = wh.query(f"SELECT * FROM {ops}.RUNS WHERE run_id = :r", {"r": run_id})
        if row.empty:
            return JSONResponse({"detail": "run not found"}, status_code=404)
        steps = wh.query(f"SELECT * FROM {ops}.RUN_STEPS WHERE run_id = :r ORDER BY step, shard", {"r": run_id})
        return {"run": _frame(row)[0], "steps": _frame(steps)}

    @app.get("/runs/{run_id}/metrics")
    def get_metrics(run_id: str) -> list[dict]:
        ops = settings.schemas.ops
        rows = wh.query(f"SELECT metric, value, recorded_at FROM {ops}.RUN_METRICS WHERE run_id = :r", {"r": run_id})
        return _frame(rows)

    @app.get("/config/runtime")
    def runtime() -> dict:
        cfg = load_config(wh, settings)
        return {
            "valid": cfg.valid,
            "gate_mode": cfg.gate_mode,
            "agent_halt": cfg.agent_halt,
            "errors": cfg.errors,
        }

    return app
