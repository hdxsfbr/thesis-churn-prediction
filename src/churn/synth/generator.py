"""Eight synthetic source domains and evaluation-only latent truth in THESIS_SYNTH.

Deterministic for a given ``synth.seed``. Imports only warehouse/settings (and its own latent module).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from churn.settings import Settings
from churn.synth import edge_cases, latent
from churn.warehouse.base import Warehouse

CHUNK = 500_000


def _ts(start: datetime, days: np.ndarray) -> pd.Series:
    secs = np.round(np.asarray(days, dtype=float) * 86400).astype("int64")
    return pd.Series(pd.Timestamp(start) + pd.to_timedelta(secs, unit="s")).astype("datetime64[us]")


def build_frames(settings: Settings, n: int) -> dict[str, pd.DataFrame]:
    cfg = settings.synth
    rng = np.random.default_rng(cfg.seed)
    end = datetime.combine(cfg.end_date, time(0))
    hist_start = end - timedelta(days=30 * cfg.history_months)
    total_days = float((end - hist_start).days)

    traits = latent.draw_traits(rng, n)
    start_day = rng.uniform(0, total_days - 14, n)
    interval = rng.choice(latent.INTERVALS, n)
    price = rng.choice(latent.PRICES, n)
    to_cancel, voluntary, _ = latent.draw_cancellation(rng, traits, start_day)
    archetype = latent.draw_archetypes(cfg.seed, n)
    reason = latent.reasons_for(cfg.seed, archetype)
    cancel_day = start_day + to_cancel
    cancelled = cancel_day <= total_days

    status = rng.choice(["ACTIVE", "PAUSED", "PAST_DUE"], n, p=[0.92, 0.05, 0.03])
    status = np.where(cancelled, "CANCELLED", status)
    cancel_type = np.where(cancelled, np.where(voluntary, "voluntary", "involuntary"), None)
    reason_code = np.where(cancelled, np.where(voluntary, reason, "PAYMENT_FAILED"), None)

    ids = np.arange(n)
    started = _ts(hist_start, start_day)
    cancelled_at = _ts(hist_start, cancel_day).where(cancelled, pd.NaT)
    subs = pd.DataFrame(
        {
            "customer_id": [f"c{i:07d}" for i in ids],
            "subscription_id": [f"s{i:07d}" for i in ids],
            "status": status,
            "started_at": started,
            "cancelled_at": cancelled_at,
            "cancel_type": cancel_type,
            "cancel_reason_code": reason_code,
            "price_usd": price,
            "interval_days": interval.astype("int64"),
            "loaded_at": started,
        }
    )

    stretch = latent.emission(archetype, 'stretch')
    idx, day = latent.order_times(
        rng, start_day, interval, cancel_day, voluntary, total_days + 1, stretch_scale=stretch
    )
    m = len(idx)
    amount = price[idx]
    discount_boost = latent.emission(archetype, 'discount')
    refund_boost = latent.emission(archetype, 'refund')
    discounted = rng.random(m) < 0.05 + 0.7 * traits.price_sensitivity[idx] + discount_boost[idx]
    discount = np.where(discounted, amount * rng.uniform(0.1, 0.3, m), 0.0).round(2)
    refunded = rng.random(m) < 0.01 + 0.15 * traits.fatigue[idx] + refund_boost[idx]
    refund_day = day + 5
    refunded &= refund_day <= total_days
    ordered_at = _ts(hist_start, day)
    orders = pd.DataFrame(
        {
            "order_id": [f"o{i:09d}" for i in range(m)],
            "customer_id": [f"c{i:07d}" for i in idx],
            "ordered_at": ordered_at,
            "blend_sku": rng.choice(latent.SKUS, m),
            "amount_usd": amount,
            "discount_usd": discount,
            "refund_usd": np.where(refunded, amount - discount, 0.0).round(2),
            "refunded_at": _ts(hist_start, refund_day).where(refunded, pd.NaT),
            "loaded_at": ordered_at,
        }
    )
    frames = {'subscriptions': subs, 'orders': orders}
    frames.update(_domains(cfg.seed, archetype, idx, day, hist_start, start_day))
    frames['latent_truth'] = pd.DataFrame({
        'customer_id': subs.customer_id, 'archetype': archetype,
        'price_sensitivity': traits.price_sensitivity, 'fatigue': traits.fatigue,
        'hazard_per_day': traits.hazard_per_day, 'churn_cause': cancel_type,
    })
    return edge_cases.apply(frames, cfg.seed)


def _domains(seed, archetype, idx, day, hist_start, start_day):
    """Emit observable events on each customer's live timeline, never after cancellation."""
    rng = np.random.default_rng([seed, 4244])
    n, m = len(archetype), len(idx)
    customers = np.array([f'c{i:07d}' for i in range(n)])
    ts = _ts(hist_start, day)
    events = pd.DataFrame({
        'subscription_id': [f's{i:07d}' for i in idx],
        'event_id': [f'se{i}' for i in range(m)],
        'event_type': latent.emission(archetype, 'event')[idx],
        'event_ts': ts, 'loaded_at': ts,
    })
    # Occasional neutral events keep archetype emissions probabilistic.
    neutral = rng.random(m) < .15
    events.loc[neutral, 'event_type'] = rng.choice(['resume', 'reactivate', 'modify'], neutral.sum())
    email_parts = []
    age = day - start_day[idx]
    open_prob = latent.emission(archetype, 'open')[idx] * np.where(
        archetype[idx] == 'fatigued', np.exp(-age / 180), 1.)
    for kind, mask in [('send', np.ones(m, dtype=bool)),
                       ('open', rng.random(m) < open_prob),
                       ('click', rng.random(m) < open_prob * .35),
                       ('unsubscribe', rng.random(m) < .005)]:
        times = ts[mask].reset_index(drop=True)
        email_parts.append(pd.DataFrame({
            'customer_id': customers[idx[mask]],
            'event_id': [f'em{kind}{i}' for i in np.flatnonzero(mask)], 'event_type': kind,
            'campaign_id': 'monthly', 'flow_id': 'retention',
            # UTC instants represented with a real source offset; contract normalises these.
            'event_ts': times.dt.tz_localize('UTC').dt.tz_convert('America/New_York'),
            'loaded_at': times,
        }))
    ticket_mask = rng.random(m) < .12
    ti = idx[ticket_mask]
    category = latent.emission(archetype, 'category')[ti]
    noise = rng.random(len(ti)) < latent.REASON_NOISE
    category[noise] = rng.choice([v['category'] for v in latent.EMISSIONS.values()] + ['other'], noise.sum())
    times = ts[ticket_mask].reset_index(drop=True)
    tickets = pd.DataFrame({
        'customer_id': customers[ti], 'ticket_id': [f't{i}' for i in range(len(ti))],
        'created_at': times, 'category': category, 'escalated': rng.random(len(ti)) < .25,
        'resolution_hours': rng.lognormal(3, .7, len(ti)),
        'sentiment_score': rng.uniform(-1, .5, len(ti)), 'loaded_at': times,
    })
    # Initial quiz plus archetype-dependent retakes; 24 answers per session.
    retake = rng.random(m) < latent.emission(archetype, 'quiz')[idx]
    qi = np.concatenate([np.arange(n), idx[retake]])
    qt = pd.concat([_ts(hist_start, start_day), ts[retake]], ignore_index=True)
    repeat = np.repeat(np.arange(len(qi)), 24)
    qidx = qi[repeat]
    times = qt.iloc[repeat].reset_index(drop=True)
    quiz = pd.DataFrame({
        'answer_id': [f'a{i}' for i in range(len(repeat))], 'customer_id': customers[qidx],
        'quiz_session_id': [f'q{i}' for i in repeat],
        'question_key': np.tile([f'question_{i}' for i in range(24)], len(qi)),
        'answer_value': rng.choice(['low', 'medium', 'high'], len(repeat)), 'answered_at': times,
        'recommended_sku': latent.emission(archetype, 'sku')[qidx], 'loaded_at': times,
    })
    products = pd.DataFrame({
        'blend_sku': latent.SKUS, 'blend_name': [s.title() + ' blend' for s in latent.SKUS],
        'goal_category': latent.SKUS, 'price_usd': 39., 'loaded_at': hist_start,
    })
    pii = pd.DataFrame({
        'customer_id': customers, 'first_name': rng.choice(['Alex', 'Sam', 'Taylor'], n),
        'email': [f'customer{i}@example.com' for i in range(n)],
        'phone': [f'+1555{i:07d}' for i in range(n)],
        'address': [f'{i + 1} Fictional Street' for i in range(n)],
        'loaded_at': _ts(hist_start, start_day),
    })
    return {'subscription_events': events, 'products': products,
            'email_events': pd.concat(email_parts, ignore_index=True), 'support_tickets': tickets,
            'quiz_responses': quiz, 'customers_pii': pii}


def latent_archetypes(settings: Settings, scale: str = "tiny") -> pd.Series:
    """The generator's latent archetype per customer_id: ground truth for judging the classifier, never a feature."""
    n = settings.synth.scales[scale]
    return pd.Series(latent.draw_archetypes(settings.synth.seed, n), index=[f"c{i:07d}" for i in range(n)])


def generate(wh: Warehouse, settings: Settings, scale: str = "tiny") -> dict[str, int]:
    """Atomically replace every synthetic source table and latent truth."""
    try:
        n = settings.synth.scales[scale]
    except KeyError:
        raise ValueError(f"unknown scale {scale!r}; choose one of {sorted(settings.synth.scales)}") from None
    frames = build_frames(settings, n)
    schema = settings.schemas.synth
    with wh.transaction():
        for name, frame in frames.items():
            wh.execute(f'DELETE FROM {schema}.{name}')
            for lo in range(0, len(frame), CHUNK):
                wh.insert_frame(f'{schema}.{name}', frame.iloc[lo:lo + CHUNK])
    return {name: len(frame) for name, frame in frames.items()}
