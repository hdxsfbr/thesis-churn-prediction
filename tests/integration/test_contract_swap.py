"""R1.3: a second schema with different physical names is read by editing configuration only."""

from __future__ import annotations

from datetime import date

import yaml

from churn.orchestration.runner import run_cycle

AS_OF = date(2025, 6, 30)


def test_second_schema_by_config_only(pipeline, tmp_path):
    wh, base = pipeline["wh"], pipeline["settings"]

    # A copy of the source under another schema, with a renamed table and column.
    wh.execute(
        "CREATE SCHEMA IF NOT EXISTS ALT_SOURCE;"
        "CREATE OR REPLACE TABLE ALT_SOURCE.SUBS_V2 AS "
        "SELECT * EXCLUDE (status), status AS state FROM THESIS_SYNTH.subscriptions;"
        "CREATE OR REPLACE TABLE ALT_SOURCE.orders AS SELECT * FROM THESIS_SYNTH.orders"
    )
    raw = yaml.safe_load(base.contract_file.read_text())
    for name, view in raw['views'].items():
        if name not in ('subscriptions', 'orders'):
            physical = view['object'].replace('{schema}', raw['schema'])
            wh.execute(f'CREATE OR REPLACE TABLE ALT_SOURCE.{name} AS SELECT * FROM {physical}')
    raw["schema"] = "ALT_SOURCE"
    raw["views"]["subscriptions"]["object"] = "{schema}.SUBS_V2"
    raw["views"]["subscriptions"]["columns"]["status"]["source"] = "state"
    path = tmp_path / "alt_contract.yaml"
    path.write_text(yaml.safe_dump(raw))
    alt = base.model_copy(update={"contract_path": str(path)})

    original = run_cycle(wh, base, AS_OF, run_id="t-swap-orig")
    swapped = run_cycle(wh, alt, AS_OF, run_id="t-swap-alt")

    assert swapped.status == "SUCCEEDED"
    assert swapped.scored == swapped.active_in_source == original.scored > 100
    q = "SELECT subscriber_key, churn_prob FROM CHURN_OPS.RISK_SCORES WHERE run_id = :r ORDER BY 1"
    assert wh.query(q, {"r": "t-swap-orig"}).equals(wh.query(q, {"r": "t-swap-alt"}))
