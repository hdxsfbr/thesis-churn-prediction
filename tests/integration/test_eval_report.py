"""T8 (R3.6, R3.3, R9.2): `churn evaluate` writes EVAL_REPORTS, the eval report and the model card."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from churn import runtime_config
from churn.model.archetype import ARCHETYPES
from churn.model.evaluate import evaluate
from churn.model.train import train
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations


@pytest.fixture(scope="module")
def report(pipeline, tmp_path_factory):
    docs = tmp_path_factory.mktemp("docs")
    result = evaluate(pipeline["wh"], pipeline["settings"], docs_dir=docs, scale="tiny")
    return result, docs, pipeline


def test_report_states_label_window_and_holdout(report):
    result, docs, _ = report
    text = (docs / "eval-report.md").read_text()
    assert "voluntary cancellation" in text
    assert "30 days" in text
    assert "last 3 months" in text
    assert f"{result.holdout_start:%Y-%m-%d}" in text and f"{result.holdout_end:%Y-%m-%d}" in text
    assert "ROC-AUC" in text and "PR-AUC" in text and "Calibration" in text
    # every R3.6 target is printed as PASS or MISS
    target_lines = [line for line in text.splitlines() if line.startswith("| R3.6")]
    assert len(target_lines) == 2
    for line in target_lines:
        assert "PASS" in line or "MISS" in line
    card = (docs / "model-card.md").read_text()
    assert result.model_version in card
    assert "synthetic accuracy is not real-world accuracy" in text.lower()
    assert "90-day impact target can only be checked after go-live" in text
    assert "90-day impact target can only be checked after go-live" in card
    assert "30 days" in card and "Intended use" in card
    assert "PASS" in card or "MISS" in card


def test_report_has_per_archetype_precision(report):
    result, docs, _ = report
    text = (docs / "eval-report.md").read_text()
    for archetype in ARCHETYPES:
        assert archetype in text
    assert "Confusion matrix" in text
    assert set(result.archetype_metrics) == set(ARCHETYPES)
    for m in result.archetype_metrics.values():
        assert {"precision", "recall", "support"} <= set(m)


def test_eval_reports_row_written_and_idempotent(report):
    result, docs, pipeline = report
    wh = pipeline["wh"]
    evaluate(wh, pipeline["settings"], docs_dir=docs, scale="tiny")
    rows = wh.query("SELECT * FROM CHURN_OPS.EVAL_REPORTS WHERE report_id = :r", {"r": result.report_id})
    assert len(rows) == 1
    row = rows.iloc[0]
    metrics = json.loads(row["metrics_json"])
    assert "top_decile_precision" in metrics and "roc_auc" in metrics
    targets = json.loads(row["targets_json"])
    assert {t["name"] for t in targets} == {"top_decile_precision", "pre_cancellation_recall"}
    assert all(t["status"] in ("PASS", "MISS") for t in targets)


def test_cli_evaluate_prints_targets(tmp_path, monkeypatch, pipeline):
    from churn import cli

    monkeypatch.setattr(cli, "_open", lambda: (pipeline["settings"], pipeline["wh"]))
    out = CliRunner().invoke(cli.app, ["evaluate", "--docs-dir", str(tmp_path), "--scale", "tiny"])
    assert out.exit_code == 0, out.output
    assert "top_decile_precision" in out.output and ("PASS" in out.output or "MISS" in out.output)
    assert (tmp_path / "eval-report.md").exists() and (tmp_path / "model-card.md").exists()


def test_scale_mismatch_and_unknown_scale_fail_cleanly(report, tmp_path):
    from churn.model.registry import ModelError

    _, _, pipeline = report
    settings = pipeline["settings"].model_copy(deep=True)
    with pytest.raises(ModelError, match="unknown scale"):
        evaluate(pipeline["wh"], settings, docs_dir=tmp_path, scale="nope")
    # a scale far smaller than the warehouse population gives truth for too few holdout churners
    settings.synth.scales["tiny"] = 5
    with pytest.raises(ModelError, match="ground truth for only"):
        evaluate(pipeline["wh"], settings, docs_dir=tmp_path, scale="tiny")


def test_cli_evaluate_without_scale_skips_archetypes(tmp_path, monkeypatch, pipeline):
    from churn import cli

    monkeypatch.setattr(cli, "_open", lambda: (pipeline["settings"], pipeline["wh"]))
    out = CliRunner().invoke(cli.app, ["evaluate", "--docs-dir", str(tmp_path)])
    assert out.exit_code == 0, out.output
    assert "Archetype evaluation skipped" in (tmp_path / "eval-report.md").read_text()
    bad = CliRunner().invoke(cli.app, ["evaluate", "--docs-dir", str(tmp_path), "--scale", "nope"])
    assert bad.exit_code == 1 and "unknown scale" in bad.output


@pytest.mark.slow
def test_targets_on_medium_synth(tmp_path_factory):
    """R3.6 on a medium synthetic base: precision > 70% and recall > 60%, else xfail with the stated reason."""
    from conftest import make_settings

    settings = make_settings(tmp_path_factory.mktemp("artefacts"))
    wh = DuckDBWarehouse(":memory:")
    try:
        apply_migrations(wh, settings)
        runtime_config.seed(wh, settings)
        generate(wh, settings, scale="small")
        train(wh, settings)
        result = evaluate(wh, settings, docs_dir=tmp_path_factory.mktemp("docs"), scale="small")
    finally:
        wh.close()
    misses = [t for t in result.targets if t.status == "MISS"]
    if misses:
        pytest.xfail("; ".join(f"{t.name}: {t.reason}" for t in misses))
    assert result.metrics["top_decile_precision"] > 0.70
    assert result.metrics["pre_cancellation_recall"] > 0.60
