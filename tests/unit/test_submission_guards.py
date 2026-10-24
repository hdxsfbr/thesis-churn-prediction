"""Holdout stays out of a retrain fit, a clipped offer names its percent, and a blank Snowflake pepper refuses to start."""

from datetime import datetime

import pandas as pd
import pytest

from churn.agent.context import AgentContext
from churn.feedback.retrain import fit_frame
from churn.interventions.retention_offer import RetentionOffer
from churn.model.evaluate import split_snapshots
from churn.settings import Settings


def test_fit_frame_leaves_every_holdout_row_out():
    dates = list(pd.date_range("2024-01-01", periods=12, freq="MS"))
    df = pd.DataFrame({"snapshot_date": dates, "subscriber_key": "s", "label": 0})
    settings = Settings.load()
    out = fit_frame(df, settings)
    splits = split_snapshots(dates, horizon_days=settings.label.horizon_days)
    holdout = {pd.Timestamp(d) for d in splits.holdout}
    assert holdout
    assert holdout.isdisjoint(set(pd.to_datetime(out["snapshot_date"])))
    assert set(pd.to_datetime(out["snapshot_date"])) <= {
        pd.Timestamp(d) for d in [*splits.train, *splits.validation]
    }


def test_clipped_offer_code_matches_the_percent():
    context = AgentContext(
        run_id="r", subscriber_key="s", risk=0.8, tier="high", as_of=datetime(2025, 6, 30),
        max_discount_pct=10, offer_catalogue=("PCT_25_NEXT_2",),
    )
    action = RetentionOffer().action(context)
    assert action.discount_pct == 10
    assert action.offer_code == "PCT_10_CAPPED"
    assert "25" not in action.offer_code
    body = RetentionOffer().template(context).body
    assert "10% off" in body
    assert "PCT_10_CAPPED" in body
    assert RetentionOffer().cost_usd(action) == 25.0


def test_blank_pepper_refused_only_for_snowflake(monkeypatch):
    monkeypatch.setenv("CHURN_BACKEND", "snowflake")
    monkeypatch.setenv("CHURN_KEY_PEPPER", "  ")
    with pytest.raises(ValueError, match="CHURN_KEY_PEPPER"):
        Settings.load()
    monkeypatch.setenv("CHURN_BACKEND", "duckdb")
    loaded = Settings.load()
    assert loaded.key_pepper.strip() == ""
    monkeypatch.delenv("CHURN_KEY_PEPPER")
    monkeypatch.delenv("CHURN_BACKEND")
    assert Settings.load().key_pepper == "dev-pepper-not-secret"
