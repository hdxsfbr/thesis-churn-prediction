"""Data profile of the synthetic source (R1.2 AC): row counts, edge-case shares, churn-event timeline.

Reads only through the source contract and writes ``docs/data-profile.md`` plus ``CHURN_OPS.DATA_PROFILE``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd

from churn.contract.loader import Contract, contract_for
from churn.settings import Settings
from churn.synth.edge_cases import EDGE_CATEGORIES
from churn.warehouse.base import Warehouse

HOLDOUT_DAYS = 90  # the 3-month evaluation holdout that follows the label horizon

# Views whose rows can be duplicated / arrive late, with their event-time column.
ROW_LEVEL_TS = {
    "subscriptions": "started_at",
    "orders": "ordered_at",
    "subscription_events": "event_ts",
    "email_events": "event_ts",
    "support_tickets": "created_at",
    "quiz_responses": "answered_at",
}
DEDUP_VIEWS = tuple(ROW_LEVEL_TS)

DESCRIPTIONS = {
    "no_quiz": "subscribers with no quiz response at all",
    "no_email": "subscribers with no email event at all",
    "no_tickets": "subscribers who never contacted support",
    "single_order": "subscribers with exactly one order",
    "tenure_lt_30d": "subscribers whose subscription lasted (or has lasted) under 30 days",
    "null_values": "subscribers with a null in a nullable column (blend_sku, sentiment, phone, campaign_id, cancel reason)",
    "duplicates": "event rows that repeat another row's key (share of rows)",
    "late_arriving": "rows loaded more than 1 day after their event timestamp (share of rows)",
    "tz_offsets": "email/support rows stamped in local time with a non-zero offset (share of rows)",
}


@dataclass
class EdgeShare:
    count: int
    total: int

    @property
    def share(self) -> float:
        return self.count / self.total if self.total else 0.0


@dataclass
class DataProfile:
    end_date: datetime
    subscribers: int
    view_rows: dict[str, int]
    edge_cases: dict[str, EdgeShare]
    timeline: pd.DataFrame  # month, voluntary, involuntary
    horizon_days: int
    voluntary_first: pd.Timestamp | None = None
    voluntary_last: pd.Timestamp | None = None
    voluntary_in_final_window: int = 0
    voluntary_before_final_window: int = 0
    involuntary_total: int = 0
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def voluntary_span_days(self) -> int:
        if self.voluntary_first is None or self.voluntary_last is None:
            return 0
        return (self.voluntary_last - self.voluntary_first).days

    @property
    def final_window_days(self) -> int:
        return self.horizon_days + HOLDOUT_DAYS


class _Reader:
    def __init__(self, wh: Warehouse, contract: Contract):
        self.wh, self.c = wh, contract

    def table(self, view: str) -> str:
        return self.c.physical(view)

    def col(self, view: str, logical: str) -> str:
        return self.c.views[view].columns[logical].source

    def loaded(self, view: str) -> str:
        return self.c.views[view].loaded_at

    def n(self, sql: str, params: dict | None = None) -> int:
        return int(self.wh.query(sql, params).iloc[0, 0])


def _customers_without(r: _Reader, view: str) -> int:
    subs, cid = r.table("subscriptions"), r.col("subscriptions", "customer_id")
    other, ocid = r.table(view), r.col(view, "customer_id")
    return r.n(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {cid} AS k FROM {subs}) s "
        f"LEFT JOIN (SELECT DISTINCT {ocid} AS k FROM {other}) o ON o.k = s.k WHERE o.k IS NULL"
    )


def _edge_cases(r: _Reader, settings: Settings, subscribers: int) -> dict[str, EdgeShare]:
    out: dict[str, EdgeShare] = {}
    out["no_quiz"] = EdgeShare(_customers_without(r, "quiz_responses"), subscribers)
    out["no_email"] = EdgeShare(_customers_without(r, "email_events"), subscribers)
    out["no_tickets"] = EdgeShare(_customers_without(r, "support_tickets"), subscribers)

    o, ocid, oid = r.table("orders"), r.col("orders", "customer_id"), r.col("orders", "order_id")
    out["single_order"] = EdgeShare(
        r.n(f"SELECT COUNT(*) FROM (SELECT {ocid} FROM {o} GROUP BY {ocid} HAVING COUNT(DISTINCT {oid}) = 1)"),
        subscribers,
    )

    s = r.table("subscriptions")
    cid, started, cancelled = (r.col("subscriptions", c) for c in ("customer_id", "started_at", "cancelled_at"))
    end = datetime.combine(settings.synth.end_date, time(0))
    out["tenure_lt_30d"] = EdgeShare(
        r.n(
            f"SELECT COUNT(DISTINCT {cid}) FROM {s} "
            f"WHERE DATEDIFF(day, {started}, COALESCE({cancelled}, CAST(:end AS TIMESTAMP))) < 30",
            {"end": end},
        ),
        subscribers,
    )

    checks = [
        ("orders", "blend_sku"),
        ("support_tickets", "sentiment"),
        ("customers_pii", "phone"),
        ("email_events", "campaign_id"),
    ]
    selects = [
        f"SELECT {r.col(v, 'customer_id')} AS k FROM {r.table(v)} WHERE {r.col(v, c)} IS NULL" for v, c in checks
    ]
    ctype, creason = r.col("subscriptions", "cancel_type"), r.col("subscriptions", "cancel_reason_code")
    selects.append(f"SELECT {cid} AS k FROM {s} WHERE {ctype} = 'voluntary' AND {creason} IS NULL")
    out["null_values"] = EdgeShare(
        r.n(f"SELECT COUNT(DISTINCT k) FROM ({' UNION ALL '.join(selects)})"), subscribers
    )

    dup = rows = late = 0
    for v, ts in ROW_LEVEL_TS.items():
        t, key = r.table(v), r.c.views[v].dedup_key
        total, distinct = r.wh.query(f"SELECT COUNT(*) AS a, COUNT(DISTINCT {key}) AS b FROM {t}").iloc[0]
        dup += int(total) - int(distinct)
        rows += int(total)
        late += r.n(f"SELECT COUNT(*) FROM {t} WHERE {r.loaded(v)} > DATEADD(day, 1, {r.col(v, ts)})")
    out["duplicates"] = EdgeShare(dup, rows)
    out["late_arriving"] = EdgeShare(late, rows)

    tz = tz_rows = 0
    for v in ("email_events", "support_tickets"):
        col = r.col(v, "tz_offset_min")
        tz_rows += r.n(f"SELECT COUNT(*) FROM {r.table(v)}")
        tz += r.n(f"SELECT COUNT(*) FROM {r.table(v)} WHERE {col} <> 0")
    out["tz_offsets"] = EdgeShare(tz, tz_rows)

    out["no_tickets"].total = subscribers
    return {name: out[name] for name in EDGE_CATEGORIES}


def build_profile(wh: Warehouse, settings: Settings, contract: Contract | None = None) -> DataProfile:
    contract = contract or contract_for(settings)
    r = _Reader(wh, contract)
    end = datetime.combine(settings.synth.end_date, time(0))
    horizon = settings.label.horizon_days

    view_rows = {v: r.n(f"SELECT COUNT(*) FROM {contract.physical(v)}") for v in contract.views}
    s = contract.physical("subscriptions")
    cid = r.col("subscriptions", "customer_id")
    subscribers = r.n(f"SELECT COUNT(DISTINCT {cid}) FROM {s}")
    edge = _edge_cases(r, settings, subscribers)

    sid = r.col("subscriptions", "subscription_id")
    cancelled, ctype = r.col("subscriptions", "cancelled_at"), r.col("subscriptions", "cancel_type")
    tl = wh.query(
        f"SELECT DATE_TRUNC('month', {cancelled}) AS month, {ctype} AS cancel_type, "
        f"COUNT(DISTINCT {sid}) AS n FROM {s} WHERE {cancelled} IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2"
    )
    if tl.empty:
        timeline = pd.DataFrame({"month": [], "voluntary": [], "involuntary": []})
    else:
        timeline = (
            tl.pivot_table(index="month", columns="cancel_type", values="n", aggfunc="sum", fill_value=0)
            .reindex(columns=["voluntary", "involuntary"], fill_value=0)
            .reset_index()
        )

    window_start = end - timedelta(days=horizon + HOLDOUT_DAYS)
    vol = f"{ctype} = 'voluntary' AND {cancelled} IS NOT NULL"
    span = wh.query(f"SELECT MIN({cancelled}) AS lo, MAX({cancelled}) AS hi FROM {s} WHERE {vol}").iloc[0]
    p = DataProfile(
        end_date=end,
        subscribers=subscribers,
        view_rows=view_rows,
        edge_cases=edge,
        timeline=timeline,
        horizon_days=horizon,
        voluntary_first=None if pd.isna(span["lo"]) else pd.Timestamp(span["lo"]),
        voluntary_last=None if pd.isna(span["hi"]) else pd.Timestamp(span["hi"]),
        voluntary_in_final_window=r.n(
            f"SELECT COUNT(DISTINCT {sid}) FROM {s} WHERE {vol} AND {cancelled} >= CAST(:w AS TIMESTAMP)",
            {"w": window_start},
        ),
        voluntary_before_final_window=r.n(
            f"SELECT COUNT(DISTINCT {sid}) FROM {s} WHERE {vol} AND {cancelled} < CAST(:w AS TIMESTAMP)",
            {"w": window_start},
        ),
        involuntary_total=r.n(f"SELECT COUNT(DISTINCT {sid}) FROM {s} WHERE {ctype} = 'involuntary'"),
    )
    return p


def render_markdown(p: DataProfile) -> str:
    lines = [
        "# Synthetic data profile",
        "",
        "Generated by `churn synth --profile` from the synthetic source (`THESIS_SYNTH`) through the source",
        "contract. Synthetic accuracy is not real-world accuracy: this report only shows that the source",
        "covers every domain and edge case the pipeline must survive.",
        "",
        f"- History ends: {p.end_date:%Y-%m-%d}",
        f"- Subscribers: {p.subscribers:,}",
        "",
        "## Row counts per contract view",
        "",
        "| View | Rows |",
        "| --- | ---: |",
    ]
    lines += [f"| {v} | {n:,} |" for v, n in p.view_rows.items()]
    lines += [
        "",
        "## Edge-case shares",
        "",
        "| Category | Count | Of | Share | Meaning |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for name, e in p.edge_cases.items():
        lines.append(f"| {name} | {e.count:,} | {e.total:,} | {e.share:.2%} | {DESCRIPTIONS[name]} |")
    lines += [
        "",
        "## Churn events",
        "",
        f"- Voluntary cancellations: {p.voluntary_first:%Y-%m-%d} to {p.voluntary_last:%Y-%m-%d} "
        f"({p.voluntary_span_days} days)"
        if p.voluntary_first is not None
        else "- Voluntary cancellations: none",
        (
            f"- Voluntary cancellations in the final {p.final_window_days} days "
            f"(label horizon {p.horizon_days} d + {HOLDOUT_DAYS} d holdout): {p.voluntary_in_final_window:,}"
        ),
        f"- Voluntary cancellations before that window (training history): {p.voluntary_before_final_window:,}",
        f"- Involuntary cancellations (payment failure): {p.involuntary_total:,}",
        "",
        "| Month | Voluntary | Involuntary |",
        "| --- | ---: | ---: |",
    ]
    for _, row in p.timeline.iterrows():
        lines.append(f"| {pd.Timestamp(row['month']):%Y-%m} | {int(row['voluntary']):,} | {int(row['involuntary']):,} |")
    return "\n".join(lines) + "\n"


def profile_frame(p: DataProfile, generated_at: datetime) -> pd.DataFrame:
    rows = [("view_rows", v, n, None, None, None) for v, n in p.view_rows.items()]
    rows += [("edge_case", k, e.count, e.total, e.share, DESCRIPTIONS[k]) for k, e in p.edge_cases.items()]
    rows += [
        ("churn_timeline", f"{pd.Timestamp(r['month']):%Y-%m}", int(r["voluntary"]), None, None,
         f"involuntary={int(r['involuntary'])}")
        for _, r in p.timeline.iterrows()
    ]
    rows += [
        ("churn_window", "voluntary_final_window", p.voluntary_in_final_window, None, None,
         f"last {p.final_window_days} days"),
        ("churn_window", "voluntary_before_final_window", p.voluntary_before_final_window, None, None, None),
        ("churn_window", "voluntary_span_days", p.voluntary_span_days, None, None, None),
    ]
    df = pd.DataFrame(rows, columns=["section", "name", "rows", "total", "share", "detail"])
    df["rows"] = df["rows"].astype("int64")
    df["total"] = df["total"].astype("Int64").astype(object).where(df["total"].notna(), None)
    df["share"] = df["share"].astype(float)
    df["detail"] = df["detail"].astype(object).where(df["detail"].notna(), None)
    df["generated_at"] = pd.Timestamp(generated_at).as_unit("us")
    return df


def write_profile(
    wh: Warehouse, settings: Settings, p: DataProfile | None = None, doc_path: Path | str | None = None
) -> DataProfile:
    """Write the markdown report and replace the contents of CHURN_OPS.DATA_PROFILE."""
    p = p or build_profile(wh, settings)
    path = Path(doc_path) if doc_path else settings.resolve("docs/data-profile.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(p))
    table = f"{settings.schemas.ops}.DATA_PROFILE"
    frame = profile_frame(p, datetime.now())
    with wh.transaction():
        wh.execute(f"DELETE FROM {table}")
        wh.merge_frame(table, frame, keys=["section", "name"])
    return p
