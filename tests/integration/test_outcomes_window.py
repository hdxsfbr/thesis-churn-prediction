"""Outcome rows stay pending inside the window and close to retained, churned or unknown after it."""

from datetime import date, datetime, timedelta

import pandas as pd

from churn import runtime_config
from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes
from churn.feedback.outcomes import advance, close_outcomes
from churn.synth.responder import respond
from churn.warehouse.migrate import apply_migrations

DISPATCHED = datetime(2025, 1, 1)
AFTER = datetime(2025, 2, 15)


def _ready(wh, settings):
    apply_migrations(wh, settings)
    runtime_config.seed(wh, settings)


def _customer(wh, settings, customer_id: str, **extra) -> str:
    row = {
        "customer_id": customer_id,
        "subscription_id": f"sub-{customer_id}",
        "status": "ACTIVE",
        "started_at": datetime(2024, 1, 1),
        "cancelled_at": None,
        "cancel_type": None,
        "cancel_reason_code": None,
        "price_usd": 39.0,
        "interval_days": 30,
        "loaded_at": datetime(2024, 1, 1),
    }
    row.update(extra)
    wh.insert_frame(f"{settings.schemas.synth}.subscriptions", pd.DataFrame([row]))
    ctes = contract_ctes(
        contract_for(settings), settings.key_pepper, ["subscriptions"], include_raw_id=True
    )
    found = wh.query(
        f"WITH {ctes} SELECT subscriber_key FROM subscriptions WHERE customer_id = :c",
        {"c": customer_id},
    )
    return found.iloc[0]["subscriber_key"]


def _log(wh, settings, decision_id: str, key: str, when: datetime, *, kind: str, status: str) -> None:
    wh.insert_frame(
        f"{settings.schemas.ops}.INTERVENTION_LOG",
        pd.DataFrame(
            [
                {
                    "decision_id": decision_id,
                    "subscriber_key": key,
                    "run_id": "t16-outcomes",
                    "intervention_type": kind,
                    "execution_status": status,
                    "dispatched_at": when,
                    "response": "PENDING",
                    "outcome": "PENDING",
                }
            ]
        ),
    )


def _email(wh, settings, customer_id: str, event_id: str, event_type: str, when: datetime) -> None:
    moment = pd.Timestamp(when)
    wh.insert_frame(
        f"{settings.schemas.synth}.email_events",
        pd.DataFrame(
            [
                {
                    "customer_id": customer_id,
                    "event_id": event_id,
                    "event_type": event_type,
                    "campaign_id": "t16",
                    "flow_id": "t16",
                    "event_ts": moment.tz_localize("UTC").tz_convert("America/New_York"),
                    "loaded_at": moment,
                }
            ]
        ),
    )


def _order(wh, settings, customer_id: str, order_id: str, when: datetime) -> None:
    wh.insert_frame(
        f"{settings.schemas.synth}.orders",
        pd.DataFrame(
            [
                {
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "ordered_at": when,
                    "blend_sku": "sim",
                    "amount_usd": 36.0,
                    "discount_usd": 4.0,
                    "refund_usd": 0.0,
                    "refunded_at": None,
                    "loaded_at": when,
                }
            ]
        ),
    )


def test_after_window_every_intervention_has_outcome(fresh_wh, settings):
    wh = fresh_wh
    _ready(wh, settings)
    kept = _customer(wh, settings, "t16-kept")
    gone = _customer(
        wh, settings, "t16-gone",
        status="CANCELLED",
        cancelled_at=DISPATCHED + timedelta(days=10),
        cancel_type="voluntary",
    )
    _log(wh, settings, "d-kept", kept, DISPATCHED, kind="checkin_email", status="DISPATCHED")
    _log(wh, settings, "d-gone", gone, DISPATCHED, kind="checkin_email", status="DISPATCHED")
    _log(wh, settings, "d-unknown", "nobody-assigned", DISPATCHED, kind="HOLDOUT", status="HOLDOUT")

    closed = advance(wh, settings, date(2025, 1, 1), 45)
    assert closed == 3
    outcomes = wh.query("SELECT decision_id, outcome FROM CHURN_OPS.OUTCOMES ORDER BY decision_id")
    assert set(outcomes["outcome"]) == {"retained", "churned", "unknown"}
    by_id = dict(zip(outcomes["decision_id"], outcomes["outcome"], strict=True))
    assert by_id == {"d-gone": "churned", "d-kept": "retained", "d-unknown": "unknown"}
    log = wh.query(
        "SELECT outcome FROM CHURN_OPS.INTERVENTION_LOG WHERE run_id = 't16-outcomes'"
    )
    assert set(log["outcome"]) == {"retained", "churned", "unknown"}


def test_before_window_outcome_stays_pending(fresh_wh, settings):
    wh = fresh_wh
    _ready(wh, settings)
    key = _customer(wh, settings, "t16-early")
    _log(wh, settings, "d-early", key, DISPATCHED, kind="checkin_email", status="DISPATCHED")
    assert close_outcomes(wh, settings, DISPATCHED + timedelta(days=19)) == 0
    log = wh.query("SELECT outcome, response FROM CHURN_OPS.INTERVENTION_LOG WHERE decision_id = 'd-early'")
    assert log.iloc[0]["outcome"] == "PENDING" and log.iloc[0]["response"] == "PENDING"
    assert wh.query("SELECT * FROM CHURN_OPS.OUTCOMES").empty


def test_response_from_email_events_and_redemptions(fresh_wh, settings):
    wh = fresh_wh
    _ready(wh, settings)
    opened = _customer(wh, settings, "t16-open")
    clicked = _customer(wh, settings, "t16-click")
    redeemed = _customer(wh, settings, "t16-redeem")
    silent = _customer(wh, settings, "t16-silent")
    _log(wh, settings, "d-open", opened, DISPATCHED, kind="checkin_email", status="DISPATCHED")
    _log(wh, settings, "d-click", clicked, DISPATCHED, kind="checkin_email", status="DISPATCHED")
    _log(wh, settings, "d-redeem", redeemed, DISPATCHED, kind="retention_offer", status="DISPATCHED")
    _log(wh, settings, "d-none", silent, DISPATCHED, kind="checkin_email", status="DISPATCHED")
    _email(wh, settings, "t16-open", "e-open", "open", DISPATCHED + timedelta(days=1))
    _email(wh, settings, "t16-click", "e-click-open", "open", DISPATCHED + timedelta(days=1))
    _email(wh, settings, "t16-click", "e-click", "click", DISPATCHED + timedelta(days=2))
    _order(wh, settings, "t16-redeem", "o-redeem", DISPATCHED + timedelta(days=3))

    assert close_outcomes(wh, settings, AFTER) == 4
    got = wh.query("SELECT decision_id, response FROM CHURN_OPS.OUTCOMES").set_index("decision_id")
    assert got.loc["d-open", "response"] == "opened"
    assert got.loc["d-click", "response"] == "clicked"
    assert got.loc["d-redeem", "response"] == "redeemed"
    assert got.loc["d-none", "response"] == "none"
    logged = wh.query("SELECT decision_id, response FROM CHURN_OPS.INTERVENTION_LOG").set_index("decision_id")
    assert logged.loc["d-redeem", "response"] == "redeemed"


def test_responder_appends_simulated_events_once(fresh_wh, settings):
    wh = fresh_wh
    _ready(wh, settings)
    key = _customer(wh, settings, "t16-sim")
    created = datetime(2025, 1, 1, 8, 0)
    wh.insert_frame(
        "CHURN_OPS.OUTBOX",
        pd.DataFrame(
            [
                {
                    "outbox_id": "box-1",
                    "channel": "email",
                    "intervention_type": "retention_offer",
                    "subject": "Hello",
                    "body": "Hi {{first_name}}",
                    "offer_code": "PCT_10_NEXT",
                    "discount_pct": 10.0,
                    "subscriber_key": key,
                    "created_at": created,
                    "status": "READY",
                }
            ]
        ),
    )
    assert respond(wh, settings, datetime(2025, 1, 2)) == 1
    events = wh.query(
        "SELECT event_type FROM THESIS_SYNTH.email_events WHERE campaign_id = 'box-1' ORDER BY event_type"
    )
    assert "send" in set(events["event_type"])
    again = respond(wh, settings, datetime(2025, 6, 1))
    assert again == 0
    assert len(wh.query("SELECT * FROM THESIS_SYNTH.email_events WHERE campaign_id = 'box-1'")) == len(events)
