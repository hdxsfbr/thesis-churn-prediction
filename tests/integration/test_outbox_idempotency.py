"""Acceptance test for T10 / R4.5: replaying a run never duplicates outbox or log rows."""

from __future__ import annotations

from datetime import date

from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)


def test_replaying_run_creates_no_duplicate_outbox_rows(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    q_out = (
        "SELECT o.outbox_id, o.created_at, o.subject, o.body FROM CHURN_OPS.OUTBOX o "
        "JOIN CHURN_OPS.INTERVENTION_LOG l ON l.decision_id = o.outbox_id "
        "WHERE l.run_id = 't-replay' ORDER BY o.outbox_id"
    )
    q_log = "SELECT decision_id, decided_at, rationale FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't-replay' ORDER BY 1"
    run_cycle(wh, settings, AS_OF, run_id="t-replay")
    out1, log1 = wh.query(q_out), wh.query(q_log)
    assert len(out1) > 0

    again = run_cycle(wh, settings, AS_OF, run_id="t-replay")
    out2, log2 = wh.query(q_out), wh.query(q_log)

    assert len(out2) == len(out1)
    assert out2["outbox_id"].is_unique
    assert out1.equals(out2)  # insert-only: first write wins, created_at untouched
    assert log1.equals(log2)
    assert again.dispatched == len(out2)  # only rows written to the outbox count; gated ones are `queued`
    assert again.queued == again.decided - again.dispatched
    total = wh.query("SELECT COUNT(*) AS n, COUNT(DISTINCT outbox_id) AS d FROM CHURN_OPS.OUTBOX").iloc[0]
    assert total["n"] == total["d"]
