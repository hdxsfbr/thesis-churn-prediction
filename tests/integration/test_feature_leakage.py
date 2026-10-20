"""R2.3: events dated after the scoring date (or loaded after it) leave that date's features unchanged."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from churn.features.builder import build_features
from churn.warehouse.duckdb import DuckDBWarehouse
from fixtures import subscribers as fx


def test_post_as_of_events_do_not_change_features(settings, tmp_path):
    wh = DuckDBWarehouse(":memory:")
    try:
        fx.build_source().write(wh)
        contract = fx.write_contract(tmp_path / "c.yaml")
        before = fx.feature_frame_by_customer(wh, settings, contract)

        # Add one event of every kind to every active customer, all dated after AS_OF, plus events dated
        # before AS_OF that only arrive after it, and a cancellation recorded after AS_OF.
        extra = fx.Source()
        extra._n = 100_000  # keep ids distinct from the fixture's
        for cid in fx.ACTIVE_CUSTOMERS:
            extra.order(cid, -3, refunded_days_ago=-4)
            extra.email(cid, "open", -2)
            extra.email(cid, "send", -2)
            extra.email(cid, "unsubscribe", -1)
            extra.sub_event(cid, "skip", -5)
            extra.sub_event(cid, "blend_switch", -6)
            extra.ticket(cid, -2, True, -0.9)
            extra.quiz(cid, "post", -1)
            extra.order(cid, 1, loaded=fx.ago(-10))
            extra.email(cid, "open", 1, loaded=fx.ago(-10))
            extra.sub_event(cid, "skip", 1, loaded=fx.ago(-10))
            extra.ticket(cid, 1, True, -0.9, loaded=fx.ago(-10))
            extra.quiz(cid, "late", 1, loaded=fx.ago(-10))
        for table, rows in extra.rows.items():
            wh.insert_frame(f"{fx.SCHEMA}.{table}", pd.DataFrame(rows))
        wh.execute(
            f"UPDATE {fx.SCHEMA}.subscriptions SET cancelled_at = TIMESTAMP '2025-07-05', status = 'CANCELLED' "
            "WHERE customer_id = 'c_full'"
        )
        after = fx.feature_frame_by_customer(wh, settings, contract)
        later = build_features(wh, settings, contract, datetime(2025, 7, 30))
    finally:
        wh.close()

    cols = [c for c in before.columns if c != "subscriber_key"]
    pd.testing.assert_frame_equal(before[cols].sort_index(), after[cols].sort_index(), check_exact=True)
    # The extra rows are real: a later scoring date sees them, so a leak would have been noticed.
    assert later["orders_total"].sum() > after["orders_total"].sum()
    assert len(after) == len(before)
