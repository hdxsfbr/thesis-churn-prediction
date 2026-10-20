"""Model registry: joblib artefacts on disk, one MODEL_REGISTRY row each; one model is 'promoted'."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import joblib
import pandas as pd

from churn.settings import Settings
from churn.warehouse.base import Warehouse


class ModelError(RuntimeError):
    """No usable model. Run `churn train`."""


def register(
    wh: Warehouse,
    settings: Settings,
    bundle: dict[str, Any],
    version: str,
    train_start: datetime,
    train_end: datetime,
    metrics: dict[str, Any],
) -> str:
    """Write the artefact, retire the previous promoted model and promote this one."""
    models_dir = settings.models_dir
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"{version}.joblib"
    joblib.dump(bundle, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    ops = settings.schemas.ops
    row = pd.DataFrame(
        [
            {
                "model_version": version,
                "artefact_uri": str(path),
                "sha256": sha,
                "train_start": train_start,
                "train_end": train_end,
                "metrics_json": json.dumps(metrics, sort_keys=True),
                "status": "promoted",
                "reason": "first model" if _promoted(wh, ops).empty else "latest training run",
                "created_at": datetime.now(),
            }
        ]
    )
    with wh.transaction():
        wh.execute(
            f"UPDATE {ops}.MODEL_REGISTRY SET status = 'retired' "
            "WHERE status = 'promoted' AND model_version <> :v",
            {"v": version},
        )
        wh.merge_frame(f"{ops}.MODEL_REGISTRY", row, keys=["model_version"])
    return version


def _promoted(wh: Warehouse, ops: str) -> pd.DataFrame:
    return wh.query(
        f"SELECT model_version, artefact_uri, sha256 FROM {ops}.MODEL_REGISTRY "
        "WHERE status = 'promoted' ORDER BY created_at DESC"
    )


def load_promoted(wh: Warehouse, settings: Settings) -> tuple[str, dict[str, Any]]:
    rows = _promoted(wh, settings.schemas.ops)
    if rows.empty:
        raise ModelError("no promoted model in MODEL_REGISTRY; run `churn train` first")
    row = rows.iloc[0]
    with open(row["artefact_uri"], "rb") as fh:
        blob = fh.read()
    if hashlib.sha256(blob).hexdigest() != row["sha256"]:
        raise ModelError(f"artefact {row['artefact_uri']} does not match its recorded sha256")
    return row["model_version"], joblib.load(row["artefact_uri"])
