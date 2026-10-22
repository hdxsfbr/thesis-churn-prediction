"""T8 (R3.6): evaluation metrics on hand-built inputs, and honest PASS / MISS target reporting."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from churn.model.evaluate import (
    BOUNDARY_MIN_RECALL,
    MIN_VALIDATION_CHURNERS,
    TARGETS,
    calibration_table,
    ceilings,
    check_targets,
    choose_boundary,
    pre_cancellation_breakdown,
    pre_cancellation_recall,
    split_snapshots,
    top_decile_lift,
    top_decile_precision,
    top_share_precision,
)


def test_top_share_precision_known_value():
    # 100 rows: the top 2% is the 2 highest scores. One of them is a churner.
    probs = np.linspace(0.01, 0.99, 100)
    y = np.zeros(100, dtype=int)
    y[-1] = 1  # highest score: churner
    y[-2] = 0  # second highest: not
    y[10] = 1  # a churner far below the cut does not count
    assert top_share_precision(y, probs) == pytest.approx(0.5)
    # 120 rows -> ceil(2.4) = 3 rows in the top 2%
    probs = np.arange(120) / 120
    y = np.zeros(120, dtype=int)
    y[[119, 118]] = 1
    assert top_share_precision(y, probs) == pytest.approx(2 / 3)
    # ties are broken by subscriber key, so the result is deterministic
    p = np.ones(100)
    y = np.zeros(100, dtype=int)
    keys = np.array([f"k{i:03d}" for i in range(100)])
    y[0] = 1  # k000 sorts first among equal scores
    assert top_share_precision(y, p, keys=keys) == pytest.approx(0.5)
    assert np.isnan(top_share_precision([], []))


def test_top_decile_lift_known_value():
    # 20 rows, 4 churners (b = 0.2). Top decile = 2 rows, both churners: precision 1.0, lift 5.0
    probs = np.linspace(0.05, 0.95, 20)
    y = np.zeros(20, dtype=int)
    y[[19, 18, 3, 2]] = 1
    assert top_decile_precision(y, probs) == pytest.approx(1.0)
    assert top_decile_lift(y, probs) == pytest.approx(5.0)
    # one of the two top rows is a churner: precision 0.5, lift 2.5
    y = np.zeros(20, dtype=int)
    y[[19, 3, 2, 1]] = 1
    assert top_decile_lift(y, probs) == pytest.approx(2.5)
    # no churners: lift undefined
    assert np.isnan(top_decile_lift(np.zeros(20, dtype=int), probs))


def _validation_frame(churner_scores, extra_non_churners=(0.9, 0.1)):
    n = len(churner_scores)
    frame = pd.DataFrame(
        {
            "subscriber_key": [f"c{i}" for i in range(n)] + [f"n{i}" for i in range(len(extra_non_churners))],
            "snapshot_date": pd.Timestamp("2025-01-31"),
            "label": [1] * n + [0] * len(extra_non_churners),
            "churn_prob": list(churner_scores) + list(extra_non_churners),
        }
    )
    return frame


def test_boundary_is_highest_threshold_with_validation_recall_at_least_065():
    assert BOUNDARY_MIN_RECALL == 0.65
    # 20 churners with scores 0.01 .. 0.20; ceil(0.65 * 20) = 13th highest = 0.08
    scores = [round(0.01 * i, 2) for i in range(1, 21)]
    b = choose_boundary(_validation_frame(scores))
    assert b.threshold == pytest.approx(0.08)
    assert b.n_churners == 20
    assert b.validation_recall == pytest.approx(13 / 20)
    assert b.validation_recall >= 0.65
    # any higher threshold has lower validation recall (the 12th highest is 0.09)
    above = sum(s >= 0.09 for s in scores) / 20
    assert above < 0.65
    # a churner counts once, through its latest snapshot
    f = _validation_frame(scores)
    extra = f[f["label"] == 1].assign(snapshot_date=pd.Timestamp("2024-12-31"), churn_prob=0.99)
    assert choose_boundary(pd.concat([f, extra])).threshold == pytest.approx(0.08)


def test_boundary_ignores_holdout_data():
    scores = [round(0.01 * i, 2) for i in range(1, 31)]
    validation = _validation_frame(scores)  # dated 2025-01-31
    assert list(inspect.signature(choose_boundary).parameters) == ["validation"]
    before = choose_boundary(validation)

    def with_holdout(holdout_scores):
        holdout = _validation_frame(holdout_scores).assign(snapshot_date=pd.Timestamp("2025-06-30"))
        holdout["subscriber_key"] = "h" + holdout["subscriber_key"]
        both = pd.concat([validation, holdout], ignore_index=True)
        # the split the way evaluate() does it: by snapshot date, the holdout dates are not validation dates
        return both[both["snapshot_date"].isin(set(validation["snapshot_date"]))]

    # changing only the holdout rows leaves the boundary where it was
    for holdout_scores in ([0.99] * 40, [0.001] * 40, []):
        assert choose_boundary(with_holdout(holdout_scores)).threshold == before.threshold
    # while changing validation scores does move it
    shifted = validation.assign(churn_prob=validation["churn_prob"] / 2)
    assert choose_boundary(shifted).threshold == pytest.approx(before.threshold / 2)


def test_too_few_validation_churners_is_recall_miss_with_reason():
    assert MIN_VALIDATION_CHURNERS == 20
    few = choose_boundary(_validation_frame([0.5] * 19))
    assert few.threshold is None and "fewer than 20" in few.reason
    none = choose_boundary(_validation_frame([]))
    assert none.threshold is None and none.reason
    results = check_targets(
        top_share_precision=0.9, top_decile_lift=6.0, recall=float("nan"), base_rate=0.03, n_positives=40,
        n_rows=5000, boundary=few,
    )
    recall = {r.name: r for r in results}["pre_cancellation_recall"]
    assert recall.status == "MISS" and "no at-risk boundary" in recall.reason and "fewer than 20" in recall.reason


def test_ceilings_from_measured_base_rate():
    c = ceilings(0.031)
    assert c["top_decile_precision"] == pytest.approx(0.31)
    assert c["top_decile_lift"] == pytest.approx(10.0)
    assert c["top_share_precision"] == pytest.approx(1.0)
    c = ceilings(0.005)
    assert c["top_decile_precision"] == pytest.approx(0.05)
    assert c["top_share_precision"] == pytest.approx(0.25)
    assert c["top_decile_lift"] == pytest.approx(10.0)
    c = ceilings(0.5)
    assert c["top_decile_precision"] == 1.0 and c["top_decile_lift"] == pytest.approx(2.0)
    # a target above its ceiling is a MISS that says so
    results = {r.name: r for r in check_targets(0.2, 3.0, 0.7, 0.005, 50, 10000)}
    assert results["top_share_precision"].status == "MISS"
    assert "unreachable at base rate" in results["top_share_precision"].reason


def test_splits_are_disjoint_ordered_and_purged():
    horizon = pd.Timedelta(days=30)
    for dates, n in (
        (list(pd.date_range("2024-01-31", periods=14, freq="ME")), 3),
        (list(pd.date_range("2024-01-01", periods=30, freq="10D")), 6),  # dense: several windows cross the split
    ):
        s = split_snapshots(dates, holdout_months=n, validation_months=n, horizon_days=30)
        assert s.holdout == dates[-n:]
        assert s.validation and s.train
        assert s.validation[-1] < s.holdout[0] and s.train[-1] < s.validation[0]
        assert not set(s.train) & set(s.validation) and not set(s.validation) & set(s.holdout)
        # no label window reaches the first snapshot of the next split
        assert s.train[-1] + horizon < s.validation[0]
        assert s.validation[-1] + horizon < s.holdout[0]
        # everything is either kept or reported as purged
        assert set(s.train) | set(s.validation) | set(s.holdout) | set(s.purged) == set(dates)
        assert not set(s.purged) & (set(s.train) | set(s.validation) | set(s.holdout))
        assert s.purged  # month-end gaps of 30 days or less always purge at least one
    # every-10-day snapshots: the 3 snapshots before each split start (windows of 30 days) are purged
    assert len(s.purged) == 6
    # 28-30 day gaps purge the snapshot before the split; a 31-day gap does not
    monthly = split_snapshots(list(pd.date_range("2024-01-31", periods=14, freq="ME")), 3, 3, 30)
    assert pd.Timestamp("2024-08-31") in monthly.purged  # next split starts 2024-09-30, 30 days later
    assert pd.Timestamp("2024-11-30") in monthly.validation  # 2024-12-31 is 31 days later


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
    assert TARGETS == {"top_share_precision": 0.50, "top_decile_lift": 4.0, "pre_cancellation_recall": 0.60}
    boundary = choose_boundary(_validation_frame([round(0.01 * i, 2) for i in range(1, 31)]))
    results = check_targets(
        top_share_precision=0.40, top_decile_lift=4.5, recall=0.72, base_rate=0.03, n_positives=40, n_rows=1500,
        top_share_hits=12, top_share_rows=30, boundary=boundary,
    )
    by_name = {r.name: r for r in results}
    miss = by_name["top_share_precision"]
    assert miss.status == "MISS" and miss.target == 0.50
    assert "0.40" in miss.reason and "0.50" in miss.reason and "12 of 30" in miss.reason
    assert by_name["top_decile_lift"].status == "PASS" and by_name["top_decile_lift"].reason is None
    assert by_name["pre_cancellation_recall"].status == "PASS"

    # a recall miss names the boundary and splits the misses
    breakdown = {"total": 10, "caught": 4, "below_boundary": 5, "no_score": 1, "threshold": 0.35}
    recall_miss = check_targets(0.8, 5.0, 0.4, 0.03, 40, 1500, breakdown, boundary=boundary)[2]
    assert recall_miss.status == "MISS"
    assert "0.35" in recall_miss.reason and "boundary" in recall_miss.reason
    assert "1 had no score" in recall_miss.reason and "5 had a last score below" in recall_miss.reason

    # exactly at a "greater than" target is MISS; exactly 4.0x lift passes (>=)
    assert check_targets(0.50, 4.0, 0.60, 0.1, 100, 1000, boundary=boundary)[0].status == "MISS"
    assert check_targets(0.50, 4.0, 0.60, 0.1, 100, 1000, boundary=boundary)[1].status == "PASS"
    # an undefined metric (no churners in the holdout) is a MISS that says so
    undefined = check_targets(float("nan"), float("nan"), float("nan"), 0.0, 0, 100, boundary=boundary)
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
