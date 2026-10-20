"""Synthetic source data written into THESIS_SYNTH through the warehouse protocol.

A latent-state simulator (see ``latent``): each customer has a latent archetype and traits that drive both
churn and the *emitted* events across every contract view (subscriptions, orders, subscription events,
products, email, support, quiz, PII). Edge cases are injected on top (``edge_cases``). Deterministic for a
given ``synth.seed``. Imports only warehouse/settings (and its own modules): never features or model.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from churn.settings import Settings
from churn.synth import edge_cases, latent
from churn.warehouse.base import Warehouse

CHUNK = 500_000
EMAIL_CADENCE_DAYS = 28.0
BASELINE_TICKETS_PER_YEAR = 0.15
QUIZ_QUESTIONS = 24
QUIZ_KEYS = [f"Q{i:02d}" for i in range(1, QUIZ_QUESTIONS + 1)] + ["REC_PRIMARY_SKU", "REC_SECONDARY_SKU"]
TICKET_CATEGORIES = [
    "SHIPPING", "BILLING_PRICE", "PRODUCT_NOT_WORKING", "DELIVERY_FREQUENCY", "ADDRESS_CHANGE", "GENERAL",
]  # fmt: skip
TICKET_BASELINE_P = [0.30, 0.15, 0.15, 0.10, 0.10, 0.20]
ARCHETYPE_TICKET = {
    "price": "BILLING_PRICE",
    "efficacy": "PRODUCT_NOT_WORKING",
    "fatigue": "DELIVERY_FREQUENCY",
    "life_change": "ADDRESS_CHANGE",
}
GOALS = {"SLEEP": "sleep", "FOCUS": "focus", "GUT": "digestion", "ENERGY": "energy", "CALM": "stress", "SKIN": "beauty"}
FIRST_NAMES = np.array(
    ["Ana", "Ben", "Chloe", "Dev", "Elena", "Femi", "Grace", "Hugo", "Iris", "Jon", "Kira", "Leo", "Maya",
     "Nils", "Omar", "Pia", "Quinn", "Rosa", "Sam", "Tara", "Uma", "Vik", "Wen", "Xavi", "Yara", "Zane"]
)  # fmt: skip
LAST_NAMES = np.array(
    ["Adams", "Baker", "Cruz", "Diaz", "Evans", "Fox", "Gray", "Hill", "Ito", "Jones", "Khan", "Lee",
     "Moore", "Ng", "Okafor", "Patel", "Quist", "Reyes", "Silva", "Tran", "Usman", "Vega", "Wong", "Young"]
)  # fmt: skip


def _ts(start: datetime, days: np.ndarray) -> pd.Series:
    secs = np.round(np.asarray(days, dtype=float) * 86400).astype("int64")
    return pd.Series(pd.Timestamp(start) + pd.to_timedelta(secs, unit="s")).astype("datetime64[us]")


def _ids(prefix: str, values: np.ndarray, width: int) -> np.ndarray:
    return (prefix + pd.Series(values).astype(str).str.zfill(width)).to_numpy(dtype=object)



# --------------------------------------------------------------------------- per-domain emitters


def _subscription_events(rng, hist_start, n, tr, start_day, cancel_day, cancelled, voluntary, reason,
                         status, total_days, idx, day, blend, discounted, discount):  # fmt: skip
    stop = np.minimum(cancel_day, total_days)
    near = voluntary[idx] & ((cancel_day[idx] - day) < latent.WIND_DOWN_DAYS)
    boost = 1.0 + near
    m = len(idx)
    parts = [(np.arange(n), start_day, "CREATED", np.full(n, None, dtype=object))]

    sk = rng.random(m) < np.minimum(0.02 + 0.25 * tr.fatigue[idx] * boost, 0.9)
    skip_day = np.maximum(day - rng.uniform(1, 3, m), start_day[idx])
    parts.append((idx[sk], skip_day[sk], "SKIPPED", np.full(sk.sum(), None, dtype=object)))

    sw = rng.random(m) < np.minimum(0.01 + 0.2 * tr.efficacy_gap[idx] * boost, 0.9)
    new_sku = rng.choice(latent.SKUS, m)
    detail = np.char.add(np.char.add(blend.astype(str), "->"), new_sku)
    parts.append((idx[sw], day[sw], "BLEND_SWITCHED", detail[sw].astype(object)))

    parts.append((idx[discounted], day[discounted], "DISCOUNT_REDEEMED",
                  np.round(discount[discounted], 2).astype(str).astype(object)))  # fmt: skip

    ac = rng.random(m) < np.minimum(0.1 * tr.life_event[idx] * (1.0 + 2.0 * near), 0.9)
    parts.append((idx[ac], day[ac], "ADDRESS_CHANGED", np.full(ac.sum(), None, dtype=object)))

    # Life-change customers pause abruptly, usually for a few weeks.
    lc = (tr.archetype == "life_change") & (rng.random(n) < 0.2)
    p_day = start_day + rng.uniform(0.2, 0.9, n) * (stop - start_day)
    lc &= p_day < stop
    r_day = p_day + rng.uniform(10, 40, n)
    back = lc & (r_day < stop)
    parts.append((np.flatnonzero(lc), p_day[lc], "PAUSED", np.full(lc.sum(), None, dtype=object)))
    parts.append((np.flatnonzero(back), r_day[back], "RESUMED", np.full(back.sum(), None, dtype=object)))
    paused_now = status == "PAUSED"
    parts.append((np.flatnonzero(paused_now), total_days - rng.uniform(1, 30, n)[paused_now], "PAUSED",
                  np.full(paused_now.sum(), None, dtype=object)))  # fmt: skip

    # Payment failures: dunning before an involuntary cancellation, or a current past-due state.
    invol = cancelled & ~voluntary
    for back_off in (14.0, 7.0, 0.0):
        parts.append((np.flatnonzero(invol), cancel_day[invol] - back_off, "PAYMENT_FAILED",
                      np.full(invol.sum(), None, dtype=object)))  # fmt: skip
    past_due = status == "PAST_DUE"
    parts.append((np.flatnonzero(past_due), total_days - rng.uniform(1, 10, n)[past_due], "PAYMENT_FAILED",
                  np.full(past_due.sum(), None, dtype=object)))  # fmt: skip

    parts.append((np.flatnonzero(cancelled), cancel_day[cancelled], "CANCELLED", reason[cancelled].astype(object)))

    e_idx = np.concatenate([p[0] for p in parts])
    e_day = np.concatenate([p[1] for p in parts])
    e_kind = np.concatenate([np.full(len(p[0]), p[2], dtype=object) for p in parts])
    e_detail = np.concatenate([p[3] for p in parts])
    o = np.lexsort((e_day, e_idx))
    e_idx, e_day, e_kind, e_detail = e_idx[o], e_day[o], e_kind[o], e_detail[o]
    ts = _ts(hist_start, e_day)
    return pd.DataFrame(
        {
            "event_id": _ids("e", np.arange(len(e_idx)), 9),
            "customer_id": _ids("c", e_idx, 7),
            "subscription_id": _ids("s", e_idx, 7),
            "event_type": e_kind,
            "event_ts": ts,
            "detail": e_detail,
            "loaded_at": ts,
        }
    )


def _email_events(rng, hist_start, n, tr, start_day, cancel_day, voluntary, has_email, total_days):
    stop = np.minimum(cancel_day, total_days)
    first = start_day + 0.1
    sends = np.where(has_email & (first < stop), np.floor((stop - first) / EMAIL_CADENCE_DAYS) + 1, 0).astype(int)
    unsub = (rng.random(n) < 0.08 * (0.3 + tr.fatigue)) & (sends > 0)
    stop_at = (rng.random(n) * sends).astype(int) + 1
    counts = np.where(unsub, stop_at, sends)

    e = np.repeat(np.arange(n), counts)
    j = np.arange(len(e)) - np.repeat(np.cumsum(counts) - counts, counts)
    k = len(e)
    send_day = np.clip(first[e] + EMAIL_CADENCE_DAYS * j + rng.uniform(-1, 1, k), start_day[e], stop[e])
    near = voluntary[e] & ((cancel_day[e] - send_day) < 90)
    p_open = 0.45 * (1.0 - 0.6 * tr.fatigue[e] * near) * (1.0 - 0.3 * near)
    opened = rng.random(k) < p_open
    clicked = opened & (rng.random(k) < 0.25)
    last_unsub = unsub[e] & (j == counts[e] - 1)
    flow = np.where(j == 0, "WELCOME", rng.choice(["NEWSLETTER", "REPLENISH", "PROMO"], k))
    campaign = _ids("cmp", np.floor(send_day / 7).astype(int), 3)

    def rows(mask, offset, kind):
        d = send_day[mask] + offset
        keep = d <= total_days
        return pd.DataFrame(
            {
                "customer_idx": e[mask][keep],
                "event_type": kind,
                "campaign_id": campaign[mask][keep],
                "flow": flow[mask][keep],
                "day": d[keep],
            }
        )

    allk = np.ones(k, dtype=bool)
    frame = pd.concat(
        [
            rows(allk, 0.0, "SENT"),
            rows(opened, rng.uniform(0.01, 1.0, int(opened.sum())), "OPEN"),
            rows(clicked, rng.uniform(0.02, 1.2, int(clicked.sum())), "CLICK"),
            rows(last_unsub, 0.05, "UNSUBSCRIBE"),
        ],
        ignore_index=True,
    )
    frame = frame.iloc[np.lexsort((frame["day"].to_numpy(), frame["customer_idx"].to_numpy()))].reset_index(drop=True)
    ts = _ts(hist_start, frame["day"].to_numpy())
    return pd.DataFrame(
        {
            "event_id": _ids("m", np.arange(len(frame)), 9),
            "customer_id": _ids("c", frame["customer_idx"].to_numpy(), 7),
            "event_type": frame["event_type"].to_numpy(dtype=object),
            "campaign_id": frame["campaign_id"].to_numpy(dtype=object),
            "flow": frame["flow"].to_numpy(dtype=object),
            "event_ts": ts,
            "loaded_at": ts,
        }
    )


def _support_tickets(rng, hist_start, n, tr, start_day, cancel_day, voluntary, total_days):
    stop = np.minimum(cancel_day, total_days)
    life_years = np.maximum(stop - start_day, 0.0) / 365.0
    idx_parts, day_parts, cat_parts, neg_parts = [], [], [], []

    def add(counts, cat, neg):
        i = np.repeat(np.arange(n), counts)
        d = rng.uniform(start_day[i], np.maximum(stop[i], start_day[i] + 1e-3))
        idx_parts.append(i)
        day_parts.append(np.minimum(d, total_days))
        cat_parts.append(cat(len(i), i))
        neg_parts.append(np.full(len(i), neg))

    add(rng.poisson(BASELINE_TICKETS_PER_YEAR * life_years),
        lambda m, i: rng.choice(TICKET_CATEGORIES, m, p=TICKET_BASELINE_P).astype(object), False)  # fmt: skip
    for arch, trait in (("price", tr.price_sensitivity), ("efficacy", tr.efficacy_gap),
                        ("fatigue", tr.fatigue), ("life_change", tr.life_event)):  # fmt: skip
        mask = tr.archetype == arch
        add(rng.poisson(np.where(mask, 0.4 * trait * life_years, 0.0)),
            lambda m, i, a=arch: np.full(m, ARCHETYPE_TICKET[a], dtype=object), True)  # fmt: skip

    # A burst of complaints shortly before many voluntary cancellations, category noisy around the archetype.
    pre = np.flatnonzero(voluntary & (cancel_day <= total_days) & (rng.random(n) < 0.35))
    pre_cat = np.array([ARCHETYPE_TICKET[a] for a in tr.archetype[pre]], dtype=object)
    noisy = rng.random(len(pre)) < 0.2
    pre_cat = np.where(noisy, rng.choice(TICKET_CATEGORIES, len(pre)), pre_cat)
    idx_parts.append(pre)
    day_parts.append(np.maximum(cancel_day[pre] - rng.uniform(0, 30, len(pre)), start_day[pre]))
    cat_parts.append(pre_cat)
    neg_parts.append(np.full(len(pre), True))

    idx = np.concatenate(idx_parts)
    day = np.concatenate(day_parts)
    cat = np.concatenate(cat_parts)
    neg = np.concatenate(neg_parts)
    o = np.lexsort((day, idx))
    idx, day, cat, neg = idx[o], day[o], cat[o], neg[o]
    m = len(idx)
    sentiment = np.clip(np.where(neg, rng.normal(-0.4, 0.3, m), rng.normal(0.1, 0.4, m)), -1, 1).round(3)
    resolved_day = day + rng.lognormal(np.log(20.0), 0.8, m) / 24.0
    resolved = resolved_day <= total_days
    created = _ts(hist_start, day)
    return pd.DataFrame(
        {
            "ticket_id": _ids("t", np.arange(m), 9),
            "customer_id": _ids("c", idx, 7),
            "category": cat,
            "created_at": created,
            "resolved_at": _ts(hist_start, resolved_day).where(resolved, pd.NaT),
            "sentiment": sentiment,
            "status": np.where(resolved, "RESOLVED", "OPEN").astype(object),
            "loaded_at": created,
        }
    )


def _quiz_responses(rng, hist_start, n, tr, start_day, cancel_day, has_quiz, total_days):
    taken = [np.flatnonzero(has_quiz)]
    when = [start_day[taken[0]] - rng.uniform(0.0, 0.1, len(taken[0]))]
    # Efficacy-dissatisfied customers sometimes retake the quiz looking for a better blend.
    retake_day = start_day + rng.uniform(30, 150, n)
    retake = has_quiz & (tr.archetype == "efficacy") & (rng.random(n) < 0.3) & (retake_day < np.minimum(cancel_day, total_days))
    taken.append(np.flatnonzero(retake))
    when.append(retake_day[retake])

    frames = []
    letters = np.array(list("ABCD"), dtype=object)
    for cust, day in zip(taken, when, strict=True):
        m = len(cust)
        answers = np.concatenate(
            [letters[rng.integers(0, 4, (m, QUIZ_QUESTIONS))], rng.choice(latent.SKUS, (m, 2)).astype(object)],
            axis=1,
        )
        q = len(QUIZ_KEYS)
        offset = np.tile(np.arange(q) * 20.0 / 86400.0, m)
        frames.append(
            (np.repeat(cust, q), np.repeat(day, q) + offset, np.tile(np.array(QUIZ_KEYS, dtype=object), m),
             answers.ravel())
        )
    cust = np.concatenate([f[0] for f in frames])
    day = np.concatenate([f[1] for f in frames])
    key = np.concatenate([f[2] for f in frames])
    ans = np.concatenate([f[3] for f in frames])
    o = np.lexsort((day, cust))
    answered = _ts(hist_start, day[o])
    return pd.DataFrame(
        {
            "response_id": _ids("q", np.arange(len(o)), 10),
            "customer_id": _ids("c", cust[o], 7),
            "question_key": key[o],
            "answer_value": ans[o],
            "answered_at": answered,
            "loaded_at": answered,
        }
    )


def _customers_pii(rng, n, started):
    first = rng.choice(FIRST_NAMES, n)
    last = rng.choice(LAST_NAMES, n)
    ids = np.arange(n)
    email = np.char.add(np.char.add(np.char.add(np.char.lower(first), "."), np.char.lower(last)), ids.astype(str))
    phone = np.char.add("+1555", rng.integers(1_000_000, 9_999_999, n).astype(str))
    postal = np.char.zfill(rng.integers(0, 99_999, n).astype(str), 5)
    return pd.DataFrame(
        {
            "customer_id": _ids("c", ids, 7),
            "first_name": first.astype(object),
            "last_name": last.astype(object),
            "email": np.char.add(email, "@example.com").astype(object),
            "phone": phone.astype(object),
            "postal_code": postal.astype(object),
            "loaded_at": started,
        }
    )


def _products(hist_start):
    skus = list(latent.SKUS)
    ts = _ts(hist_start, np.zeros(len(skus)))
    return pd.DataFrame(
        {
            "sku": skus,
            "product_name": [f"{s.title()} Blend" for s in skus],
            "goal_category": [GOALS[s] for s in skus],
            "price_usd": float(latent.PRICES[0]),
            "status": "ACTIVE",
            "loaded_at": ts,
        }
    )


# --------------------------------------------------------------------------- orchestration


def build_frames(settings: Settings, n: int) -> dict[str, pd.DataFrame]:
    cfg = settings.synth
    rng = np.random.default_rng(cfg.seed)
    end = datetime.combine(cfg.end_date, time(0))
    hist_start = end - timedelta(days=30 * cfg.history_months)
    total_days = float((end - hist_start).days)

    traits = latent.draw_traits(rng, n)
    has_quiz, has_email = edge_cases.coverage(rng, n)
    start_day = edge_cases.recent_starts(rng, rng.uniform(0, total_days - 14, n), total_days)
    interval = rng.choice(latent.INTERVALS, n)
    price = rng.choice(latent.PRICES, n)
    to_cancel, voluntary, reason = latent.draw_cancellation(rng, traits, start_day)
    cancel_day = start_day + to_cancel
    cancelled = cancel_day <= total_days

    status = rng.choice(["ACTIVE", "PAUSED", "PAST_DUE"], n, p=[0.92, 0.05, 0.03])
    status = np.where(cancelled, "CANCELLED", status)
    cancel_type = np.where(cancelled, np.where(voluntary, "voluntary", "involuntary"), None)
    reason_code = np.where(cancelled, reason, None)

    ids = np.arange(n)
    started = _ts(hist_start, start_day)
    subs = pd.DataFrame(
        {
            "customer_id": _ids("c", ids, 7),
            "subscription_id": _ids("s", ids, 7),
            "status": status,
            "started_at": started,
            "cancelled_at": _ts(hist_start, cancel_day).where(cancelled, pd.NaT),
            "cancel_type": cancel_type,
            "cancel_reason_code": reason_code,
            "price_usd": price,
            "interval_days": interval.astype("int64"),
            "loaded_at": started,
        }
    )

    idx, day = latent.order_times(rng, start_day, interval, cancel_day, voluntary, total_days + 1)
    keep = edge_cases.keep_orders(idx, edge_cases.single_order_mask(rng, n))
    idx, day = idx[keep], day[keep]
    m = len(idx)
    amount = price[idx]
    discounted = rng.random(m) < 0.05 + 0.7 * traits.price_sensitivity[idx]
    discount = np.where(discounted, amount * rng.uniform(0.1, 0.3, m), 0.0).round(2)
    refunded = rng.random(m) < 0.01 + 0.15 * traits.fatigue[idx]
    refund_day = day + 5
    refunded &= refund_day <= total_days
    ordered_at = _ts(hist_start, day)
    blend = rng.choice(latent.SKUS, m)
    orders = pd.DataFrame(
        {
            "order_id": _ids("o", np.arange(m), 9),
            "customer_id": _ids("c", idx, 7),
            "ordered_at": ordered_at,
            "blend_sku": blend.astype(object),
            "amount_usd": amount,
            "discount_usd": discount,
            "refund_usd": np.where(refunded, amount - discount, 0.0).round(2),
            "refunded_at": _ts(hist_start, refund_day).where(refunded, pd.NaT),
            "loaded_at": ordered_at,
        }
    )

    frames = {
        "subscriptions": subs,
        "orders": orders,
        "subscription_events": _subscription_events(
            rng, hist_start, n, traits, start_day, cancel_day, cancelled, voluntary, reason, status,
            total_days, idx, day, blend, discounted, discount,
        ),
        "products": _products(hist_start),
        "email_events": _email_events(rng, hist_start, n, traits, start_day, cancel_day, voluntary, has_email, total_days),
        "support_tickets": _support_tickets(rng, hist_start, n, traits, start_day, cancel_day, voluntary, total_days),
        "quiz_responses": _quiz_responses(rng, hist_start, n, traits, start_day, cancel_day, has_quiz, total_days),
        "customers_pii": _customers_pii(rng, n, started),
    }

    edge_cases.apply_nulls(rng, frames)
    edge_cases.add_duplicates(rng, frames)
    edge_cases.late_arrivals(rng, frames)
    edge_cases.tz_offsets(rng, frames)

    # Ground truth for evaluation only; not part of the source contract.
    frames["latent_truth"] = pd.DataFrame(
        {
            "customer_id": _ids("c", ids, 7),
            "archetype": traits.archetype.astype(object),
            "price_sensitivity": traits.price_sensitivity,
            "efficacy_gap": traits.efficacy_gap,
            "fatigue": traits.fatigue,
            "life_event": traits.life_event,
            "hazard_per_day": traits.hazard_per_day,
        }
    )
    return frames


def generate(wh: Warehouse, settings: Settings, scale: str = "tiny") -> dict[str, int]:
    """Replace the contents of every THESIS_SYNTH source table with a fresh synthetic population."""
    try:
        n = settings.synth.scales[scale]
    except KeyError:
        raise ValueError(f"unknown scale {scale!r}; choose one of {sorted(settings.synth.scales)}") from None
    frames = build_frames(settings, n)
    schema = settings.schemas.synth
    with wh.transaction():
        wh.execute(";".join(f"DELETE FROM {schema}.{name}" for name in frames))
        for name, df in frames.items():
            for lo in range(0, len(df), CHUNK):
                wh.insert_frame(f"{schema}.{name}", df.iloc[lo : lo + CHUNK])
    return {name: len(df) for name, df in frames.items()}
