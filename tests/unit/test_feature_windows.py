"""R2.2: every domain has a multi-window or slope feature; docs/features.md is generated from the registry."""

from __future__ import annotations

from pathlib import Path

from churn.features import registry

REPO = Path(__file__).resolve().parents[2]


def test_each_domain_has_multi_window_feature():
    assert set(registry.DOMAINS) == {"subscription", "subscription_events", "orders", "email", "support", "quiz"}
    for domain in registry.TREND_DOMAINS:
        trends = [f for f in registry.FEATURES if f.domain == domain and registry.is_trend(f)]
        assert trends, f"domain {domain!r} has no multi-window/slope feature"
    for f in registry.FEATURES:
        assert f.domain in registry.DOMAINS


SQL_FILE = {"subscription": "orders", "subscription_events": "subscription_events", "orders": "orders",
            "email": "email", "support": "support", "quiz": "quiz"}


def test_trend_features_span_two_distinct_windows():
    for f in registry.FEATURES:
        if f.trend:
            assert len(set(f.windows)) >= 2, f.name
            assert registry.is_trend(f)
    for name in ("blend_switch_count_365", "orders_per_30d_90", "quiz_retake_count_365", "paid_to_list_price_90"):
        assert not registry.is_trend(registry.FEATURE_BY_NAME[name])
    assert registry.is_trend(registry.FEATURE_BY_NAME["skip_rate_delta_60"])


def test_every_window_is_used_in_sql():
    for f in registry.FEATURES:
        text = (REPO / "sql" / "features" / f"{SQL_FILE[f.domain]}.sql").read_text()
        for w in f.windows:
            assert f"DATEADD(day, -{w}," in text, f"{f.name}: window {w} not used in SQL"


def test_every_feature_documented_and_labelled():
    for f in registry.FEATURES:
        assert f.description.strip() and f.missing.strip(), f.name
        assert registry.render_label(f.name, 1.0), f.name
    assert len(set(registry.FEATURE_COLUMNS)) == len(registry.FEATURE_COLUMNS)


def test_five_leading_indicators_registered():
    for name in ("email_open_rate_delta_30_90", "skip_rate_delta_60", "ticket_escalation_score",
                 "days_since_last_quiz", "blend_switch_count_90"):
        assert name in registry.FEATURE_BY_NAME
        assert registry.FEATURE_BY_NAME[name].leading_indicator
    assert sum(f.leading_indicator for f in registry.FEATURES) == 5


def test_features_md_matches_registry():
    committed = (REPO / "docs" / "features.md").read_text()
    assert committed == registry.render_features_md()
    for f in registry.FEATURES:
        assert f"`{f.name}`" in committed
