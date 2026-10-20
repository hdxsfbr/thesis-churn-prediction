"""Acceptance tests for T1: synthetic subscriptions scored into RISK_SCORES on DuckDB."""

from __future__ import annotations

from datetime import date

import pytest

from churn.orchestration import runner
from churn.orchestration.runner import RunFailed, run_cycle
from churn.warehouse.migrate import apply_migrations

AS_OF = date(2025, 6, 30)
EARLIER = date(2025, 5, 31)

ACTIVE_SQL = """
SELECT COUNT(DISTINCT customer_id) AS n FROM THESIS_SYNTH.subscriptions
WHERE started_at < CAST(:as_of AS TIMESTAMP)
  AND (cancelled_at IS NULL OR cancelled_at >= CAST(:as_of AS TIMESTAMP))
  AND loaded_at <= CAST(:as_of AS TIMESTAMP)
  AND (cancelled_at IS NOT NULL OR status IN ('ACTIVE', 'PAUSED', 'PAST_DUE'))
"""


def _active_in_source(wh, as_of) -> int:
    return int(wh.query(ACTIVE_SQL, {"as_of": as_of}).iloc[0]["n"])


def test_every_active_subscriber_scored(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    result = run_cycle(wh, settings, AS_OF, run_id="t-active-1")

    expected = _active_in_source(wh, AS_OF)
    assert expected > 100
    n_scores = wh.query(
        "SELECT COUNT(*) AS n FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r", {"r": "t-active-1"}
    ).iloc[0]["n"]
    assert n_scores == expected

    run = wh.query("SELECT * FROM CHURN_OPS.RUNS WHERE run_id = :r", {"r": "t-active-1"})
    assert len(run) == 1
    row = run.iloc[0]
    assert row["status"] == "SUCCEEDED"
    assert row["active_in_source"] == expected
    assert row["scored"] == expected
    assert row["model_version"] == pipeline["model_version"]
    assert result.active_in_source == result.scored == expected

    probs = wh.query("SELECT churn_prob FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r", {"r": "t-active-1"})
    assert probs["churn_prob"].between(0, 1).all()
    assert probs["churn_prob"].nunique() > 10


def test_score_history_query(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    run_cycle(wh, settings, EARLIER, run_id="t-hist-a")
    run_cycle(wh, settings, AS_OF, run_id="t-hist-b")

    key = wh.query(
        """
        SELECT a.subscriber_key FROM CHURN_OPS.RISK_SCORES a
        JOIN CHURN_OPS.RISK_SCORES b ON a.subscriber_key = b.subscriber_key
        WHERE a.run_id = 't-hist-a' AND b.run_id = 't-hist-b'
        ORDER BY a.subscriber_key LIMIT 1
        """
    ).iloc[0]["subscriber_key"]

    # Metabase-style score-history query.
    hist = wh.query(
        """
        SELECT r.as_of, s.churn_prob, s.model_version
        FROM CHURN_OPS.RISK_SCORES s
        JOIN CHURN_OPS.RUNS r ON r.run_id = s.run_id
        WHERE s.subscriber_key = :k AND r.run_id LIKE 't-hist-%'
        ORDER BY r.as_of
        """,
        {"k": key},
    )
    assert len(hist) == 2
    assert list(hist["as_of"].dt.date) == [EARLIER, AS_OF]
    assert hist["churn_prob"].between(0, 1).all()


def test_same_run_id_rerun_identical(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    q = "SELECT subscriber_key, churn_prob FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-rerun' ORDER BY subscriber_key"
    run_cycle(wh, settings, AS_OF, run_id="t-rerun")
    first = wh.query(q)
    run_cycle(wh, settings, AS_OF, run_id="t-rerun")
    second = wh.query(q)

    assert len(first) > 0
    assert len(first) == len(second)
    assert list(first["subscriber_key"]) == list(second["subscriber_key"])
    assert first["subscriber_key"].is_unique
    assert list(first["churn_prob"]) == list(second["churn_prob"])
    runs = wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.RUNS WHERE run_id = 't-rerun'")
    assert runs.iloc[0]["n"] == 1


def test_migrations_idempotent(fresh_wh, settings):
    first = apply_migrations(fresh_wh, settings)
    assert first, "first application applies migrations"
    snapshot = fresh_wh.query("SELECT version, checksum FROM CHURN_OPS.SCHEMA_MIGRATIONS ORDER BY version")

    second = apply_migrations(fresh_wh, settings)
    assert second == []
    again = fresh_wh.query("SELECT version, checksum FROM CHURN_OPS.SCHEMA_MIGRATIONS ORDER BY version")
    assert snapshot.equals(again)
    assert len(snapshot) == len(first)
    assert snapshot["checksum"].str.fullmatch(r"[0-9a-f]{64}").all()

    tables = fresh_wh.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema ILIKE 'CHURN_OPS'"
    )["table_name"].str.upper()
    assert {"RUNS", "RISK_SCORES", "MODEL_REGISTRY", "SCHEMA_MIGRATIONS"} <= set(tables)


def test_scored_count_mismatch_fails_run(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    real = runner.score_frame

    def drops_one(*args, **kwargs):
        return real(*args, **kwargs).iloc[:-1]

    monkeypatch.setattr(runner, "score_frame", drops_one)
    with pytest.raises(RunFailed, match="scored"):
        run_cycle(wh, settings, AS_OF, run_id="t-mismatch")

    run = wh.query("SELECT * FROM CHURN_OPS.RUNS WHERE run_id = 't-mismatch'").iloc[0]
    assert run["status"] == "FAILED"
    assert run["active_in_source"] == run["scored"] + 1
    assert "scored" in run["error_msg"]
    leftover = wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-mismatch'")
    assert leftover.iloc[0]["n"] == 0


def test_training_frame_has_no_label_leak(pipeline):
    """Status-derived features must not exist yet, and no common feature value may fix the label."""
    from churn.contract.loader import contract_for
    from churn.features.registry import FEATURE_COLUMNS
    from churn.model.dataset import build_dataset

    wh, settings = pipeline["wh"], pipeline["settings"]
    assert not {"is_paused", "is_past_due"} & set(FEATURE_COLUMNS)
    df = build_dataset(wh, settings, contract_for(settings), AS_OF)
    assert df["label"].nunique() == 2
    for col in FEATURE_COLUMNS:
        for value, grp in df.groupby(df[col].fillna(-1e9))["label"]:
            if len(grp) >= 0.05 * len(df):
                assert grp.nunique() > 1, f"{col}={value} perfectly separates the label"
