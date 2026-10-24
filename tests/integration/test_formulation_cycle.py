"""The cycle logs formulation from the subscriber's quiz, or from purchase history when there is none."""

import json
from datetime import datetime

import pandas as pd

from churn.agent.cycle import run_agent
from churn.agent.policy import Selection
from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.execution.outbox import dispatch
from churn.runtime_config import HoldoutConfig, load_pinned

AS_OF = datetime(2025, 6, 30)
RUN = "submit-formulation"


def _latest_sku(quiz: pd.DataFrame, key: str):
    rows = quiz[quiz["subscriber_key"] == key]
    if rows.empty:
        return None
    return rows.sort_values("answered_at").iloc[-1]["recommended_sku"]


def _blend_goal(orders: pd.DataFrame, products: pd.DataFrame, key: str):
    rows = orders[(orders["subscriber_key"] == key) & (pd.to_datetime(orders["ordered_at"]) < AS_OF)]
    if rows.empty:
        return None
    counts = rows.groupby("blend_sku").size().sort_values(ascending=False)
    top = min(counts[counts == counts.iloc[0]].index)
    match = products.loc[products["blend_sku"] == top, "goal_category"]
    if match.empty or pd.isna(match.iloc[0]):
        return None
    return str(match.iloc[0])


def test_cycle_logs_quiz_and_purchase_history(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    ops = settings.schemas.ops
    ctes = contract_ctes(
        contract_for(settings), settings.key_pepper,
        ["quiz_responses", "orders", "products"], include_raw_id=True,
    )
    quiz = wh.query(
        f"WITH {ctes} SELECT subscriber_key, customer_id, recommended_sku, answered_at FROM quiz_responses"
    )
    orders = wh.query(f"WITH {ctes} SELECT subscriber_key, blend_sku, ordered_at FROM orders")
    products = wh.query(f"WITH {ctes} SELECT blend_sku, goal_category FROM products")
    both = set(quiz["subscriber_key"]) & set(orders["subscriber_key"])
    quiz_key = min(both)
    plain_key = max(both)
    assert quiz_key != plain_key
    plain_customer = quiz.loc[quiz["subscriber_key"] == plain_key, "customer_id"].iloc[0]
    physical = contract_for(settings).physical("quiz_responses")
    backup = wh.query(f"SELECT * FROM {physical} WHERE customer_id = :c", {"c": plain_customer})
    expected_sku = str(_latest_sku(quiz[quiz["subscriber_key"] == quiz_key], quiz_key))
    expected_goal = _blend_goal(orders, products, plain_key)
    assert expected_goal
    keys = [quiz_key, plain_key, "submit-neither"]
    prior = wh.query(
        f"SELECT * FROM {ops}.HOLDOUT_ASSIGNMENTS WHERE subscriber_key IN (:a, :b, :c)",
        {"a": keys[0], "b": keys[1], "c": keys[2]},
    )
    from churn.agent import cycle

    original = cycle.select

    def forced(context, registry, cfg, stats, *, allow_explore=True):
        result = original(context, registry, cfg, stats, allow_explore=allow_explore)
        if "formulation" not in registry:
            return result
        return Selection("formulation", result.scores, "formulation", "formulation", False, False)

    monkeypatch.setattr(cycle, "select", forced)
    try:
        wh.execute(f"DELETE FROM {physical} WHERE customer_id = :c", {"c": plain_customer})
        wh.execute(
            f"DELETE FROM {ops}.HOLDOUT_ASSIGNMENTS WHERE subscriber_key IN (:a, :b, :c)",
            {"a": keys[0], "b": keys[1], "c": keys[2]},
        )
        wh.merge_frame(
            f"{ops}.RISK_SCORES",
            pd.DataFrame([
                {
                    "run_id": RUN, "subscriber_key": key, "as_of": AS_OF,
                    "churn_prob": 0.8, "tier": "high", "top_factors": "[]",
                    "archetype": "price_sensitive", "archetype_conf": 0.95,
                    "archetype_probs": "{}", "archetype_low_conf": False,
                    "model_version": pipeline["model_version"], "scored_at": AS_OF,
                }
                for key in keys
            ]),
            keys=["run_id", "subscriber_key"],
        )
        cfg = load_pinned(wh, settings, RUN)
        cfg = cfg.model_copy(update={"holdout": HoldoutConfig(share=0, salt_version="submit")})
        decisions = run_agent(wh, settings, RUN, cfg)
        dispatch(wh, settings, decisions)
        log = wh.query(
            f"SELECT subscriber_key, params FROM {ops}.INTERVENTION_LOG WHERE run_id = :r",
            {"r": RUN},
        )
        by_key = {row.subscriber_key: json.loads(row.params) for row in log.itertuples(index=False)}
        assert by_key[quiz_key]["data_source"] == "quiz"
        assert by_key[quiz_key]["recommended_sku"] == expected_sku
        assert expected_sku in by_key[quiz_key]["goals"]
        assert by_key[plain_key]["data_source"] == "purchase_history"
        assert by_key[plain_key]["goals"] == [expected_goal]
        assert "goal_email_engagement" in by_key[plain_key]
        assert by_key["submit-neither"]["data_source"] == "purchase_history"
        assert by_key["submit-neither"]["goals"] == []
    finally:
        wh.execute(f"DELETE FROM {physical} WHERE customer_id = :c", {"c": plain_customer})
        if not backup.empty:
            wh.insert_frame(physical, backup)
        ids = wh.query(
            f"SELECT decision_id FROM {ops}.INTERVENTION_LOG WHERE run_id = :r", {"r": RUN}
        )
        for table in ("INTERVENTION_LOG", "APPROVALS", "RISK_SCORES", "COVERAGE_GAPS", "RUN_METRICS"):
            wh.execute(f"DELETE FROM {ops}.{table} WHERE run_id = :r", {"r": RUN})
        for decision_id in ids["decision_id"]:
            wh.execute(f"DELETE FROM {ops}.OUTBOX WHERE outbox_id = :d", {"d": decision_id})
        wh.execute(
            f"DELETE FROM {ops}.HOLDOUT_ASSIGNMENTS WHERE subscriber_key IN (:a, :b, :c)",
            {"a": keys[0], "b": keys[1], "c": keys[2]},
        )
        if not prior.empty:
            wh.insert_frame(f"{ops}.HOLDOUT_ASSIGNMENTS", prior)
