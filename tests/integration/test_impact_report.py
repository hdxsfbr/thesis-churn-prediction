"""sql/reports/impact.sql returns a churn rate for treated subscribers and for the holdout."""

import pandas as pd

from churn.warehouse.migrate import apply_migrations


def test_impact_sql_returns_treated_and_control_churn_rate(fresh_wh, settings):
    wh = fresh_wh
    apply_migrations(wh, settings)
    log = pd.DataFrame(
        [
            {"decision_id": "t-treated-churn", "subscriber_key": "a", "intervention_type": "checkin_email"},
            {"decision_id": "t-treated-keep", "subscriber_key": "b", "intervention_type": "formulation"},
            {"decision_id": "t-control-churn", "subscriber_key": "c", "intervention_type": "HOLDOUT"},
            {"decision_id": "t-control-keep", "subscriber_key": "d", "intervention_type": "HOLDOUT"},
            {"decision_id": "t-unknown", "subscriber_key": "e", "intervention_type": "checkin_email"},
        ]
    )
    outcomes = pd.DataFrame(
        [
            {"decision_id": "t-treated-churn", "response": "none", "outcome": "churned", "window_days": 30},
            {"decision_id": "t-treated-keep", "response": "opened", "outcome": "retained", "window_days": 30},
            {"decision_id": "t-control-churn", "response": "none", "outcome": "churned", "window_days": 30},
            {"decision_id": "t-control-keep", "response": "none", "outcome": "retained", "window_days": 30},
            {"decision_id": "t-unknown", "response": "none", "outcome": "unknown", "window_days": 30},
        ]
    )
    wh.insert_frame("CHURN_OPS.INTERVENTION_LOG", log)
    wh.insert_frame("CHURN_OPS.OUTCOMES", outcomes)
    rates = wh.query((settings.sql_dir / "reports" / "impact.sql").read_text()).set_index("arm")
    assert set(rates.index) == {"treated", "control"}
    assert int(rates.loc["treated", "n"]) == 2
    assert int(rates.loc["control", "n"]) == 2
    assert rates.loc["treated", "churn_rate"] == 0.5
    assert rates.loc["control", "churn_rate"] == 0.5
