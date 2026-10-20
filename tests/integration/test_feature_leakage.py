"""R2.3: events after as_of (or loaded after as_of) never change the features of a scoring date."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from churn.contract.loader import contract_for
from churn.features.builder import build_features
from churn.warehouse.duckdb import DuckDBWarehouse
from fixtures import subscribers as fx


def _features(settings, source):
    wh = DuckDBWarehouse(":memory:")
    fx.load(wh, source)
    try:
        return build_features(wh, settings, fx.fixture_contract(contract_for(settings)), fx.AS_OF)
    finally:
        wh.close()


def test_post_as_of_events_do_not_change_features(settings):
    before = _features(settings, fx.known_values())

    polluted = fx.known_values()
    future = fx.AS_OF + timedelta(days=3)
    late = fx.AS_OF + timedelta(days=5)
    for cid in ("alpha", "beta"):
        polluted.order(cid, -3)  # ordered after as_of
        polluted.order(cid, 5, loaded_at=late)  # ordered before as_of, but loaded after it
        for kind in ("send", "open", "click", "unsubscribe"):
            polluted.email(cid, kind, 0, at=future)
            polluted.email(cid, kind, 5, loaded_at=late)
        for kind in ("skip", "blend_switch", "pause"):
            polluted.sub_event(cid, kind, 0, at=future)
            polluted.sub_event(cid, kind, 5, loaded_at=late)
        polluted.ticket(cid, -2, True, -1.0)
        polluted.ticket(cid, 5, True, -1.0, loaded_at=late)
        polluted.quiz(cid, -1)
        polluted.quiz(cid, 5, loaded_at=late)
    # a subscriber who only exists after as_of is not scored at all
    polluted.subscriber("future-sub", -2)
    polluted.order("future-sub", -1)

    after = _features(settings, polluted)
    pd.testing.assert_frame_equal(before, after)
    assert len(after) == 2
