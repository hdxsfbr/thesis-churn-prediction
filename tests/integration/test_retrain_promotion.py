"""Retrain reads intervention history and the observed outcome, and the gate keeps a worse model out."""

import json
from datetime import date, datetime, timedelta

import pandas as pd

from churn.contract.loader import contract_for
from churn.features.builder import build_features
from churn.features.registry import FEATURE_COLUMNS, INTERVENTION_TYPE_CODE
from churn.feedback.retrain import retrain
from churn.model.dataset import build_dataset, churners, snapshot_dates
from churn.model.train import fit, train
from churn.synth.generator import generate
from churn.warehouse.duckdb import DuckDBWarehouse
from churn.warehouse.migrate import apply_migrations
from conftest import make_settings

AS_OF = date(2025, 6, 30)


class Inverted:
    """Ranks the opposite of a fitted model. Defined at module level so the rejected artefact can be pickled."""

    def __init__(self, inner):
        self.inner = inner

    def predict_proba(self, frame):
        probs = self.inner.predict_proba(frame)
        flipped = 1.0 - probs[:, 1]
        return pd.DataFrame({0: 1.0 - flipped, 1: flipped}).to_numpy()


def test_intervention_history_features_present(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    contract = contract_for(settings)
    before = build_features(wh, settings, contract, AS_OF)
    key = before.iloc[0]["subscriber_key"]
    prior = float(before.set_index("subscriber_key").loc[key, "n_interventions_90d"])
    decision_id = "t18-history"
    ops = settings.schemas.ops
    dispatched = datetime(2025, 6, 20)
    try:
        wh.insert_frame(
            f"{ops}.INTERVENTION_LOG",
            pd.DataFrame([
                {
                    "decision_id": decision_id,
                    "subscriber_key": key,
                    "intervention_type": "formulation",
                    "execution_status": "DISPATCHED",
                    "dispatched_at": dispatched,
                    "outcome": "PENDING",
                },
                {
                    "decision_id": decision_id + "-hold",
                    "subscriber_key": key,
                    "intervention_type": "HOLDOUT",
                    "execution_status": "HOLDOUT",
                    "dispatched_at": dispatched + timedelta(days=1),
                    "outcome": "PENDING",
                },
            ]),
        )
        after = build_features(wh, settings, contract, AS_OF).set_index("subscriber_key").loc[key]
        assert float(after["n_interventions_90d"]) == prior + 1
        assert after["days_since_last_intervention"] == 10
        assert after["last_intervention_type"] == INTERVENTION_TYPE_CODE["formulation"]
        assert set(INTERVENTION_TYPE_CODE) <= {"checkin_email", "formulation", "retention_offer", "escalation"}
        for name in ("n_interventions_90d", "last_intervention_type", "days_since_last_intervention"):
            assert name in FEATURE_COLUMNS
    finally:
        wh.execute(f"DELETE FROM {ops}.INTERVENTION_LOG WHERE decision_id = :d", {"d": decision_id})
        wh.execute(
            f"DELETE FROM {ops}.INTERVENTION_LOG WHERE decision_id = :d",
            {"d": decision_id + "-hold"},
        )


def _fitted(tmp_path):
    settings = make_settings(tmp_path)
    wh = DuckDBWarehouse(":memory:")
    apply_migrations(wh, settings)
    generate(wh, settings, scale="tiny")
    version = train(wh, settings)
    return wh, settings, version


def _row(wh, settings, version):
    ops = settings.schemas.ops
    return wh.query(
        f"SELECT status, reason, metrics_json FROM {ops}.MODEL_REGISTRY WHERE model_version = :v",
        {"v": version},
    ).iloc[0]


def test_retrain_consumes_intervention_outcomes_and_versions_model(tmp_path):
    wh, settings, current = _fitted(tmp_path)
    contract = contract_for(settings)
    snap = snapshot_dates(wh, settings, contract, settings.synth.end_date)[-1]
    positives = churners(wh, settings, contract, snap)
    assert positives
    key = min(positives)
    moment = datetime(snap.year, snap.month, snap.day) - timedelta(days=2)
    wh.insert_frame(
        f"{settings.schemas.ops}.INTERVENTION_LOG",
        pd.DataFrame([{
            "decision_id": "t18-observed",
            "subscriber_key": key,
            "intervention_type": "formulation",
            "execution_status": "DISPATCHED",
            "dispatched_at": moment,
            "outcome": "retained",
            "outcome_at": moment,
            "policy_scores": "{}",
        }]),
    )
    frame = build_dataset(wh, settings, contract, settings.synth.end_date)
    matched = frame[
        (frame["subscriber_key"] == key) & (pd.to_datetime(frame["snapshot_date"]) == pd.Timestamp(snap))
    ]
    assert int(matched["label"].iloc[0]) == 0  # retained overrides the later cancellation
    version, status = retrain(wh, settings)
    assert version != current
    assert version.startswith("retrain-")
    current_row = _row(wh, settings, current)
    candidate_row = _row(wh, settings, version)
    assert candidate_row["status"] == status
    if status == "promoted":
        assert current_row["status"] == "retired"
    else:
        assert current_row["status"] == "promoted"
    wh.close()


def test_worse_candidate_rejected_current_kept(tmp_path, monkeypatch):
    wh, settings, current = _fitted(tmp_path)

    real_fit = fit

    def worse_fit(df, cfg):
        model, metrics = real_fit(df, cfg)
        return Inverted(model), metrics

    monkeypatch.setattr("churn.feedback.retrain.fit", worse_fit)
    version, status = retrain(wh, settings)
    assert status == "rejected"
    assert _row(wh, settings, current)["status"] == "promoted"
    assert _row(wh, settings, version)["status"] == "rejected"
    wh.close()


def test_registry_records_metrics_and_reason(tmp_path):
    wh, settings, _current = _fitted(tmp_path)
    version, status = retrain(wh, settings)
    row = _row(wh, settings, version)
    metrics = json.loads(row["metrics_json"])
    assert row["status"] == status
    assert {"top_decile_precision", "pr_auc", "tolerance", "compared_to"} <= set(metrics)
    assert metrics["tolerance"] == settings.retrain.tolerance
    assert row["reason"].startswith(f"{status}:")
    wh.close()
