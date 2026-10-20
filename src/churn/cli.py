"""`churn` command line: migrate, synth, train, cycle."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import typer

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
def synth(
    scale: str = typer.Option("tiny", help="tiny | small | default"),
    profile: bool = typer.Option(False, "--profile", help="Also write docs/data-profile.md and CHURN_OPS.DATA_PROFILE"),
) -> None:
    """Write the synthetic source (all contract views) into THESIS_SYNTH."""
    settings, wh = _open()
    counts = generate(wh, settings, scale=scale)
    typer.echo(f"synthesised {counts['subscriptions']} subscriptions, {counts['orders']} orders")
    if profile:
        from churn.synth.profile import write_profile

        report = write_profile(wh, settings)
        typer.echo(f"profile written: {sum(report.view_rows.values())} rows across {len(report.view_rows)} views")


@app.command()
def train(as_of: datetime | None = typer.Option(None, formats=["%Y-%m-%d"], help="Training data end date")) -> None:
    """Fit and register a calibrated model on point-in-time snapshots."""
    settings, wh = _open()
    version = train_model(wh, settings, as_of.date() if as_of else None)
    typer.echo(f"promoted model {version}")


@app.command()
def cycle(
    as_of: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Scoring date"),
    run_id: str | None = typer.Option(None, help="Defaults to scheduled-YYYYMMDD"),
) -> None:
    """Score every active subscriber into CHURN_OPS.RISK_SCORES."""
    settings, wh = _open()
    try:
        result = run_cycle(wh, settings, as_of.date(), run_id=run_id)
    except RunFailed as exc:
        typer.echo(f"run FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{result.run_id}: scored {result.scored} of {result.active_in_source} active subscribers")


if __name__ == "__main__":
    app()
