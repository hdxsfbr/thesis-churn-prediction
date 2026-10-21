"""T7 (A3): cancel reason codes and ticket categories map to the four archetypes; anything else has no label."""

from __future__ import annotations

import pytest

from churn.model.archetype import ARCHETYPES, load_labels


@pytest.fixture(scope="module")
def labels():
    return load_labels()


def test_reason_codes_and_ticket_categories_map_to_archetypes(labels):
    assert set(ARCHETYPES) == {"price_sensitive", "efficacy_dissatisfied", "fatigued", "life_change"}
    assert labels.label("TOO_EXPENSIVE") == "price_sensitive"
    assert labels.label("NOT_WORKING") == "efficacy_dissatisfied"
    assert labels.label("TOO_MANY") == "fatigued"
    assert labels.label("LIFE_CHANGE") == "life_change"
    # case and whitespace do not matter
    assert labels.label(" too_expensive ") == "price_sensitive"
    # every archetype is reachable from both signals, and only maps to known archetypes
    assert set(labels.reason_codes.values()) == set(ARCHETYPES)
    assert set(labels.ticket_categories.values()) == set(ARCHETYPES)
    # ticket categories label a churner whose reason code is missing or unmapped
    for category, archetype in labels.ticket_categories.items():
        assert labels.label(None, [category]) == archetype
        assert labels.label("OTHER", [category]) == archetype
    # the reason code wins over tickets
    category = next(c for c, a in labels.ticket_categories.items() if a != "price_sensitive")
    assert labels.label("TOO_EXPENSIVE", [category]) == "price_sensitive"
    # with several tickets the most frequent mapped category decides; unmapped categories are ignored
    price_cat = next(c for c, a in labels.ticket_categories.items() if a == "price_sensitive")
    fatigue_cat = next(c for c, a in labels.ticket_categories.items() if a == "fatigued")
    assert labels.label(None, [fatigue_cat, "zzz-unknown", price_cat, price_cat]) == "price_sensitive"


def test_unmapped_reason_has_no_label(labels):
    assert labels.label("OTHER") is None
    assert labels.label("PAYMENT_FAILED") is None
    assert labels.label("SOMETHING_NEW") is None
    assert labels.label(None) is None
    assert labels.label("") is None
    assert labels.label(float("nan")) is None
    assert labels.label("OTHER", ["not-a-category"]) is None
    assert labels.label(None, []) is None
