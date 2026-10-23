"""`churn` command line: migrate, synth, train, evaluate, cycle, simulate."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from churn import runtime_config
from churn.feedback.outcomes import advance
from churn.model.registry import ModelError
from churn.model.train import train as train_model
from churn.orchestration.runner import RunFailed, run_cycle
from churn.settings import Settings
from churn.synth.generator import generate
from churn.warehouse.duckdb import open_warehouse
from churn.warehouse.migrate import apply_migrations

app = typer.Typer(help="Churn prediction pipeline", no_args_is_help=True)

DateArg = Annotated[datetime, typer.Argument(formats=["%Y-%m-%d"])]


def _open():
    settings = Settings.load()
    wh = open_warehouse(settings)
    apply_migrations(wh, settings)
    return settings, wh


@app.command()
def migrate() -> None:
    """Apply pending schema migrations."""
    settings = Settings.load()
    wh = open_warehouse(settings)
    applied = apply_migrations(wh, settings)
    typer.echo(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")


@app.command()
def bootstrap(reset: bool = typer.Option(False, help="Overwrite edited AGENT_CONFIG rows with the defaults")) -> None:
    """Migrate, then seed CHURN_OPS.AGENT_CONFIG from config/runtime_defaults.yaml (keeps edits)."""
    settings, wh = _open()
    n = runtime_config.seed(wh, settings, overwrite=reset)
    typer.echo(f"AGENT_CONFIG seeded ({n} default keys{', overwritten' if reset else ', existing kept'})")


@app.command()
def synth(
    scale: str = typer.Option("tiny", help="tiny | small | default | full"),
    profile: bool = typer.Option(False, help="Write docs/data-profile.md and CHURN_OPS.DATA_PROFILE"),
) -> None:
    """Write all eight synthetic source domains and latent truth into THESIS_SYNTH."""
    settings, wh = _open()
    counts = generate(wh, settings, scale=scale)
    if profile:
        from churn.synth.profile import write_profile

        write_profile(wh, settings, scale)
    typer.echo(f"synthesised {counts['subscriptions']} subscriptions, {counts['orders']} orders")


@app.command()
def train(as_of: datetime | None = typer.Option(None, formats=["%Y-%m-%d"], help="Training data end date")) -> None:
    """Fit and register a calibrated model on point-in-time snapshots."""
    settings, wh = _open()
    version = train_model(wh, settings, as_of.date() if as_of else None)
    typer.echo(f"promoted model {version}")


@app.command()
def evaluate(
    scale: str | None = typer.Option(
        None, help="Synthetic scale of the warehouse population, to judge archetypes (skipped when omitted)"
    ),
    docs_dir: Path | None = typer.Option(None, help="Where to write eval-report.md and model-card.md (default docs/)"),
) -> None:
    """Score the 3-month time holdout; write EVAL_REPORTS, docs/eval-report.md and docs/model-card.md."""
    from churn.model.evaluate import evaluate as run_evaluation

    settings, wh = _open()
    try:
        result = run_evaluation(wh, settings, docs_dir=docs_dir, scale=scale)
    except ModelError as exc:
        typer.echo(f"evaluation FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    for t in result.targets:
        line = f"{t.requirement} {t.name}: {t.status} (measured {t.value:.3f}, target {t.op} {t.target:.2f})"
        typer.echo(line + (f" - {t.reason}" if t.reason else ""))


def record_cycle_duration(wh, settings: Settings, run_id: str) -> float:
    """Copy this run's RUNS.duration_s into RUN_METRICS. The runner already stored it on RUNS."""
    ops = settings.schemas.ops
    duration = float(wh.query(
        f"SELECT duration_s FROM {ops}.RUNS WHERE run_id = :r", {"r": run_id},
    ).iloc[0]["duration_s"])
    wh.execute(
        f"DELETE FROM {ops}.RUN_METRICS WHERE run_id = :r AND metric = 'duration_s'",
        {"r": run_id},
    )
    wh.execute(
        f"INSERT INTO {ops}.RUN_METRICS (run_id, metric, value, recorded_at) "
        "VALUES (:r, 'duration_s', :v, :t)",
        {"r": run_id, "v": duration, "t": datetime.now()},
    )
    return duration


@app.command()
def cycle(
    as_of: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Scoring date"),
    run_id: str | None = typer.Option(None, help="Defaults to scheduled-YYYYMMDD"),
    timed: bool = typer.Option(False, "--timed", help="Record the duration in RUNS and RUN_METRICS"),
) -> None:
    """Score every active subscriber into CHURN_OPS.RISK_SCORES."""
    settings, wh = _open()
    try:
        result = run_cycle(wh, settings, as_of.date(), run_id=run_id)
    except RunFailed as exc:
        typer.echo(f"run FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{result.run_id}: scored {result.scored} of {result.active_in_source} active subscribers")
    if timed:
        seconds = record_cycle_duration(wh, settings, result.run_id)
        typer.echo(f"duration {seconds:.1f}s")
    if result.status == "DEGRADED":
        typer.echo(f"run DEGRADED: gate_mode={result.gate_mode}, agent_halted={result.agent_halted}", err=True)


@app.command()
def simulate(
    days: int = typer.Option(..., "--days", help="Days to advance the clock"),
    as_of: datetime | None = typer.Option(
        None, formats=["%Y-%m-%d"], help="Day the clock starts on. Defaults to today."
    ),
) -> None:
    """Each day, append simulated responses for the outbox, then close outcome windows that have elapsed."""
    settings, wh = _open()
    start = date.today() if as_of is None else as_of.date()
    closed = advance(wh, settings, start, days)
    typer.echo(f"simulated {days} days from {start.isoformat()}; closed {closed} outcomes")


if __name__ == "__main__":
    app()
