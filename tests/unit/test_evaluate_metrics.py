"""T8 (R3.6): evaluation metrics on hand-built inputs, and honest PASS / MISS target reporting."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churn.model.evaluate import (
    PRECISION_TARGET,
    RECALL_TARGET,
    calibration_table,
    check_targets,
    pre_cancellation_breakdown,
    pre_cancellation_recall,
    top_decile_precision,
)


def test_top_decile_precision_known_value():
    # 20 rows: the top decile is the 2 highest probabilities. One of them is a churner.
    probs = np.linspace(0.05, 0.95, 20)
    y = np.zeros(20, dtype=int)
    y[-1] = 1  # highest score: churner
    y[-2] = 0  # second highest: not
    y[3] = 1  # a churner far below the cut does not count
    assert top_decile_precision(y, probs) == pytest.approx(0.5)

    # 25 rows -> ceil(2.5) = 3 rows in the top decile
    probs = np.arange(25) / 25
    y = np.zeros(25, dtype=int)
    y[[24, 23, 22]] = 1
    assert top_decile_precision(y, probs) == pytest.approx(1.0)


def test_pre_cancellation_recall_uses_last_score_before_cancel():
    scores = pd.DataFrame(
        {
            "subscriber_key": ["a", "a", "a", "b", "b", "c"],
            "as_of": pd.to_datetime(["2025-01-31", "2025-02-28", "2025-03-31", "2025-01-31", "2025-02-28", "2025-03-31"]),
            "churn_prob": [0.9, 0.1, 0.9, 0.1, 0.8, 0.9],
        }
    )
    cancels = pd.DataFrame(
        {
            "subscriber_key": ["a", "b", "c", "d"],
            # a: last score before cancel is 02-28 (0.1, below) -> missed; the 03-31 score is after cancel
            "cancelled_at": pd.to_datetime(["2025-03-10", "2025-03-05", "2025-03-01", "2025-03-05"]),
        }
    )
    # b: last score before cancel is 02-28 (0.8) -> caught
    # c: only score is after the cancel -> no score before cancelling, missed
    # d: never scored -> missed
    assert pre_cancellation_recall(scores, cancels, threshold=0.2) == pytest.approx(1 / 4)

    # raising an earlier score must not matter: only the *last* one counts
    scores.loc[0, "churn_prob"] = 0.99
    assert pre_cancellation_recall(scores, cancels, threshold=0.2) == pytest.approx(1 / 4)
    # no churners: undefined, not zero
    assert np.isnan(pre_cancellation_recall(scores, cancels.iloc[0:0], threshold=0.2))


def test_missed_target_reported_as_miss_with_reason():
    results = check_targets(precision=0.55, recall=0.72, base_rate=0.03, n_positives=40, n_rows=1500)
    by_name = {r.name: r for r in results}
    miss = by_name["top_decile_precision"]
    assert miss.status == "MISS"
    assert miss.target == PRECISION_TARGET
    assert miss.reason and "0.55" in miss.reason and "0.70" in miss.reason
    ok = by_name["pre_cancellation_recall"]
    assert ok.status == "PASS" and ok.reason is None
    assert ok.target == RECALL_TARGET

    # a recall miss names the boundary and splits the misses
    breakdown = {"total": 10, "caught": 4, "below_boundary": 5, "no_score": 1, "threshold": 0.35}
    recall_miss = check_targets(0.8, 0.4, 0.03, 40, 1500, breakdown)[1]
    assert recall_miss.status == "MISS"
    assert "0.35" in recall_miss.reason and "boundary" in recall_miss.reason
    assert "1 had no score" in recall_miss.reason and "5 had a last score below" in recall_miss.reason

    # exactly at the target is not "above": MISS
    assert check_targets(0.70, 0.60, 0.1, 100, 1000)[0].status == "MISS"
    # an undefined metric (no churners in the holdout) is a MISS that says so
    undefined = check_targets(float("nan"), float("nan"), 0.0, 0, 100)
    assert all(r.status == "MISS" and "no churners" in r.reason for r in undefined)


def test_pre_cancellation_breakdown_splits_misses():
    scores = pd.DataFrame(
        {
            "subscriber_key": ["a", "b"],
            "as_of": pd.to_datetime(["2025-02-28", "2025-02-28"]),
            "churn_prob": [0.9, 0.1],
        }
    )
    cancels = pd.DataFrame(
        {"subscriber_key": ["a", "b", "c"], "cancelled_at": pd.to_datetime(["2025-03-05"] * 3)}
    )
    b = pre_cancellation_breakdown(scores, cancels, 0.5)
    assert (b["total"], b["caught"], b["below_boundary"], b["no_score"], b["threshold"]) == (3, 1, 1, 1, 0.5)


def test_calibration_table_bins_cover_all_rows():
    probs = np.array([0.05, 0.15, 0.25, 0.95])
    y = np.array([0, 0, 1, 1])
    table = calibration_table(y, probs, bins=10)
    assert table["n"].sum() == 4
    assert {"bin_low", "bin_high", "mean_predicted", "observed_rate", "n"} <= set(table.columns)
    assert table.loc[table["n"] > 0, "observed_rate"].between(0, 1).all()
