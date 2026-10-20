"""R2.3: rows that happen after as_of (or are loaded after it) never change a feature."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from churn.features.builder import build_features
from churn.warehouse.duckdb import DuckDBWarehouse
from fixtures import subscribers as fx


def test_post_as_of_events_do_not_change_features(settings):
    wh = DuckDBWarehouse(":memory:")
    contract = fx.load(wh)
    before = build_features(wh, settings, contract, fx.AS_OF)
    assert len(before) == len(fx.SUBS)  # cancelled / not-yet-started subscriptions are not active

    after_ts = fx.AS_OF + timedelta(days=2)
    late = after_ts + timedelta(days=1)
    for name in ("alpha", "no_data", "new"):
        sub = fx.SUBS[name]
        cust = f"c_{name}"
        # events that happen after as_of
        fx.add_row(wh, "orders", order_id=f"x_o_{name}", customer_id=cust, ordered_at=after_ts, amount_usd=99.0,
                   discount_usd=50.0, refund_usd=99.0, refunded_at=after_ts, loaded_at=after_ts)
        for kind in ("skip", "blend_switch", "pause"):
            fx.add_row(wh, "subscription_events", subscription_id=sub, event_id=f"x_s_{kind}_{name}",
                       event_type=kind, event_ts=after_ts, loaded_at=after_ts)
        for kind in ("send", "open", "click", "unsubscribe"):
            fx.add_row(wh, "email_events", customer_id=cust, event_id=f"x_m_{kind}_{name}", event_type=kind,
                       event_ts=after_ts, loaded_at=after_ts)
        fx.add_row(wh, "support_tickets", customer_id=cust, ticket_id=f"x_t_{name}", created_at=after_ts,
                   category="billing", escalated=True, sentiment_score=-1.0, loaded_at=after_ts)
        fx.add_row(wh, "quiz_responses", answer_id=f"x_a_{name}", customer_id=cust, quiz_session_id=f"x_q_{name}",
                   question_key="q0", answer_value="y", answered_at=after_ts, recommended_sku="s", loaded_at=after_ts)
        # events dated before as_of but loaded after it (late arrivals are not visible yet)
        fx.add_row(wh, "orders", order_id=f"l_o_{name}", customer_id=cust, ordered_at=fx.ago(1), amount_usd=10.0,
                   discount_usd=0.0, refund_usd=None, refunded_at=None, loaded_at=late)
        fx.add_row(wh, "subscription_events", subscription_id=sub, event_id=f"l_s_{name}", event_type="skip",
                   event_ts=fx.ago(1), loaded_at=late)
        fx.add_row(wh, "email_events", customer_id=cust, event_id=f"l_m_{name}", event_type="click",
                   event_ts=fx.ago(1), loaded_at=late)
        fx.add_row(wh, "support_tickets", customer_id=cust, ticket_id=f"l_t_{name}", created_at=fx.ago(1),
                   category="billing", escalated=True, sentiment_score=-1.0, loaded_at=late)
        fx.add_row(wh, "quiz_responses", answer_id=f"l_a_{name}", customer_id=cust, quiz_session_id=f"l_q_{name}",
                   question_key="q0", answer_value="y", answered_at=fx.ago(1), recommended_sku="s", loaded_at=late)

    after = build_features(wh, settings, contract, fx.AS_OF)
    pd.testing.assert_frame_equal(before, after)

    # sanity: moving as_of past the new rows does change something
    moved = build_features(wh, settings, contract, fx.AS_OF + timedelta(days=5))
    alpha_before = before.set_index("subscription_id").loc[fx.SUBS["alpha"]]
    alpha_moved = moved.set_index("subscription_id").loc[fx.SUBS["alpha"]]
    assert alpha_moved["orders_total"] > alpha_before["orders_total"]
    wh.close()
