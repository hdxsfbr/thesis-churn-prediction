"""Drafts must carry the merge token, no contact details, and exactly the chosen offer."""

import pytest

from churn.agent.content import ContentInvalid, validate_message


def _check(body: str, *, offer="PCT_10_NEXT", discount=10.0) -> None:
    validate_message(
        "A note for {{first_name}}", body,
        offer_code=offer, discount_pct=discount, max_subject_chars=140, max_body_chars=2000,
    )


def test_rejects_email_phone_patterns_and_mismatched_offer():
    _check("Hello {{first_name}}. Offer PCT_10_NEXT (10%).")
    with pytest.raises(ContentInvalid):
        _check("Write me at a@b.com, {{first_name}}. Offer PCT_10_NEXT (10%).")
    with pytest.raises(ContentInvalid):
        _check("Call 415-555-1212, {{first_name}}. Offer PCT_10_NEXT (10%).")
    with pytest.raises(ContentInvalid):
        _check("Hello {{first_name}}. Offer PCT_25_NEXT_2 (10%).")
