"""Retention offer from the A6 catalogue, never above the configured maximum discount."""

from __future__ import annotations

from typing import TYPE_CHECKING

from churn.interventions.base import Action, Content, InterventionType

if TYPE_CHECKING:
    from churn.agent.context import AgentContext

# code -> (discount_pct, cost_usd, expected uplift). Ordered cheapest first.
OFFERS: dict[str, tuple[float, float, float]] = {
    "FREE_SHIPPING": (0.0, 6.0, 0.04),
    "PCT_10_NEXT": (10.0, 5.0, 0.06),
    "FREE_SAMPLE_BLEND": (0.0, 8.0, 0.07),
    "PCT_20_NEXT": (20.0, 10.0, 0.10),
    "PCT_25_NEXT_2": (25.0, 25.0, 0.14),
}
VALUE_USD = 120.0  # expected remaining margin used to size the step up to a pricier offer
SUBJECT = "A thank-you from us, {{first_name}}"
BODY = (
    "Hi {{first_name}},\n\n"
    "As a thank-you for being with us, here is an offer on your next box: {description}. "
    "Use code {code} at checkout.\n\n"
    "Warm regards,\nThe tea team"
)


def _cost_sorted(codes: list[str]) -> list[str]:
    return sorted(codes, key=lambda c: (OFFERS[c][1], OFFERS[c][0]))


class RetentionOffer(InterventionType):
    name = "retention_offer"
    channel = "email"

    def _choose(self, context: AgentContext) -> tuple[str, float]:
        cap = context.max_discount_pct
        eligible = [c for c in context.offer_catalogue if c in OFFERS and OFFERS[c][0] <= cap]
        if not eligible:
            # Catalogue entries all exceed the cap: clip the first known one down to the cap.
            known = [c for c in context.offer_catalogue if c in OFFERS]
            if not known:
                raise ValueError(
                    f"offer_catalogue has no known offer codes: {sorted(context.offer_catalogue)}; "
                    f"known codes: {sorted(OFFERS)}"
                )
            code = _cost_sorted(known)[0]
            return code, min(OFFERS[code][0], cap)
        ordered = _cost_sorted(eligible)
        best = ordered[0]
        for nxt in ordered[1:]:
            gain = (OFFERS[nxt][2] - OFFERS[best][2]) * context.risk * VALUE_USD
            if gain > OFFERS[nxt][1] - OFFERS[best][1]:
                best = nxt
        return best, min(OFFERS[best][0], cap)

    def action(self, context: AgentContext) -> Action:
        code, discount = self._choose(context)
        return Action(params={"offer": code}, offer_code=code, discount_pct=discount)

    def template(self, context: AgentContext) -> Content:
        code, discount = self._choose(context)
        description = f"{discount:g}% off" if discount else code.replace("_", " ").lower()
        body = BODY.replace("{description}", description).replace("{code}", code)
        return Content(subject=SUBJECT, body=body, source="template", ref=f"retention_offer/{code}")

    def cost_usd(self, context: AgentContext | Action) -> float:
        # The gate costs the selected action; policy callers may still supply context.
        if isinstance(context, Action):
            return OFFERS[context.offer_code][1]
        code, _ = self._choose(context)
        return OFFERS[code][1]
