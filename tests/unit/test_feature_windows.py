"""Registry checks: multi-window coverage per domain, required indicators, generated docs."""

from __future__ import annotations

from pathlib import Path

from churn.features import registry
from churn.features.registry import FEATURES, render_features_md

ROOT = Path(__file__).resolve().parents[2]
DATA_DOMAINS = {"orders", "subscription_events", "email", "support", "quiz"}
REQUIRED_INDICATORS = {
    "email_open_rate_delta_30_90",
    "skip_rate_60_vs_prior_60",
    "ticket_escalation_score",
    "days_since_last_quiz",
    "blend_switch_count_90",
}
SECTION_6_2 = {"email_click_slope_90", "skip_count_trend", "blend_switch_rate_change"}


def test_each_domain_has_multi_window_feature():
    assert {f.domain for f in FEATURES} >= DATA_DOMAINS
    for domain in DATA_DOMAINS:
        multi = [f for f in FEATURES if f.domain == domain and (len(f.windows) >= 2 or f.trend)]
        assert multi, f"domain {domain!r} has no multi-window or slope feature"


def test_required_indicators_and_section_6_2_features_registered():
    by_name = registry.FEATURE_BY_NAME
    for name in REQUIRED_INDICATORS:
        assert by_name[name].leading_indicator, name
    assert SECTION_6_2 <= set(by_name)


def test_every_domain_has_a_has_indicator_or_defined_missing_policy():
    for domain in DATA_DOMAINS - {"orders"}:
        assert f"has_{domain}" in registry.FEATURE_BY_NAME
    assert all(f.missing.strip() for f in FEATURES)
    assert len({f.name for f in FEATURES}) == len(FEATURES)


def test_features_md_matches_registry():
    assert (ROOT / "docs" / "features.md").read_text() == render_features_md()
    text = render_features_md()
    for f in FEATURES:
        assert f"`{f.name}`" in text
