"""The committed evaluation was rerun on the mapped synthetic source (R3.8, R3.6)."""

import re
from pathlib import Path

DOCS = Path("docs")
REPORT = (DOCS / "eval-report.md").read_text()
CARD = (DOCS / "model-card.md").read_text()

# T8's committed holdout, recorded again in tickets/T30.md. This rerun must be at least that large.
T8_HOLDOUT_ROWS = 55536

VIEWS = (
    "customers_pii",
    "email_events",
    "orders",
    "products",
    "quiz_responses",
    "subscription_events",
    "subscriptions",
    "support_tickets",
)


def _provenance() -> str:
    start = REPORT.index("## Data provenance")
    end = REPORT.index("## R3.6 targets")
    return REPORT[start:end]


def test_report_states_full_source_all_eight_views():
    section = _provenance()
    for view in VIEWS:
        assert view in section
    assert "Synth scale:" in section
    assert re.search(r"Synth scale:\*\* (?!not given)", section)


def test_domain_coverage_every_domain_populated():
    section = _provenance()
    rows = [line for line in section.splitlines() if line.startswith(("| has_", "| orders_total"))]
    assert rows
    for line in rows:
        share = line.rsplit("|", 2)[-2].strip().rstrip("%")
        assert float(share) > 0, line


def test_committed_report_has_every_r3_6_line():
    for name in ("top-2% precision", "Top-decile lift", "Holdout recall at the boundary", "Holdout precision at the boundary"):
        assert name in REPORT
    assert "## Precision ceilings" in REPORT
    targets = [line for line in REPORT.splitlines() if line.startswith("| R3.6")]
    assert len(targets) >= 3
    assert all(("PASS" in line) or ("MISS" in line) for line in targets)
    holdout = int(re.search(r"\*\*Holdout size:\*\* (\d+) scored", REPORT).group(1))
    assert holdout >= T8_HOLDOUT_ROWS
    assert "not real-world accuracy" in CARD
    assert "90-day impact target can only be checked after go-live" in CARD
