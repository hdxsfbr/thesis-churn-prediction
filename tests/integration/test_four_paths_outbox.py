"""T11: each intervention type produces a valid OUTBOX row that the dispatcher writes."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from churn.agent.content import build_content
from churn.agent.context import AgentContext
from churn.agent.cycle import Decision, decision_id
from churn.execution.outbox import dispatch
from churn.interventions.base import load_registry


def _decision(name, c):
    t = load_registry()[name]
    return Decision(decision_id(c.run_id, c.subscriber_key), c, t, t.action(c), build_content(t, c))


def test_each_type_writes_valid_outbox_row(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    as_of = datetime(2025, 6, 30)
    profiles = {
        "checkin_email": {},
        "formulation": {"has_quiz": False, "top_blend_goal": "energy"},
        "retention_offer": {"max_discount_pct": 20},
        "escalation": {"risk": 0.92, "tier": "critical"},
    }
    decisions = []
    for name, extra in profiles.items():
        kw = {"run_id": "t11-run", "subscriber_key": f"sub-{name}", "risk": 0.6, "tier": "high", "as_of": as_of}
        kw.update(extra)
        decisions.append(_decision(name, AgentContext(**kw)))
    assert dispatch(wh, settings, decisions) == 4

    out = wh.query("SELECT * FROM CHURN_OPS.OUTBOX WHERE outbox_id IN (SELECT decision_id FROM "
                   "CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't11-run')")
    rows = {r.intervention_type: r for r in out.itertuples(index=False)}
    assert set(rows) == set(profiles)
    for r in rows.values():
        assert r.status == "READY" and r.subject and r.body and r.subscriber_key
    assert rows["retention_offer"].offer_code
    assert 0 <= rows["retention_offer"].discount_pct <= 20
    assert rows["escalation"].channel == "human_queue"
    assert rows["escalation"].escalation_reason
    assert rows["formulation"].channel == "email"
    assert pd.isna(rows["checkin_email"].offer_code)
