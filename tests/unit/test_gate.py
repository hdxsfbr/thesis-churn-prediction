"""Acceptance tests for T14 gate: thresholds from the config snapshot decide approval vs outbox (R5.1)."""

from __future__ import annotations

from datetime import datetime

from churn import runtime_config as rc
from churn.agent.context import AgentContext
from churn.agent.cycle import Decision, decision_id
from churn.agent.gate import apply_gate, evaluate
from churn.interventions.base import Action, Content, InterventionType

GATE = {"t_risk": 0.85, "t_cost_usd": 15, "t_discount_pct": 20, "always_approve": []}


class Fake(InterventionType):
    def __init__(self, name="fake", channel="email", cost=0.0):
        self.name, self.channel, self._cost = name, channel, cost

    def action(self, context):
        return Action()

    def template(self, context):
        return Content("s", "b", "template", "fake")

    def cost_usd(self, action):
        return self._cost


def make(risk=0.5, discount=None, cost=0.0, name="fake", channel="email", key="k1") -> Decision:
    ctx = AgentContext("r1", key, risk, "high", datetime(2025, 6, 30))
    itv = Fake(name, channel, cost)
    return Decision(decision_id("r1", key), ctx, itv, Action(discount_pct=discount), itv.template(ctx))


def config(gate=GATE, drop_gate=False):
    import json

    rows = {k: json.dumps(v) for k, v in rc.defaults().items()}
    rows["gate"] = json.dumps(gate)
    if drop_gate:
        del rows["gate"]
    return rc.parse(rows)


def test_above_threshold_pending_no_outbox_below_threshold_outbox():
    cfg = config()
    cases = {
        "below": make(0.5, key="a"),
        "risk": make(0.85, key="b"),  # boundary is inclusive
        "cost": make(0.1, cost=15, key="c"),
        "discount": make(0.1, discount=20, key="d"),
        "just_below": make(0.8499, discount=19.9, cost=14.99, key="e"),
    }
    gated = {k: apply_gate([d], cfg)[0] for k, d in cases.items()}
    needs = {k: g.gate.needs_approval for k, g in gated.items()}
    assert needs == {"below": False, "risk": True, "cost": True, "discount": True, "just_below": False}
    assert "risk" in gated["risk"].gate.reasons[0]

    # Pending decisions are logged as PENDING with no outbox id and no dispatch time.
    now = datetime(2025, 7, 1)
    pending = gated["risk"].log_row(now)
    assert pending["approval_status"] == "PENDING"
    assert pending["execution_status"] == "PENDING_APPROVAL"
    assert pending["outbox_id"] is None and pending["dispatched_at"] is None
    assert pending["payload_hash"]
    auto = gated["below"].log_row(now)
    assert auto["approval_status"] == "NOT_REQUIRED" and auto["execution_status"] == "DISPATCHED"
    assert auto["outbox_id"] == gated["below"].decision_id


def test_always_approve_type_and_escalation_handoff():
    cfg = config({**GATE, "always_approve": ["fake"]})
    assert evaluate(make(0.1), cfg).needs_approval
    # Escalation is a human hand-off: thresholds do not apply, always_approve still does.
    esc = make(0.99, name="escalation", channel="human_queue")
    assert not evaluate(esc, config()).needs_approval
    assert evaluate(esc, config({**GATE, "always_approve": ["escalation"]})).needs_approval


def test_approve_all_mode_routes_everything_to_approval():
    for cfg in (config(drop_gate=True), config({"t_risk": 5, "t_cost_usd": 1, "t_discount_pct": 5})):
        assert cfg.gate_mode == "approve_all"
        decisions = [make(0.0001, key="a"), make(0.2, key="b"), make(0.01, name="escalation", channel="human_queue", key="c")]
        gated = apply_gate(decisions, cfg)
        assert all(d.gate.needs_approval for d in gated)
        assert all(d.log_row(datetime(2025, 7, 1))["approval_status"] == "PENDING" for d in gated)
