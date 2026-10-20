"""R2.2: every domain has a multi-window or slope feature; docs/features.md is generated from the registry."""

from __future__ import annotations

from pathlib import Path

from churn.features import registry

DOCS = Path(__file__).resolve().parents[2] / "docs" / "features.md"
REQUIRED_LEADING = {
    "email_open_rate_delta_30_90",
    "email_click_slope_90",
    "skip_rate_60_vs_prior_60",
    "skip_count_trend",
    "ticket_escalation_score",
    "days_since_last_quiz",
    "has_quiz",
    "quiz_retake_count_365",
    "blend_switch_count_90",
    "blend_switch_count_365",
    "blend_switch_rate_change",
}


def test_each_domain_has_multi_window_feature():
    for domain in registry.TREND_DOMAINS:
        feats = [f for f in registry.FEATURES if f.domain == domain]
        assert feats, f"no features for domain {domain}"
        assert any(len(f.windows) >= 2 or f.trend for f in feats), f"{domain} has no multi-window/slope feature"


def test_registry_is_consistent():
    names = [f.name for f in registry.FEATURES]
    assert len(names) == len(set(names))
    assert REQUIRED_LEADING <= set(names)
    assert {f.name for f in registry.FEATURES if f.leading_indicator} >= REQUIRED_LEADING - {
        "has_quiz", "quiz_retake_count_365", "blend_switch_count_365",
    }
    for domain in registry.TREND_DOMAINS:
        assert f"has_{domain}" in names
    for f in registry.FEATURES:
        assert f.label.strip(), f"{f.name} has no label template"


def test_features_md_matches_registry():
    assert DOCS.read_text() == registry.render_features_md()
