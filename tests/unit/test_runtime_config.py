"""Acceptance tests for T5: runtime config validation and fail-safe behaviour."""

from __future__ import annotations

import json
from datetime import date

import pytest

from churn import runtime_config as rc
from churn.orchestration.runner import run_cycle
from churn.warehouse.migrate import apply_migrations

AS_OF = date(2025, 6, 30)


def set_key(wh, key, value):
    wh.execute("DELETE FROM CHURN_OPS.AGENT_CONFIG WHERE key = :k", {"k": key})
    if value is not None:
        wh.execute(
            "INSERT INTO CHURN_OPS.AGENT_CONFIG (key, value_json, updated_by, updated_at) "
            "VALUES (:k, :v, 'test', CURRENT_TIMESTAMP)",
            {"k": key, "v": value if isinstance(value, str) else json.dumps(value)},
        )


@pytest.fixture()
def cfg_wh(pipeline):
    """The shared pipeline warehouse, with AGENT_CONFIG reset to the defaults afterwards."""
    wh, settings = pipeline["wh"], pipeline["settings"]
    rc.seed(wh, settings, overwrite=True)
    yield wh, settings
    rc.seed(wh, settings, overwrite=True)


def test_defaults_are_valid(fresh_wh, settings):
    apply_migrations(fresh_wh, settings)
    rc.seed(fresh_wh, settings)
    cfg = rc.load(fresh_wh, settings)
    assert cfg.valid and cfg.gate_mode == "normal" and not cfg.agent_halt
    assert cfg.tiers.boundaries == {"medium": 0.20, "high": 0.45, "critical": 0.70}
    assert cfg.gate.t_risk == 0.85 and cfg.gate.t_cost_usd == 15 and cfg.gate.t_discount_pct == 20
    assert cfg.guardrails.max_discount_pct == 25
    assert cfg.holdout.share == 0.10


def test_seed_preserves_edits(fresh_wh, settings):
    apply_migrations(fresh_wh, settings)
    rc.seed(fresh_wh, settings)
    set_key(fresh_wh, "gate", {"t_risk": 0.5, "t_cost_usd": 1, "t_discount_pct": 5, "always_approve": []})
    rc.seed(fresh_wh, settings)
    assert rc.load(fresh_wh, settings).gate.t_risk == 0.5


def test_assign_tiers_boundaries():
    t = rc.TierConfig(boundaries={"medium": 0.2, "high": 0.45, "critical": 0.7})
    assert rc.assign_tiers([0.0, 0.1999, 0.2, 0.449, 0.45, 0.7, 1.0], t) == [
        "low", "low", "medium", "medium", "high", "critical", "critical",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"t_risk": 1.5, "t_cost_usd": 15, "t_discount_pct": 20, "always_approve": []},
        {"t_risk": "high", "t_cost_usd": 15, "t_discount_pct": 20, "always_approve": []},
        {"t_risk": 0.85, "t_cost_usd": -1, "t_discount_pct": 20, "always_approve": []},
        "not json{",
    ],
)
def test_invalid_thresholds_yield_approve_all_and_degraded(cfg_wh, bad):
    wh, settings = cfg_wh
    set_key(wh, "gate", bad)
    cfg = rc.load(wh, settings)
    assert cfg.gate_mode == "approve_all"
    assert not cfg.agent_halt
    assert not cfg.valid and any(e.startswith("gate") for e in cfg.errors)

    result = run_cycle(wh, settings, AS_OF, run_id="t-cfg-degraded")
    assert result.status == "DEGRADED"
    assert result.gate_mode == "approve_all" and not result.agent_halted
    run = wh.query("SELECT * FROM CHURN_OPS.RUNS WHERE run_id = 't-cfg-degraded'").iloc[0]
    assert run["status"] == "DEGRADED" and run["error_class"] == "ConfigError"
    assert run["scored"] == run["active_in_source"] > 0
    snap = wh.query("SELECT * FROM CHURN_OPS.CONFIG_SNAPSHOTS WHERE run_id = 't-cfg-degraded'").iloc[0]
    assert not snap["valid"] and snap["gate_mode"] == "approve_all"
    assert run["config_snapshot_id"] == "t-cfg-degraded"
    # Tiers are still valid, so scoring still assigns them.
    tiers = wh.query("SELECT DISTINCT tier FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-cfg-degraded'")
    assert tiers["tier"].notna().all()


def test_approval_grace_parsed_defaults_and_rejects_negative(cfg_wh):
    wh, settings = cfg_wh
    assert rc.load(wh, settings).guardrails.approval_grace_h == 24
    base = {"contact_cap_14d": 1, "contact_cap_90d": 3, "max_discount_pct": 25,
            "offer_catalogue": ["FREE_SHIPPING"], "approval_expiry_h": 72}
    set_key(wh, "guardrails", {**base, "approval_grace_h": 6.5})
    assert rc.load(wh, settings).guardrails.approval_grace_h == 6.5
    set_key(wh, "guardrails", base)  # absent: default
    assert rc.load(wh, settings).guardrails.approval_grace_h == 24
    set_key(wh, "guardrails", {**base, "approval_grace_h": -1})
    assert rc.load(wh, settings).agent_halt


@pytest.mark.parametrize(
    "key,bad",
    [
        ("tiers", {"boundaries": {"medium": 0.5, "high": 0.4, "critical": 0.7}, "at_risk_from": "medium"}),
        ("tiers", {"boundaries": {"medium": 0.2, "high": 0.45, "critical": 1.4}, "at_risk_from": "medium"}),
        ("tiers", {"boundaries": {"medium": 0.2, "high": 0.45, "critical": 0.7}, "at_risk_from": "nope"}),
        ("guardrails", {"contact_cap_14d": 0, "contact_cap_90d": 3, "max_discount_pct": 25,
                        "offer_catalogue": ["FREE_SHIPPING"], "approval_expiry_h": 72}),
        ("guardrails", {"contact_cap_14d": 1, "contact_cap_90d": 3, "max_discount_pct": 25,
                        "offer_catalogue": [], "approval_expiry_h": 72}),
        ("guardrails", "garbage"),
    ],
)
def test_invalid_tiers_or_guardrails_halt_agent(cfg_wh, key, bad):
    wh, settings = cfg_wh
    set_key(wh, key, bad)
    cfg = rc.load(wh, settings)
    assert cfg.agent_halt
    assert cfg.gate_mode == "normal"  # thresholds themselves are fine
    assert any(e.startswith(key) for e in cfg.errors)

    result = run_cycle(wh, settings, AS_OF, run_id=f"t-cfg-halt-{key}")
    assert result.agent_halted
    assert result.status == "DEGRADED"
    assert result.scored == result.active_in_source > 0  # scores are still written
    snap = wh.query(
        "SELECT agent_halt FROM CHURN_OPS.CONFIG_SNAPSHOTS WHERE run_id = :r", {"r": f"t-cfg-halt-{key}"}
    ).iloc[0]
    assert snap["agent_halt"]
    if key == "tiers":
        tiers = wh.query("SELECT tier FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r", {"r": result.run_id})
        assert tiers["tier"].isna().all()  # no tier is invented from a bad boundary


def test_missing_keys_fall_back_failsafe_not_autonomous(cfg_wh):
    wh, settings = cfg_wh
    # Missing gate thresholds: approve everything.
    set_key(wh, "gate", None)
    cfg = rc.load(wh, settings)
    assert cfg.gate is None and cfg.gate_mode == "approve_all"
    assert not cfg.agent_halt

    # Missing guardrails: halt.
    rc.seed(wh, settings, overwrite=True)
    set_key(wh, "guardrails", None)
    assert rc.load(wh, settings).agent_halt

    # Empty table: never autonomous, never silently defaulted.
    wh.execute("DELETE FROM CHURN_OPS.AGENT_CONFIG")
    cfg = rc.load(wh, settings)
    assert cfg.gate_mode == "approve_all" and cfg.agent_halt and not cfg.valid
    assert {"gate", "tiers", "guardrails"} <= {e.split(":")[0] for e in cfg.errors}
    assert cfg.tiers is None and cfg.guardrails is None

    result = run_cycle(wh, settings, AS_OF, run_id="t-cfg-empty")
    assert result.status == "DEGRADED" and result.agent_halted and result.gate_mode == "approve_all"
