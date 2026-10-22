"""Acceptance test for T14 / R5.1 + R5.2: thresholds live in AGENT_CONFIG and apply from the next run."""

from __future__ import annotations

import json
from datetime import date

from churn import runtime_config as rc
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)
AT_RISK = ("medium", "high", "critical")


def set_gate(wh, t_risk):
    gate = {"t_risk": t_risk, "t_cost_usd": 15, "t_discount_pct": 20, "always_approve": []}
    wh.execute("UPDATE CHURN_OPS.AGENT_CONFIG SET value_json = :v WHERE key = 'gate'", {"v": json.dumps(gate)})


def routing(wh, run_id):
    q = (
        "SELECT (SELECT COUNT(*) FROM CHURN_OPS.APPROVALS WHERE run_id = :r) AS pending, "
        "(SELECT COUNT(*) FROM CHURN_OPS.OUTBOX o JOIN CHURN_OPS.INTERVENTION_LOG l ON l.decision_id = o.outbox_id "
        " WHERE l.run_id = :r) AS outbox"
    )
    row = wh.query(q, {"r": run_id}).iloc[0]
    return int(row["pending"]), int(row["outbox"])


def test_editing_agent_config_threshold_changes_next_run_routing(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    rc.seed(wh, settings, overwrite=True)
    try:
        set_gate(wh, 1.0)  # only a score of exactly 1.0 reaches it
        first = run_cycle(wh, settings, AS_OF, run_id="t-thr-before")
        assert first.at_risk > 0
        scores = wh.query(
            "SELECT churn_prob FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-thr-before' AND tier IN "
            "('medium', 'high', 'critical')"
        )["churn_prob"]

        def split(threshold, run_id):
            # Escalation is already a human hand-off and bypasses thresholds (T14).
            log = wh.query('SELECT risk, intervention_type, params FROM CHURN_OPS.INTERVENTION_LOG '
                           'WHERE run_id = :r', {'r': run_id})
            above = int(((log['risk'] >= threshold) & (log['intervention_type'] != 'escalation')).sum())
            return above, len(log) - above

        assert routing(wh, "t-thr-before") == split(1.0, 't-thr-before')

        lowest = float(scores.min())
        set_gate(wh, lowest)  # now every at-risk subscriber is at or above the threshold
        second = run_cycle(wh, settings, AS_OF, run_id="t-thr-after")
        assert second.status == "SUCCEEDED"
        assert routing(wh, "t-thr-after") == split(lowest, 't-thr-after')
        assert routing(wh, "t-thr-after")[0] > routing(wh, "t-thr-before")[0]  # the edit changed the routing

        # A threshold in the middle splits the batch exactly by risk.
        mid = float(scores.median())
        set_gate(wh, mid)
        run_cycle(wh, settings, AS_OF, run_id="t-thr-mid")
        assert routing(wh, "t-thr-mid") == split(mid, 't-thr-mid')

        # The first run's pinned snapshot is unchanged by later edits: a replay routes like before.
        run_cycle(wh, settings, AS_OF, run_id="t-thr-before")
        assert routing(wh, "t-thr-before") == split(1.0, 't-thr-before')
    finally:
        rc.seed(wh, settings, overwrite=True)
