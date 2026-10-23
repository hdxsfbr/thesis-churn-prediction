"""Seeded efficacy outcomes pull later selections toward the arm that retains."""

import json
from datetime import date, datetime, timedelta

import pandas as pd

from churn.agent.policy import PolicyConfig, PolicyContext, select
from churn.feedback.policy_stats import update_posteriors
from churn.interventions.base import load_registry
from churn.orchestration.runner import run_cycle
from churn.runtime_config import load_pinned, seed
from churn.warehouse.migrate import apply_migrations

AS_OF = date(2025, 6, 30)
BUCKET = "high:90-365"
ARCHETYPE = "efficacy_dissatisfied"
ARMS = ("formulation", "checkin_email")


def _policy(cfg):
    raw = cfg.policy.model_dump()
    for arm in ARMS:
        raw["priors"][ARCHETYPE][arm] = [20.0, 20.0]
    raw["max_explore_share"] = 1.0
    return PolicyConfig(**raw)


def _share(policy, stats, n=400):
    registry = {name: load_registry()[name] for name in ARMS}
    chosen = [
        select(
            PolicyContext(
                run_id="t17-share",
                subscriber_key=f"s{i}",
                risk=0.6,
                tier="high",
                as_of=datetime(2025, 6, 30),
                archetype=ARCHETYPE,
                archetype_conf=0.95,
                tenure_days=180,
                email_open_rate=1.0,
            ),
            registry,
            policy,
            stats,
        ).chosen
        for i in range(n)
    ]
    return chosen.count("formulation") / n


def _wave(wave, start):
    rows = []
    plan = {
        "formulation": ["retained"] * 4 + ["churned"],
        "checkin_email": ["retained"] + ["churned"] * 4,
    }
    moment = datetime(2025, 3, 1) + timedelta(days=start)
    for arm, outcomes in plan.items():
        for i, outcome in enumerate(outcomes):
            rows.append({
                "decision_id": f"t17-{wave}-{arm}-{i}",
                "archetype": ARCHETYPE,
                "intervention_type": arm,
                "policy_scores": json.dumps({arm: {"bucket": BUCKET}}),
                "outcome": outcome,
                "execution_status": "DISPATCHED",
                "outcome_at": moment + timedelta(hours=i),
            })
    return rows


def _stats(wh, settings):
    ops = settings.schemas.ops
    frame = wh.query(f"SELECT * FROM {ops}.POLICY_STATS")
    return {
        (row.archetype, row.intervention_type, row.context_bucket): (row.alpha, row.beta)
        for row in frame.itertuples(index=False)
    }


def test_selection_shifts_toward_better_intervention(fresh_wh, settings):
    apply_migrations(fresh_wh, settings)
    seed(fresh_wh, settings)
    cfg = load_pinned(fresh_wh, settings, "t17-shift")
    policy = _policy(cfg)
    tuned = cfg.model_copy(update={"policy": policy})
    ops = settings.schemas.ops
    before = _share(policy, {})
    fresh_wh.insert_frame(f"{ops}.INTERVENTION_LOG", pd.DataFrame(_wave(0, 0)))
    update_posteriors(fresh_wh, settings, "t17-wave-0", tuned)
    mid = _share(policy, _stats(fresh_wh, settings))
    fresh_wh.insert_frame(f"{ops}.INTERVENTION_LOG", pd.DataFrame(_wave(1, 10)))
    update_posteriors(fresh_wh, settings, "t17-wave-1", tuned)
    later = _share(policy, _stats(fresh_wh, settings))
    folded = wh_rates(fresh_wh, settings)
    assert folded == {"formulation": 0.8, "checkin_email": 0.2}
    assert later > mid > before


def wh_rates(wh, settings):
    ops = settings.schemas.ops
    frame = wh.query(
        f"""
        SELECT intervention_type,
               AVG(CASE WHEN outcome = 'retained' THEN 1.0 ELSE 0.0 END) AS rate
        FROM {ops}.INTERVENTION_LOG
        WHERE execution_status = 'DISPATCHED' AND outcome IN ('retained', 'churned')
        GROUP BY intervention_type
        """
    )
    return {row.intervention_type: row.rate for row in frame.itertuples(index=False)}


def test_cycle_folds_a_closed_outcome_after_decide(pipeline):
    """This run's decide step keeps the posteriors it loaded. The update step writes the next run's."""
    wh, settings = pipeline["wh"], pipeline["settings"]
    ops = settings.schemas.ops
    decision_id = "t17-pinned"
    run_id = "t17-cycle"
    wh.insert_frame(
        f"{ops}.INTERVENTION_LOG",
        pd.DataFrame([{
            "decision_id": decision_id,
            "archetype": ARCHETYPE,
            "intervention_type": "formulation",
            "policy_scores": json.dumps({"formulation": {"bucket": BUCKET}}),
            "outcome": "retained",
            "execution_status": "DISPATCHED",
            "outcome_at": datetime(2025, 5, 1),
        }]),
    )
    try:
        result = run_cycle(wh, settings, AS_OF, run_id=run_id)
        assert result.status == "SUCCEEDED"
        row = wh.query(
            f"""
            SELECT alpha, beta, n, last_updated_run FROM {ops}.POLICY_STATS
            WHERE archetype = :a AND intervention_type = 'formulation' AND context_bucket = :b
            """,
            {"a": ARCHETYPE, "b": BUCKET},
        )
        assert len(row) == 1
        assert (row.iloc[0]["alpha"], row.iloc[0]["beta"], int(row.iloc[0]["n"])) == (81, 20, 1)
        assert row.iloc[0]["last_updated_run"] == run_id
    finally:
        wh.execute(
            f"""
            DELETE FROM {ops}.POLICY_STATS
            WHERE archetype = :a AND intervention_type = 'formulation' AND context_bucket = :b
            """,
            {"a": ARCHETYPE, "b": BUCKET},
        )
        wh.execute(f"DELETE FROM {ops}.INTERVENTION_LOG WHERE decision_id = :d", {"d": decision_id})
        ids = wh.query(
            f"SELECT decision_id FROM {ops}.INTERVENTION_LOG WHERE run_id = :r", {"r": run_id}
        )
        wh.execute(f"DELETE FROM {ops}.COVERAGE_GAPS WHERE run_id = :r", {"r": run_id})
        wh.execute(f"DELETE FROM {ops}.RISK_SCORES WHERE run_id = :r", {"r": run_id})
        wh.execute(f"DELETE FROM {ops}.RUNS WHERE run_id = :r", {"r": run_id})
        wh.execute(f"DELETE FROM {ops}.CONFIG_SNAPSHOTS WHERE run_id = :r", {"r": run_id})
        if not ids.empty:
            for item in ids["decision_id"]:
                wh.execute(f"DELETE FROM {ops}.OUTBOX WHERE outbox_id = :d", {"d": item})
                wh.execute(f"DELETE FROM {ops}.APPROVALS WHERE decision_id = :d", {"d": item})
                wh.execute(f"DELETE FROM {ops}.INTERVENTION_LOG WHERE decision_id = :d", {"d": item})
                wh.execute(f"DELETE FROM {ops}.OUTCOMES WHERE decision_id = :d", {"d": item})
