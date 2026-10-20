"""contract.validate fails the run before any write when the mapped source does not match."""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from churn.contract.loader import ContractError, load_contract
from churn.contract.validate import validate
from churn.orchestration.runner import RunFailed, run_cycle
from churn.settings import Settings

AS_OF = date(2025, 6, 30)


def _settings_with_contract(base: Settings, tmp_path, mutate) -> Settings:
    raw = yaml.safe_load(base.contract_file.read_text())
    mutate(raw)
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(raw))
    return base.model_copy(update={"contract_path": str(path)})


def test_missing_column_fails_before_write(pipeline, tmp_path):
    wh, base = pipeline["wh"], pipeline["settings"]

    def mutate(raw):
        raw["views"]["orders"]["columns"]["amount_usd"]["source"] = "no_such_column"

    settings = _settings_with_contract(base, tmp_path, mutate)
    with pytest.raises(ContractError, match="no_such_column"):
        validate(wh, load_contract(settings.contract_file))

    with pytest.raises(RunFailed, match="ContractError"):
        run_cycle(wh, settings, AS_OF, run_id="t-contract-missing")
    n = wh.query("SELECT COUNT(*) AS n FROM CHURN_OPS.RISK_SCORES WHERE run_id = 't-contract-missing'")
    assert n.iloc[0]["n"] == 0
    run = wh.query("SELECT status, error_msg FROM CHURN_OPS.RUNS WHERE run_id = 't-contract-missing'").iloc[0]
    assert run["status"] == "FAILED"
    assert "ContractError" in run["error_msg"]


def test_missing_object_and_incompatible_type(pipeline, tmp_path):
    wh, base = pipeline["wh"], pipeline["settings"]

    def missing_object(raw):
        raw["views"]["orders"]["object"] = "{schema}.nope"

    with pytest.raises(ContractError, match="nope"):
        validate(wh, load_contract(_settings_with_contract(base, tmp_path, missing_object).contract_file))

    def wrong_type(raw):
        raw["views"]["orders"]["columns"]["amount_usd"]["source"] = "blend_sku"  # varchar, declared number

    with pytest.raises(ContractError, match="incompatible"):
        validate(wh, load_contract(_settings_with_contract(base, tmp_path, wrong_type).contract_file))


def test_valid_contract_passes(pipeline):
    validate(pipeline["wh"], load_contract(pipeline["settings"].contract_file))
