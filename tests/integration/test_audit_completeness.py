"""Every intervention written by a cycle has the R6.1 fields filled in."""

from datetime import date

from churn.feedback.metrics import AUDIT_FIELDS, audit_completeness
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)


def test_every_intervention_has_complete_log_row(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    result = run_cycle(wh, settings, AS_OF, run_id="t19-audit")
    assert result.status == "SUCCEEDED"
    log = wh.query(
        f"SELECT * FROM {settings.schemas.ops}.INTERVENTION_LOG WHERE run_id = :r",
        {"r": result.run_id},
    )
    assert not log.empty
    assert audit_completeness(log) == 1
    for column in AUDIT_FIELDS:
        assert column in log.columns
    stored = wh.query(
        f"""
        SELECT value FROM {settings.schemas.ops}.RUN_METRICS
        WHERE run_id = :r AND metric = 'audit_completeness'
        """,
        {"r": result.run_id},
    )
    assert float(stored.iloc[0]["value"]) == 1
