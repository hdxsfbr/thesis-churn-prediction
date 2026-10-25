"""A full cycle leaves no synthetic PII in logs, features, scores, decisions, or the model."""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

from churn.contract.loader import contract_for
from churn.features.builder import build_features
from churn.llm.client import LLMConfig, override_llm
from churn.llm.fake import FakeLLM
from churn.model.dataset import build_dataset
from churn.obs.logging import configure_logging, redact_pii
from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w:-])(?:\+\d[\d\s().-]{7,}\d|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?")
_STREET = re.compile(
    r"\b\d{1,6}\s+[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,3}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b",
    re.IGNORECASE,
)


def _pii_values(wh) -> tuple[set[str], set[str]]:
    frame = wh.query(
        "SELECT first_name, email, phone, address FROM THESIS_SYNTH.customers_pii"
    )
    exact: set[str] = set()
    names: set[str] = set()
    for column in ("first_name", "email", "phone", "address"):
        for value in frame[column].dropna().astype(str):
            text = value.strip()
            if text and text.lower() != "none":
                exact.add(text)
                if column == "first_name":
                    names.add(text)
    return exact, names


def _strings(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    out = [str(column) for column in frame.columns]
    for column in frame.columns:
        if frame[column].dtype == object or str(frame[column].dtype).startswith("string"):
            out.extend(str(value) for value in frame[column].dropna().tolist())
    return out


def _problems(label: str, text: str, exact: set[str], names: set[str], *, phones: bool) -> list[str]:
    found = []
    for value in exact:
        if value and value in text:
            found.append(f"{label}: exact {value!r}")
    email = _EMAIL.search(text)
    if email:
        found.append(f"{label}: email {email.group(0)!r}")
    if phones:
        for match in _PHONE.finditer(text):
            if not _ISO_DATE.fullmatch(match.group(0)):
                found.append(f"{label}: phone {match.group(0)!r}")
                break
    street = _STREET.search(text)
    if street:
        found.append(f"{label}: address {street.group(0)!r}")
    for name in names:
        if re.search(rf"\b{re.escape(name)}\b", text):
            found.append(f"{label}: name {name!r}")
            break
    return found


def test_log_redaction_processor_masks_email_and_phone():
    event = redact_pii(
        None,
        "info",
        {
            "event": "note",
            "detail": "reach a@b.co or +1 415 555 0199",
            "nested": ["sam@example.com"],
            "ts": "2025-06-30T00:00:00",
        },
    )
    assert event["detail"] == "reach [redacted-email] or [redacted-phone]"
    assert event["nested"] == ["[redacted-email]"]
    assert "555" not in event["detail"]
    assert event["ts"] == "2025-06-30T00:00:00"
    assert "redacted" not in json.dumps({"ts": event["ts"]})


def test_no_pii_in_logs_tables_or_model_artefacts(pipeline):
    wh, settings = pipeline["wh"], pipeline["settings"]
    exact, names = _pii_values(wh)
    assert exact and names
    run_id = "t25-pii"
    buf = io.StringIO()
    configure_logging(stream=buf)
    fake = FakeLLM()
    with override_llm(fake, LLMConfig(max_calls_per_run=30, retries=1, concurrency=4)):
        run_cycle(wh, settings, AS_OF, run_id=run_id)
    contract = contract_for(settings)
    features = build_features(wh, settings, contract, AS_OF)
    training = build_dataset(wh, settings, contract, settings.synth.end_date)
    ops = settings.schemas.ops
    blobs = {
        "logs": buf.getvalue(),
        "prompts": "\n".join(fake.requests),
        "features": "\n".join(_strings(features)),
        "training": "\n".join(_strings(training)),
    }
    # The warehouse is session-scoped. Other tests store an approver address in decided_by.
    # This scan covers the cycle just run.
    for table in ("RISK_SCORES", "INTERVENTION_LOG", "APPROVALS"):
        blobs[table] = "\n".join(
            _strings(wh.query(f"SELECT * FROM {ops}.{table} WHERE run_id = :r", {"r": run_id}))
        )
    blobs["OUTBOX"] = "\n".join(
        _strings(wh.query(
            f"SELECT o.* FROM {ops}.OUTBOX o WHERE o.outbox_id IN "
            f"(SELECT decision_id FROM {ops}.INTERVENTION_LOG WHERE run_id = :r)",
            {"r": run_id},
        ))
    )
    version = wh.query(
        f"SELECT artefact_uri FROM {ops}.MODEL_REGISTRY WHERE status = 'promoted'"
    ).iloc[0]["artefact_uri"]
    blobs["model"] = Path(version).read_bytes().decode("utf-8", errors="ignore")
    found = []
    for label, text in blobs.items():
        found.extend(_problems(label, text, exact, names, phones=label != "model"))
    assert found == [], found
