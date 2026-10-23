"""Email content: a whitelisted prompt, a validator, and the template fallback (R4.4, R7.2).

Escalation stays on its template. Email arms call the model only when a client is configured. Any failure,
a rejected draft, or an exhausted call budget uses the intervention template and logs ``content_source=template``.
Within one run, identical (intervention, archetype, factors, offer) prompts share one call. A rerun reads the
subject and body already stored on OUTBOX or APPROVALS and does not call the model again.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pandas as pd

from churn.agent.context import AgentContext
from churn.interventions.base import Action, Content, InterventionType
from churn.interventions.retention_offer import OFFERS
from churn.llm.budget import CallBudget
from churn.llm.client import LLMConfig, LLMError, call_model, current_config, resolve_client
from churn.settings import Settings

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?\d[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]\d{4}")


class ContentInvalid(ValueError):
    """The draft failed a structural check and must not be sent."""


@dataclass(frozen=True)
class PromptContext:
    """The only fields a prompt may carry. No identifiers and no free-form blob."""

    archetype: str
    factor_labels: tuple[str, ...]
    tenure_band: str
    blend_names: tuple[str, ...]
    goal_categories: tuple[str, ...]
    offer_code: str | None
    discount_pct: float | None


def prompt_context(context: AgentContext, action: Action) -> PromptContext:
    from churn.agent.policy import tenure_band

    days = getattr(context, "tenure_days", None)
    band = tenure_band(days) if days is not None else "unknown"
    blends = (context.recommended_sku,) if context.recommended_sku else ()
    goals = list(context.quiz_goals)
    if context.top_blend_goal and context.top_blend_goal not in goals:
        goals.append(context.top_blend_goal)
    discount = action.discount_pct
    return PromptContext(
        archetype=context.archetype,
        factor_labels=tuple(context.factor_labels),
        tenure_band=band,
        blend_names=blends,
        goal_categories=tuple(goals),
        offer_code=action.offer_code,
        discount_pct=None if discount is None else float(discount),
    )


def render_prompt(ctx: PromptContext) -> str:
    discount = "" if ctx.discount_pct is None else f"{ctx.discount_pct:g}"
    return (
        "Write a short retention note as JSON with keys subject and body. "
        "Both must contain the merge token {{first_name}} and no real names, emails or phone numbers.\n"
        f"archetype: {ctx.archetype}\n"
        f"tenure_band: {ctx.tenure_band}\n"
        f"factors: {', '.join(ctx.factor_labels)}\n"
        f"blends: {', '.join(ctx.blend_names)}\n"
        f"goals: {', '.join(ctx.goal_categories)}\n"
        f"offer_code: {ctx.offer_code or ''}\n"
        f"discount_pct: {discount}\n"
    )


def validate_message(
    subject: str,
    body: str,
    *,
    offer_code: str | None,
    discount_pct: float | None,
    max_subject_chars: int,
    max_body_chars: int,
) -> None:
    """Raise ContentInvalid when the draft is too long, misses the merge token, leaks a contact, or disagrees with the offer."""
    if not subject.strip() or not body.strip():
        raise ContentInvalid("empty subject or body")
    if len(subject) > max_subject_chars or len(body) > max_body_chars:
        raise ContentInvalid("subject or body is too long")
    if "{{first_name}}" not in subject or "{{first_name}}" not in body:
        raise ContentInvalid("missing {{first_name}} merge token")
    text = subject + "\n" + body
    if _EMAIL.search(text) or _PHONE.search(text):
        raise ContentInvalid("email or phone pattern in the draft")
    if offer_code and offer_code not in body:
        raise ContentInvalid("offer code missing from the draft")
    if discount_pct is not None and discount_pct > 0 and f"{discount_pct:g}" not in body:
        raise ContentInvalid("discount does not match the draft")
    for code in OFFERS:
        if code != offer_code and code in body:
            raise ContentInvalid(f"draft names {code}, not the chosen offer")


def build_content(intervention: InterventionType, context: AgentContext) -> Content:
    """Deterministic template. The agent cycle uses ``resolve_contents`` when a model may be called."""
    content = intervention.template(context)
    if not content.subject.strip() or not content.body.strip():
        raise ValueError(f"intervention {intervention.name!r} produced empty content")
    return content


def _cache_key(name: str, ctx: PromptContext) -> str:
    raw = json.dumps(
        [name, ctx.archetype, list(ctx.factor_labels), ctx.offer_code, ctx.discount_pct],
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _parse(raw: str) -> tuple[str, str]:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ContentInvalid("model did not return JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ContentInvalid("model JSON did not parse") from exc
    if not isinstance(data, dict) or "subject" not in data or "body" not in data:
        raise ContentInvalid("model JSON is missing subject or body")
    return str(data["subject"]), str(data["body"])


def load_stored(wh, settings: Settings, run_id: str) -> dict[str, Content]:
    """Content already written for this run id, keyed by decision_id. Empty on the first pass."""
    ops = settings.schemas.ops
    log = wh.query(
        f"SELECT decision_id, content_source, content_ref FROM {ops}.INTERVENTION_LOG WHERE run_id = :r",
        {"r": run_id},
    )
    if log.empty:
        return {}
    text: dict[str, tuple[str, str]] = {}
    outbox = wh.query(f"SELECT outbox_id, subject, body FROM {ops}.OUTBOX")
    approvals = wh.query(f"SELECT decision_id, subject, body FROM {ops}.APPROVALS")
    frames = []
    if not outbox.empty:
        frames.append(outbox.rename(columns={"outbox_id": "decision_id"}))
    if not approvals.empty:
        frames.append(approvals[["decision_id", "subject", "body"]])
    if frames:
        for row in pd.concat(frames, ignore_index=True).itertuples(index=False):
            if row.decision_id in text or pd.isna(row.subject) or pd.isna(row.body):
                continue
            if not str(row.subject).strip() or not str(row.body).strip():
                continue
            text[row.decision_id] = (str(row.subject), str(row.body))
    stored: dict[str, Content] = {}
    for row in log.itertuples(index=False):
        pair = text.get(row.decision_id)
        if pair is None:
            continue
        stored[row.decision_id] = Content(
            subject=pair[0], body=pair[1],
            source=str(row.content_source or "template"),
            ref=str(row.content_ref or "stored"),
        )
    return stored


def resolve_contents(
    items: list[tuple[InterventionType, AgentContext, Action, str]],
    *,
    stored: dict[str, Content] | None = None,
    settings: Settings | None = None,
) -> list[Content]:
    """One content per item, in order. ``settings is None`` forces the template (unit tests of decide)."""
    stored = stored or {}
    use_model = settings is not None
    config = current_config() if use_model else LLMConfig()
    client = resolve_client(config) if use_model else None
    budget = CallBudget(config.max_calls_per_run)
    results: list[Content | None] = [None] * len(items)
    pending: dict[str, list[int]] = {}
    prompts: dict[str, str] = {}
    meta: dict[str, tuple[InterventionType, AgentContext, Action]] = {}

    for index, (intervention, context, action, did) in enumerate(items):
        if did in stored:
            results[index] = stored[did]
            continue
        if client is None or intervention.channel != "email":
            results[index] = build_content(intervention, context)
            continue
        ctx = prompt_context(context, action)
        key = _cache_key(intervention.name, ctx)
        pending.setdefault(key, []).append(index)
        prompts.setdefault(key, render_prompt(ctx))
        meta.setdefault(key, (intervention, context, action))

    def one(key: str) -> Content:
        intervention, context, action = meta[key]
        fallback = build_content(intervention, context)
        if not budget.try_acquire():
            return fallback
        try:
            subject, body = _parse(call_model(client, prompts[key], config))
            validate_message(
                subject, body,
                offer_code=action.offer_code, discount_pct=action.discount_pct,
                max_subject_chars=config.max_subject_chars, max_body_chars=config.max_body_chars,
            )
        except (LLMError, ContentInvalid, ValueError, TypeError, TimeoutError):
            return fallback
        return Content(subject=subject, body=body, source="llm", ref=f"llm/{intervention.name}/{key[:12]}")

    if pending:
        workers = max(1, min(config.concurrency, len(pending)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            filled = dict(zip(pending, pool.map(one, list(pending)), strict=True))
        for key, indexes in pending.items():
            for index in indexes:
                results[index] = filled[key]
    return [c if c is not None else Content("", "", "template", "missing") for c in results]
