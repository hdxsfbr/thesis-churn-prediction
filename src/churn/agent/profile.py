"""Point-in-time quiz and purchase signals for formulation. Contract views only."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.settings import Settings
from churn.warehouse.base import Warehouse


def formulation_signals(
    wh: Warehouse, settings: Settings, as_of: datetime, keys: set[str]
) -> dict[str, dict]:
    """Latest quiz before `as_of`, else the most reordered blend's goal category.

    The quiz has no goal column. `recommended_sku` is the quiz's goal signal, and it uses the
    same vocabulary as `products.goal_category`. A subscriber in `keys` with neither is still returned.
    """
    blank = {"has_quiz": False, "quiz_goals": [], "recommended_sku": None, "top_blend_goal": None}
    out = {key: dict(blank) for key in keys}
    if not keys:
        return out
    ctes = contract_ctes(contract_for(settings), settings.key_pepper, ["quiz_responses", "orders", "products"])
    quiz = wh.query(
        f"""
        WITH {ctes}
        SELECT subscriber_key, recommended_sku FROM (
            SELECT subscriber_key, recommended_sku,
                   ROW_NUMBER() OVER (PARTITION BY subscriber_key ORDER BY answered_at DESC) AS rn
            FROM quiz_responses
            WHERE answered_at < :as_of
        ) latest WHERE rn = 1
        """,
        {"as_of": as_of},
    )
    orders = wh.query(
        f"""
        WITH {ctes}
        SELECT subscriber_key, goal_category FROM (
            SELECT subscriber_key, goal_category,
                   ROW_NUMBER() OVER (PARTITION BY subscriber_key ORDER BY n DESC, blend_sku) AS rn
            FROM (
                SELECT o.subscriber_key, o.blend_sku,
                       ANY_VALUE(p.goal_category) AS goal_category, COUNT(*) AS n
                FROM orders o
                LEFT JOIN products p ON p.blend_sku = o.blend_sku
                WHERE o.ordered_at < :as_of
                GROUP BY o.subscriber_key, o.blend_sku
            ) counted
        ) ranked WHERE rn = 1
        """,
        {"as_of": as_of},
    )
    if not quiz.empty:
        quiz = quiz[quiz["subscriber_key"].isin(keys)]
        for row in quiz.itertuples(index=False):
            sku = row.recommended_sku if pd.notna(row.recommended_sku) else None
            out[row.subscriber_key] = {
                "has_quiz": True,
                "quiz_goals": [str(sku)] if sku else [],
                "recommended_sku": None if sku is None else str(sku),
                "top_blend_goal": None,
            }
    if not orders.empty:
        orders = orders[orders["subscriber_key"].isin(keys)]
        for row in orders.itertuples(index=False):
            goal = row.goal_category if pd.notna(row.goal_category) else None
            out[row.subscriber_key]["top_blend_goal"] = None if goal is None else str(goal)
    return out
