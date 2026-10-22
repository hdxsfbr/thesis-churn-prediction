import pandas as pd
import pytest

from churn.synth.generator import generate
from churn.warehouse.migrate import apply_migrations

VIEWS = {'subscriptions', 'orders', 'subscription_events', 'products', 'email_events',
         'support_tickets', 'quiz_responses', 'customers_pii'}


@pytest.fixture()
def source(fresh_wh, settings):
    apply_migrations(fresh_wh, settings)
    counts = generate(fresh_wh, settings)
    return fresh_wh, counts


def test_every_domain_has_view(source):
    wh, counts = source
    assert VIEWS <= counts.keys()
    for name in VIEWS:
        assert wh.query(f'SELECT COUNT(*) n FROM THESIS_SYNTH.{name}').iloc[0].n > 0


def test_edge_case_shares_above_zero(source, settings, tmp_path):
    from churn.synth.edge_cases import EDGE_CATEGORIES
    from churn.synth.profile import build_profile, write_profile
    wh, _ = source
    profile = build_profile(wh, settings, 'tiny')
    edges = profile[profile.section == 'edge_case'].set_index('item').value
    assert set(edges.index) == set(EDGE_CATEGORIES)
    assert edges.between(0, 1, inclusive='right').all()
    write_profile(wh, settings, 'tiny', tmp_path / 'profile.md')
    assert (tmp_path / 'profile.md').exists()
    assert len(wh.query('SELECT * FROM CHURN_OPS.DATA_PROFILE')) == len(profile)


def test_profile_cli(settings, fresh_wh, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from churn import cli
    apply_migrations(fresh_wh, settings)
    monkeypatch.setattr(cli, '_open', lambda: (settings, fresh_wh))
    from churn.synth import profile
    original = profile.write_profile
    monkeypatch.setattr(profile, 'write_profile',
                        lambda wh, cfg, scale: original(wh, cfg, scale, tmp_path / 'data-profile.md'))
    result = CliRunner().invoke(cli.app, ['synth', '--profile'])
    assert result.exit_code == 0, result.output
    assert (tmp_path / 'data-profile.md').exists()
    assert len(fresh_wh.query('SELECT * FROM CHURN_OPS.DATA_PROFILE')) > 8


def test_churn_events_span_label_and_eval_window(source):
    wh, _ = source
    rows = wh.query("SELECT cancelled_at FROM THESIS_SYNTH.subscriptions WHERE cancel_type='voluntary'")
    dates = pd.to_datetime(rows.cancelled_at)
    assert (dates.max() - dates.min()).days >= 210
    assert dates.dt.to_period('M').nunique() >= 7
    assert len(wh.query("SELECT * FROM THESIS_SYNTH.subscriptions WHERE cancel_type='involuntary'")) > 0
