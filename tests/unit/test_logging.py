"""Acceptance test for T9: log lines are JSON carrying run_id, component and step."""

from __future__ import annotations

import io
import json
from datetime import datetime

import structlog

from churn.obs.errors import ModelError, classify
from churn.obs.logging import configure_logging, get_logger


def test_log_lines_parse_as_json_with_run_id_and_component():
    buf = io.StringIO()
    configure_logging(stream=buf)
    log = get_logger("runner", run_id="r-1", step="score")
    log.info("step_end", rows=3)
    log.error("step_failed", error_class="ModelError")

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2
    first, second = (json.loads(ln) for ln in lines)
    for rec in (first, second):
        assert rec["run_id"] == "r-1"
        assert rec["component"] == "runner"
        assert rec["step"] == "score"
        assert rec["level"] and rec["event"]
        assert datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
    assert first["level"] == "info" and first["rows"] == 3
    assert second["level"] == "error" and second["error_class"] == "ModelError"


def test_pii_is_redacted():
    buf = io.StringIO()
    configure_logging(stream=buf)
    get_logger("agent", run_id="r-2").info("note", detail="mail jo@example.com or +1 415 555 0100")
    rec = json.loads(buf.getvalue().splitlines()[0])
    assert "jo@example.com" not in rec["detail"]
    assert "555 0100" not in rec["detail"]


def test_iso_dates_pass_through_unchanged():
    buf = io.StringIO()
    configure_logging(stream=buf)
    get_logger("runner", run_id="r").info("run_start", as_of="2025-06-30T00:00:00", day="2025-06-30")
    rec = json.loads(buf.getvalue().splitlines()[0])
    assert rec["as_of"] == "2025-06-30T00:00:00"
    assert rec["day"] == "2025-06-30"
    assert datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))


def test_unconfigured_get_logger_defaults_to_json_with_redaction(capsys):
    structlog.reset_defaults()
    try:
        get_logger("runner", run_id="r").info("hi", detail="jo@example.com")
        out = capsys.readouterr().out
    finally:
        structlog.reset_defaults()  # don't leave a captured stream configured
    rec = json.loads(out.splitlines()[0])
    assert rec["detail"] == "[redacted-email]"
    assert rec["component"] == "runner"


def test_classify_taxonomy():
    assert classify(ModelError("x")) == "ModelError"
    assert classify(ValueError("x")) == "ValueError"
