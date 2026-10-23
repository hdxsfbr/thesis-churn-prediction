from churn.contract.loader import contract_for
from churn.contract.validate import validate
from churn.features.builder import build_features, render_feature_sql
from churn.features.registry import FEATURE_COLUMNS
from churn.synth.generator import generate
from churn.warehouse.migrate import apply_migrations

VIEWS = {'subscriptions', 'orders', 'subscription_events', 'products', 'email_events',
         'support_tickets', 'quiz_responses', 'customers_pii'}


def test_all_eight_views_mapped_and_validate(settings, fresh_wh):
    apply_migrations(fresh_wh, settings)
    generate(fresh_wh, settings)
    contract = contract_for(settings)
    assert set(contract.views) == VIEWS
    validate(fresh_wh, contract)


def test_no_stub_cte_used_with_default_contract(settings):
    assert 'WHERE 1 = 0' not in render_feature_sql(settings, contract_for(settings))


def test_every_feature_domain_populated_on_synthetic_data(settings, fresh_wh):
    apply_migrations(fresh_wh, settings)
    generate(fresh_wh, settings)
    frame = build_features(fresh_wh, settings, contract_for(settings), settings.synth.end_date)
    for col in FEATURE_COLUMNS:
        if col.startswith('has_'):
            assert frame[col].eq(1).any(), col
    assert frame.orders_total.gt(0).any()
