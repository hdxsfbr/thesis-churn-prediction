from unittest.mock import Mock

import pytest

from churn.contract.loader import ContractError, contract_for
from churn.contract.views import contract_ctes, key_map_frame, view_cte
from churn.features.builder import render_feature_sql


def test_pii_view_never_rendered(settings):
    contract = contract_for(settings)
    assert 'customers_pii' in contract.views
    assert contract.views['customers_pii'].pii
    assert 'customers_pii' not in contract_ctes(contract, 'pepper')
    assert 'customers_pii' not in render_feature_sql(settings, contract)
    wh = Mock()
    key_map_frame(wh, contract, 'pepper')
    assert 'customers_pii' not in wh.query.call_args.args[0]
    with pytest.raises(ContractError):
        view_cte(contract, 'customers_pii', 'pepper', include_raw_id=True)
    with pytest.raises(ContractError):
        contract_ctes(contract, 'pepper', ['customers_pii'])


def test_pii_metadata_still_validated(settings, fresh_wh):
    from churn.contract.validate import validate
    from churn.warehouse.migrate import apply_migrations
    apply_migrations(fresh_wh, settings)
    fresh_wh.execute('ALTER TABLE THESIS_SYNTH.customers_pii DROP COLUMN email')
    with pytest.raises(ContractError, match='customers_pii'):
        validate(fresh_wh, contract_for(settings))
