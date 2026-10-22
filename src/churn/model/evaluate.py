"""Honest model evaluation (R3.6, Revision 2): time splits, A1 label, PASS / MISS-with-reason per target.

Monthly snapshots are split by date into training, validation and holdout (A1, with a purge so no label window
crosses into the next split). An evaluation-only model is fitted on training (never registered). The at-risk
boundary is chosen on the validation split alone (D7) and is only a recommendation (D8): the runtime tiers and
``AGENT_CONFIG`` are never touched. Pre-cancellation recall uses, for each churner, the last holdout score made
before the cancellation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from churn.contract.loader import contract_for
from churn.contract.views import contract_ctes, key_map_frame
from churn.features.registry import FEATURE_COLUMNS
from churn.model.archetype import ARCHETYPES, predict_archetype
from churn.model.dataset import build_dataset
from churn.model.registry import ModelError
from churn.model.train import fit, fit_archetypes, weak_labelled_churners
from churn.settings import Settings, project_root
from churn.synth.generator import latent_archetypes
from churn.warehouse.base import Warehouse

HOLDOUT_MONTHS = 3  # A1: the last 3 snapshot months
VALIDATION_MONTHS = 3  # A1: the 3 snapshot months before the holdout
TOP_SHARE = 0.02
DECILE = 0.10
BOUNDARY_MIN_RECALL = 0.65  # D7
MIN_VALIDATION_CHURNERS = 20
MIN_STABLE_POSITIVES = 30
MIN_TOP_ROWS = 50  # fewer rows than this in the top 2% makes precision noisy
MIN_TRUTH_COVERAGE = 0.5  # below this share the synthetic scale clearly does not match the warehouse
# R3.6 targets: name -> threshold. Precision and recall must be strictly above, the lift at least.
TARGETS = {"top_share_precision": 0.50, "top_decile_lift": 4.0, "pre_cancellation_recall": 0.60}
TARGET_OPS = {"top_share_precision": ">", "top_decile_lift": ">=", "pre_cancellation_recall": ">"}


# --- splits ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Splits:
    train: list
    validation: list
    holdout: list
    purged: list


def split_snapshots(
    dates,
    holdout_months: int = HOLDOUT_MONTHS,
    validation_months: int = VALIDATION_MONTHS,
    horizon_days: int = 30,
) -> Splits:
    """Time-ordered training / validation / holdout split of snapshot dates (A1).

    A snapshot is purged from its split when its label window (date, date + horizon] reaches the first snapshot of
    the next split, so no training or validation label looks into the period that is judged after it.
    """
    ordered = sorted(dates)
    n_hold, n_val = holdout_months, validation_months
    holdout = ordered[len(ordered) - n_hold :]
    val_raw = ordered[max(0, len(ordered) - n_hold - n_val) : len(ordered) - n_hold]
    train_raw = ordered[: max(0, len(ordered) - n_hold - n_val)]
    horizon = pd.Timedelta(days=horizon_days)

    def purge(split, nxt):
        if not split or not nxt:
            return list(split), []
        first = pd.Timestamp(nxt[0])
        keep = [d for d in split if pd.Timestamp(d) + horizon < first]
        return keep, [d for d in split if d not in keep]

    validation, dropped_val = purge(val_raw, holdout)
    train, dropped_train = purge(train_raw, val_raw)
    return Splits(train, validation, holdout, dropped_train + dropped_val)


# --- metrics -----------------------------------------------------------------------------------------------


def _order(p: np.ndarray, keys=None, dates=None) -> np.ndarray:
    """Row order by score descending, ties by subscriber_key then snapshot_date (deterministic)."""
    n = len(p)
    k = np.asarray(keys).astype(str) if keys is not None else np.zeros(n, dtype=str)
    d = pd.to_datetime(pd.Series(dates)).astype("int64").to_numpy() if dates is not None else np.zeros(n, dtype=int)
    return np.lexsort((d, k, -p))


def top_share_stats(y_true, probs, share: float = TOP_SHARE, keys=None, dates=None) -> tuple[float, int, int]:
    """(precision, churners, rows) among the ceil(share * n) highest-scored rows; precision NaN when empty."""
    y = np.asarray(y_true)
    p = np.asarray(probs, dtype=float)
    if len(y) == 0:
        return float("nan"), 0, 0
    k = max(1, math.ceil(round(len(y) * share, 9)))
    top = _order(p, keys, dates)[:k]
    hits = int(y[top].sum())
    return hits / k, hits, k


def top_share_precision(y_true, probs, share: float = TOP_SHARE, keys=None, dates=None) -> float:
    """Share of churners among the top ``share`` of rows by score (R3.6 target 1 uses 2%)."""
    return top_share_stats(y_true, probs, share, keys, dates)[0]


def top_decile_precision(y_true, probs, keys=None, dates=None) -> float:
    """Share of churners among the ceil(10%) highest-scored rows (NaN for an empty frame)."""
    return top_share_precision(y_true, probs, DECILE, keys, dates)


def top_decile_lift(y_true, probs, keys=None, dates=None) -> float:
    """Top-decile precision divided by the base rate (NaN when there are no churners)."""
    y = np.asarray(y_true)
    if len(y) == 0 or y.sum() == 0:
        return float("nan")
    return top_decile_precision(y, probs, keys, dates) / float(y.mean())


def ceilings(base_rate: float) -> dict:
    """What a perfect model could reach at the measured base rate b (A1 ceiling arithmetic)."""
    b = base_rate
    if b is None or math.isnan(b) or b <= 0:
        nan = float("nan")
        return {"top_decile_precision": nan, "top_decile_lift": nan, "top_share_precision": nan}
    return {
        "top_decile_precision": min(1.0, b / DECILE),
        "top_decile_lift": min(1 / DECILE, 1 / b),
        "top_share_precision": min(1.0, b / TOP_SHARE),
    }


@dataclass(frozen=True)
class Boundary:
    threshold: float | None
    validation_recall: float | None
    n_churners: int
    reason: str | None = None


def choose_boundary(validation: pd.DataFrame) -> Boundary:
    """The highest score threshold whose validation recall is >= 0.65 (D7); takes validation data alone.

    ``validation``: subscriber_key, snapshot_date, label, churn_prob. Each churner counts once, through its latest
    positive snapshot. No boundary when there are fewer than 20 churners (the code never falls back to anything).
    """
    pos = validation[validation["label"] == 1]
    last = pos.sort_values(["snapshot_date", "subscriber_key"]).groupby("subscriber_key").tail(1)
    k = len(last)
    if k == 0:
        return Boundary(None, None, 0, "the validation split has no scored churner")
    if k < MIN_VALIDATION_CHURNERS:
        return Boundary(None, None, k, f"the validation split has fewer than {MIN_VALIDATION_CHURNERS} churners ({k})")
    scores = np.sort(last["churn_prob"].to_numpy(dtype=float))[::-1]
    t = float(scores[math.ceil(round(BOUNDARY_MIN_RECALL * k, 9)) - 1])
    return Boundary(t, float((scores >= t).sum() / k), k)


def pre_cancellation_breakdown(scores: pd.DataFrame, cancels: pd.DataFrame, threshold: float) -> dict:
    """Churners split into caught, scored but below the boundary, and never scored before cancelling."""
    cancels = cancels.drop_duplicates("subscriber_key")
    total = len(cancels)
    if total == 0:
        return {"total": 0, "caught": 0, "below_boundary": 0, "no_score": 0, "threshold": threshold}
    merged = cancels.merge(scores, on="subscriber_key", how="left")
    merged = merged[merged["as_of"].notna() & (merged["as_of"] < merged["cancelled_at"])]
    last = merged.sort_values("as_of").groupby("subscriber_key").tail(1)
    caught = int((last["churn_prob"] >= threshold).sum())
    return {
        "total": total,
        "caught": caught,
        "below_boundary": len(last) - caught,
        "no_score": total - len(last),
        "threshold": threshold,
    }


def pre_cancellation_recall(scores: pd.DataFrame, cancels: pd.DataFrame, threshold: float) -> float:
    """Share of churners whose last score before cancelling was >= threshold.

    ``scores``: subscriber_key, as_of, churn_prob. ``cancels``: subscriber_key, cancelled_at. A churner with no
    score before the cancellation counts as missed. NaN when there are no churners.
    """
    b = pre_cancellation_breakdown(scores, cancels, threshold)
    return b["caught"] / b["total"] if b["total"] else float("nan")


def calibration_table(y_true, probs, bins: int = 10) -> pd.DataFrame:
    """Equal-width probability bins: mean predicted vs observed churn rate."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(probs, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, bins - 1)
    rows = []
    for b in range(bins):
        sel = idx == b
        n = int(sel.sum())
        rows.append(
            {
                "bin_low": float(edges[b]),
                "bin_high": float(edges[b + 1]),
                "mean_predicted": float(p[sel].mean()) if n else float("nan"),
                "observed_rate": float(y[sel].mean()) if n else float("nan"),
                "n": n,
            }
        )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class TargetResult:
    name: str
    requirement: str
    target: float
    value: float
    status: str  # PASS | MISS
    reason: str | None
    op: str = ">"


def _fmt(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.2f}"


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def check_targets(
    top_share_precision: float,
    top_decile_lift: float,
    recall: float,
    base_rate: float,
    n_positives: int,
    n_rows: int,
    recall_breakdown: dict | None = None,
    boundary: Boundary | None = None,
    top_share_hits: int | None = None,
    top_share_rows: int | None = None,
) -> list[TargetResult]:
    """R3.6: precision and recall must be strictly above target, lift at least the target; every miss says why."""
    ceil = ceilings(base_rate)
    values = {
        "top_share_precision": top_share_precision,
        "top_decile_lift": top_decile_lift,
        "pre_cancellation_recall": recall,
    }
    labels = {
        "top_share_precision": "top-2% precision",
        "top_decile_lift": "top-decile lift",
        "pre_cancellation_recall": "pre-cancellation recall",
    }
    out = []
    for name, target in TARGETS.items():
        value, op = values[name], TARGET_OPS[name]
        unit = "x" if name == "top_decile_lift" else ""
        reason = None
        if name == "pre_cancellation_recall" and boundary is not None and boundary.threshold is None:
            reason = f"no at-risk boundary could be chosen: {boundary.reason}; there is no fallback to the holdout"
        elif _isnan(value):
            reason = "no churners in the holdout, so the metric is undefined"
        elif not (value > target if op == ">" else value >= target):
            word = "above" if op == ">" else "at least"
            reason = f"{labels[name]} {_fmt(value)}{unit} is not {word} the target {_fmt(target)}{unit}"
            context = [f"holdout has {n_positives} churn labels in {n_rows} scored rows (base rate {base_rate:.1%})"]
            if name == "top_share_precision":
                if top_share_rows:
                    context.append(f"{top_share_hits} of {top_share_rows} top-2% rows are churners")
                if ceil[name] <= target:
                    context.append(f"unreachable at base rate {base_rate:.1%}: the ceiling is {_fmt(ceil[name])}")
                if top_share_rows and top_share_rows < MIN_TOP_ROWS:
                    context.append(f"only {top_share_rows} rows in the top 2%, so the estimate is noisy")
            if name == "top_decile_lift" and ceil[name] < target:
                context.append(f"unreachable at base rate {base_rate:.1%}: the ceiling is {_fmt(ceil[name])}x")
            if name == "pre_cancellation_recall" and recall_breakdown and recall_breakdown.get("total"):
                b = recall_breakdown
                context.append(
                    f"of {b['total']} cancelling churners, {b['caught']} were caught, {b['no_score']} had no score "
                    f"before cancelling, and {b['below_boundary']} had a last score below the at-risk boundary "
                    f"({b['threshold']:.2f})"
                )
            if n_positives < MIN_STABLE_POSITIVES:
                context.append(f"fewer than {MIN_STABLE_POSITIVES} churners, so the estimate is noisy")
            reason += "; " + "; ".join(context)
        out.append(TargetResult(name, "R3.6", target, value, "MISS" if reason else "PASS", reason, op))
    return out


def archetype_report(truth: pd.Series, predicted: pd.Series) -> tuple[dict[str, dict], pd.DataFrame]:
    """Per-archetype precision, recall and support, and the confusion matrix (rows truth, columns predicted)."""
    metrics = {}
    for a in ARCHETYPES:
        tp = int(((truth == a) & (predicted == a)).sum())
        called = int((predicted == a).sum())
        actual = int((truth == a).sum())
        metrics[a] = {
            "precision": tp / called if called else None,
            "recall": tp / actual if actual else None,
            "support": actual,
            "predicted": called,
        }
    matrix = pd.crosstab(
        pd.Categorical(truth, categories=list(ARCHETYPES)),
        pd.Categorical(predicted, categories=list(ARCHETYPES)),
        dropna=False,
    )
    matrix.index.name, matrix.columns.name = "truth", "predicted"
    return metrics, matrix


# --- evaluation run ----------------------------------------------------------------------------------------


@dataclass
class EvalResult:
    report_id: str
    model_version: str
    holdout_start: datetime
    holdout_end: datetime
    n_rows: int
    n_positives: int
    metrics: dict
    targets: list[TargetResult]
    archetype_metrics: dict
    confusion: pd.DataFrame | None
    calibration: pd.DataFrame
    report_md: str = ""
    model_card_md: str = ""
    notes: list[str] = field(default_factory=list)
    validation_start: datetime | None = None
    validation_end: datetime | None = None
    train_start: datetime | None = None
    train_end: datetime | None = None
    scale: str | None = None


def _promoted_version(wh: Warehouse, settings: Settings) -> str:
    df = wh.query(
        f"SELECT model_version FROM {settings.schemas.ops}.MODEL_REGISTRY WHERE status = 'promoted' "
        "ORDER BY created_at DESC"
    )
    return str(df.iloc[0]["model_version"]) if len(df) else "unregistered"


def _cancellations(wh: Warehouse, settings: Settings, contract, lo: datetime, hi: datetime) -> pd.DataFrame:
    sql = (
        f"WITH {contract_ctes(contract, settings.key_pepper, ['subscriptions'])} "
        "SELECT subscriber_key, cancelled_at FROM subscriptions "
        "WHERE cancel_type = 'voluntary' AND cancelled_at >= CAST(:lo AS TIMESTAMP) "
        "AND cancelled_at < DATEADD(day, :horizon, CAST(:hi AS TIMESTAMP))"
    )
    df = wh.query(sql, {"lo": lo, "hi": hi, "horizon": settings.label.horizon_days})
    df["cancelled_at"] = pd.to_datetime(df["cancelled_at"])
    return df


def _json_safe(obj):
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def evaluate(
    wh: Warehouse,
    settings: Settings,
    docs_dir: Path | str | None = None,
    scale: str | None = None,
    train_end: date | None = None,
    holdout_months: int = HOLDOUT_MONTHS,
    validation_months: int = VALIDATION_MONTHS,
) -> EvalResult:
    """Fit on training, choose the boundary on validation, score the holdout, check the R3.6 targets and write EVAL_REPORTS and the two docs.

    ``scale`` names the synthetic population whose latent archetypes judge the archetype classifier; without it
    the archetype section is skipped.
    """
    if scale is not None and scale not in settings.synth.scales:
        raise ModelError(f"unknown scale {scale!r}; choose one of {sorted(settings.synth.scales)}")
    contract = contract_for(settings)
    train_end = train_end or settings.synth.end_date
    df = build_dataset(wh, settings, contract, train_end)
    dates = sorted(df["snapshot_date"].unique()) if not df.empty else []
    splits = split_snapshots(dates, holdout_months, validation_months, settings.label.horizon_days)
    if not splits.train or not splits.validation:
        raise ModelError(
            f"need training, {validation_months}-month validation and {holdout_months}-month holdout snapshots "
            f"(after purging windows that cross a split); found {len(dates)} snapshots"
        )
    train_df = df[df["snapshot_date"].isin(set(splits.train))]
    val = df[df["snapshot_date"].isin(set(splits.validation))].reset_index(drop=True)
    hold = df[df["snapshot_date"].isin(set(splits.holdout))].reset_index(drop=True)

    model, _ = fit(train_df, settings)
    val_scored = val[["subscriber_key", "snapshot_date", "label"]].assign(
        churn_prob=model.predict_proba(val[FEATURE_COLUMNS])[:, 1]
    )
    boundary = choose_boundary(val_scored)

    probs = model.predict_proba(hold[FEATURE_COLUMNS])[:, 1]
    y = hold["label"].to_numpy()
    n_pos = int(y.sum())
    keys, snap = hold["subscriber_key"], hold["snapshot_date"]
    holdout_start, holdout_end = pd.Timestamp(min(splits.holdout)).to_pydatetime(), pd.Timestamp(max(splits.holdout)).to_pydatetime()
    ts = lambda d: pd.Timestamp(d).to_pydatetime()

    scores = pd.DataFrame({"subscriber_key": keys, "as_of": pd.to_datetime(snap), "churn_prob": probs})
    cancels = _cancellations(wh, settings, contract, holdout_start, holdout_end)
    cancels = cancels[cancels["cancelled_at"] > pd.Timestamp(holdout_start)]

    base_rate = float(y.mean()) if len(y) else float("nan")
    ts_prec, ts_hits, ts_rows = top_share_stats(y, probs, TOP_SHARE, keys, snap)
    td_prec, td_hits, td_rows = top_share_stats(y, probs, DECILE, keys, snap)
    lift = top_decile_lift(y, probs, keys, snap)
    if boundary.threshold is None:
        breakdown = {"total": int(cancels["subscriber_key"].nunique()), "threshold": None}
        recall = float("nan")
        at_boundary = {"threshold": None, "flagged": 0, "true_positives": 0, "precision": float("nan")}
    else:
        breakdown = pre_cancellation_breakdown(scores, cancels, boundary.threshold)
        recall = pre_cancellation_recall(scores, cancels, boundary.threshold)
        flagged = probs >= boundary.threshold
        at_boundary = {
            "threshold": boundary.threshold,
            "flagged": int(flagged.sum()),
            "true_positives": int(y[flagged].sum()),
            "precision": float(y[flagged].mean()) if flagged.any() else float("nan"),
        }
    both = n_pos > 0 and n_pos < len(y)
    metrics = {
        "base_rate": base_rate,
        "top_share_precision": ts_prec,
        "top_share_hits": ts_hits,
        "top_share_rows": ts_rows,
        "top_decile_precision": td_prec,
        "top_decile_hits": td_hits,
        "top_decile_rows": td_rows,
        "top_decile_lift": lift,
        "pre_cancellation_recall": recall,
        "recall_breakdown": breakdown,
        "boundary": {
            "threshold": boundary.threshold,
            "validation_recall": boundary.validation_recall,
            "validation_churners": boundary.n_churners,
            "reason": boundary.reason,
            "recommended_only": True,
        },
        "precision_at_boundary": at_boundary,
        "ceilings": ceilings(base_rate),
        "roc_auc": float(roc_auc_score(y, probs)) if both else float("nan"),
        "pr_auc": float(average_precision_score(y, probs)) if n_pos else float("nan"),
        "brier": float(brier_score_loss(y, probs)),
        "n_churners_cancelling": int(cancels["subscriber_key"].nunique()),
        "splits": {
            "scale": scale,
            "train_rows": len(train_df),
            "validation_rows": len(val),
            "validation_positives": int(val["label"].sum()),
            "holdout_rows": len(y),
            "train_snapshots": len(splits.train),
            "validation_snapshots": len(splits.validation),
            "holdout_snapshots": len(splits.holdout),
            "purged_snapshots": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in splits.purged],
        },
    }
    calibration = calibration_table(y, probs)
    filled = calibration[calibration["n"] > 0]
    metrics["expected_calibration_error"] = float(
        (filled["n"] * (filled["mean_predicted"] - filled["observed_rate"]).abs()).sum() / max(1, filled["n"].sum())
    )
    targets = check_targets(
        ts_prec, lift, recall, base_rate, n_pos, len(y), breakdown, boundary=boundary,
        top_share_hits=ts_hits, top_share_rows=ts_rows,
    )

    notes: list[str] = []
    archetype_metrics: dict = {a: {"precision": None, "recall": None, "support": 0, "predicted": 0} for a in ARCHETYPES}
    confusion = None
    if scale is None:
        notes.append("Archetype evaluation skipped: no ground-truth archetypes were supplied (needs a synthetic scale).")
    else:
        weak = weak_labelled_churners(wh, settings, contract)
        arch_model, _ = fit_archetypes(train_df, weak, settings)
        churned = hold[hold["label"] == 1]
        if arch_model is None:
            notes.append("Archetype classifier was not trained (too few weak-labelled churners before the holdout).")
        elif len(churned):
            keys = key_map_frame(wh, contract, settings.key_pepper).set_index("subscriber_key")["customer_id"]
            if scale not in settings.synth.scales:
                raise ModelError(f"unknown scale {scale!r}; choose one of {sorted(settings.synth.scales)}")
            truth = churned["subscriber_key"].map(keys).map(latent_archetypes(settings, scale))
            predicted = pd.Series(predict_archetype(arch_model, churned[FEATURE_COLUMNS]).archetype, index=churned.index)
            keep = truth.notna()
            covered = int(keep.sum())
            if covered == 0 or covered < len(churned) * MIN_TRUTH_COVERAGE:
                raise ModelError(
                    f"scale {scale!r} gives ground truth for only {covered} of {len(churned)} holdout churners; "
                    "it does not match the population in the warehouse"
                )
            if covered < len(churned):
                notes.append(
                    f"Archetype metrics cover {covered} of {len(churned)} holdout churners; the rest have no "
                    f"ground truth under scale {scale!r}."
                )
            archetype_metrics, confusion = archetype_report(truth[keep], predicted[keep])
        else:
            notes.append("No churners in the holdout, so archetype metrics are empty.")

    version = _promoted_version(wh, settings)
    result = EvalResult(
        report_id=f"eval-{version}-{holdout_end:%Y%m%d}",
        model_version=version,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        n_rows=len(y),
        n_positives=n_pos,
        metrics=metrics,
        targets=targets,
        archetype_metrics=archetype_metrics,
        confusion=confusion,
        calibration=calibration,
        notes=notes,
        validation_start=ts(min(splits.validation)),
        validation_end=ts(max(splits.validation)),
        train_start=ts(min(splits.train)),
        train_end=ts(max(splits.train)),
        scale=scale,
    )
    result.report_md = render_report(result, settings, holdout_months, validation_months)
    result.model_card_md = render_model_card(result, settings, holdout_months, validation_months)
    _write_report(wh, settings, result)
    docs = Path(docs_dir) if docs_dir else project_root() / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "eval-report.md").write_text(result.report_md)
    (docs / "model-card.md").write_text(result.model_card_md)
    return result


# --- persistence and rendering -----------------------------------------------------------------------------


def _write_report(wh: Warehouse, settings: Settings, r: EvalResult) -> None:
    row = pd.DataFrame(
        [
            {
                "report_id": r.report_id,
                "model_version": r.model_version,
                "holdout_start": r.holdout_start,
                "holdout_end": r.holdout_end,
                "label_window_days": settings.label.horizon_days,
                "n_rows": r.n_rows,
                "n_positives": r.n_positives,
                "metrics_json": json.dumps(_json_safe(r.metrics), sort_keys=True),
                "targets_json": json.dumps(_json_safe([t.__dict__ for t in r.targets])),
                "archetype_json": json.dumps(_json_safe(r.archetype_metrics), sort_keys=True),
                "calibration_json": json.dumps(_json_safe(r.calibration.to_dict("records"))),
                "report_md": r.report_md,
                "created_at": datetime.now(),
            }
        ]
    )
    wh.merge_frame(f"{settings.schemas.ops}.EVAL_REPORTS", row, keys=["report_id"])


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def _num(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def _value(t: TargetResult) -> str:
    if t.name == "top_decile_lift":
        return "n/a" if _isnan(t.value) else f"{t.value:.2f}x"
    return _pct(t.value)


def _goal(t: TargetResult) -> str:
    return f"{t.op} {t.target:.1f}x" if t.name == "top_decile_lift" else f"{t.op} {t.target:.0%}"


def _target_rows(r: EvalResult) -> list[str]:
    rows = []
    for t in r.targets:
        detail = t.reason or "target met"
        rows.append(f"| {t.requirement} {t.name} | {_goal(t)} | {_value(t)} | {t.status} | {detail} |")
    return rows


def _summary(r: EvalResult) -> str:
    return "; ".join(f"{t.name} {t.status}" for t in r.targets)


def _boundary_lines(r: EvalResult) -> list[str]:
    m = r.metrics
    bd, pb, rb = m["boundary"], m["precision_at_boundary"], m["recall_breakdown"]
    if bd["threshold"] is None:
        return [f"- **Recommended at-risk boundary:** none, because {bd['reason']}. There is no fallback to the holdout."]
    lines = [
        (f"- **Recommended at-risk boundary:** {bd['threshold']:.3f}, the highest threshold whose recall on the "
        f"validation split is at least {BOUNDARY_MIN_RECALL:.2f} (validation recall {_pct(bd['validation_recall'])} "
        f"over {bd['validation_churners']} validation churners). It is a recommendation only: the runtime tiers and "
        "`AGENT_CONFIG` are not changed by this report."),
        (f"- **Holdout recall at the boundary:** {_pct(m['pre_cancellation_recall'])}: {rb['caught']} of "
        f"{rb['total']} cancelling churners caught, {rb['no_score']} with no score before cancelling, "
        f"{rb['below_boundary']} with a last score below the boundary."),
        (f"- **Holdout precision at the boundary (reported, no target):** {_pct(pb['precision'])}: "
        f"{pb['true_positives']} churners among {pb['flagged']} rows scored at or above the boundary."),
    ]
    return lines


def render_report(
    r: EvalResult, settings: Settings, holdout_months: int = HOLDOUT_MONTHS, validation_months: int = VALIDATION_MONTHS
) -> str:
    m = r.metrics
    sp = m["splits"]
    days = settings.label.horizon_days
    c = m["ceilings"]
    lines = [
        "# Model evaluation report",
        "",
        f"Generated by `churn evaluate`. Promoted model: `{r.model_version}`.",
        "",
        "## Label, window and splits",
        "",
        (f"- **Label (A1):** a subscriber is a churner when they have a voluntary cancellation within {days} days "
        "after the scoring date. Involuntary cancellations (payment failure) are not positives."),
        f"- **Window:** {days} days from each scoring date.",
        f"- **Synthetic scale:** {sp['scale'] or 'not given'}.",
        (f"- **Training:** monthly snapshots {r.train_start:%Y-%m-%d} to {r.train_end:%Y-%m-%d} "
        f"({sp['train_snapshots']} snapshots, {sp['train_rows']} rows). The evaluation model is fitted on these only "
        "and is not registered."),
        (f"- **Validation:** the {validation_months} months before the holdout, {r.validation_start:%Y-%m-%d} to "
        f"{r.validation_end:%Y-%m-%d} ({sp['validation_snapshots']} snapshots after the purge, "
        f"{sp['validation_rows']} rows, {sp['validation_positives']} churn labels). "
        "Used only to choose the at-risk boundary."),
        (f"- **Holdout:** time-based, the last {holdout_months} months of monthly snapshots: "
        f"{r.holdout_start:%Y-%m-%d} to {r.holdout_end:%Y-%m-%d}. It is never seen in training or boundary selection."),
        (f"- **Purge:** snapshots whose {days}-day label window reaches the next split are dropped: "
        f"{', '.join(sp['purged_snapshots']) or 'none'}."),
        (f"- **Holdout size:** {r.n_rows} scored (snapshot, subscriber) rows, {r.n_positives} churn labels "
        f"(base rate {_pct(m['base_rate'])})."),
        "",
        "## Synthetic data and the impact target",
        "",
        ("Synthetic accuracy is not real-world accuracy: the data is generated by this repository, so these numbers "
        "say nothing about real Thesis customers. The 90-day impact target can only be checked after go-live."),
        "",
        "## R3.6 targets",
        "",
        "| Requirement | Target | Measured | Status | Reason |",
        "|---|---|---|---|---|",
        *_target_rows(r),
        "",
        "## Measured precision and lift",
        "",
        f"- Holdout base rate b: {_pct(m['base_rate'])}",
        (f"- Top-2% precision: {_pct(m['top_share_precision'])} ({m['top_share_hits']} churners among "
        f"{m['top_share_rows']} rows)"),
        (f"- Top-decile precision: {_pct(m['top_decile_precision'])} ({m['top_decile_hits']} churners among "
        f"{m['top_decile_rows']} rows)"),
        f"- Top-decile lift: {'n/a' if _isnan(m['top_decile_lift']) else format(m['top_decile_lift'], '.2f') + 'x'}",
        "",
        "## At-risk boundary and recall",
        "",
        *_boundary_lines(r),
        ("- Pre-cancellation recall counts a churner as caught when their last holdout score before cancelling was at "
        "or above the boundary. Churners with no score before cancelling count as missed."),
        "",
        "## Precision ceilings",
        "",
        ("Computed from the measured holdout base rate b, not from an assumed rate. A perfect model puts every "
        "churner at the top, so:"),
        "",
        "| Ceiling | Formula | Value at measured b |",
        "|---|---|---|",
        f"| Top-decile precision | min(1, b/0.10) | {_pct(c['top_decile_precision'])} |",
        f"| Top-decile lift | min(10, 1/b) | {'n/a' if _isnan(c['top_decile_lift']) else format(c['top_decile_lift'], '.1f') + 'x'} |",
        f"| Top-2% precision | min(1, b/0.02) | {_pct(c['top_share_precision'])} |",
        "",
        ("**Why the 0.70 top-decile target was replaced:** under this label (A1) a perfect model's top-decile "
        "precision is at most b/0.10, so a target of 0.70 is only reachable when b is at least 7%. At the measured "
        f"base rate of {_pct(m['base_rate'])} the ceiling is {_pct(c['top_decile_precision'])}. The PRD (Revision 2) "
        "replaced it with top-2% precision > 0.50 and top-decile lift >= 4x, which have reachable ceilings. The "
        "earlier measurement (T8, fixed 0.20 boundary) was an honest MISS."),
        "",
        "## Ranking and probability quality",
        "",
        f"- ROC-AUC: {_num(m['roc_auc'])}",
        f"- PR-AUC: {_num(m['pr_auc'])} (a model with no skill scores the base rate, {_pct(m['base_rate'])})",
        f"- Brier score: {_num(m['brier'])}",
        f"- Expected calibration error: {_num(m['expected_calibration_error'])}",
        f"- Churners cancelling in the holdout window: {m['n_churners_cancelling']}",
        "",
        "## Calibration",
        "",
        "| Predicted range | Rows | Mean predicted | Observed churn rate |",
        "|---|---|---|---|",
    ]
    for _, cal in r.calibration[r.calibration["n"] > 0].iterrows():
        lines.append(
            f"| {cal.bin_low:.1f} to {cal.bin_high:.1f} | {int(cal.n)} | {_pct(cal.mean_predicted)} | {_pct(cal.observed_rate)} |"
        )
    lines += [
        "",
        "## Archetype classification (R3.3)",
        "",
        "Judged on holdout churners against the generator's latent archetype (ground truth that is never a feature).",
        "",
        "| Archetype | Precision | Recall | Support | Predicted |",
        "|---|---|---|---|---|",
    ]
    for a in ARCHETYPES:
        am = r.archetype_metrics[a]
        lines.append(f"| {a} | {_pct(am['precision'])} | {_pct(am['recall'])} | {am['support']} | {am['predicted']} |")
    lines += ["", "### Confusion matrix (rows: truth, columns: predicted)", ""]
    if r.confusion is None:
        lines.append("Not available.")
    else:
        lines += ["| truth \\ predicted | " + " | ".join(ARCHETYPES) + " |", "|---|" + "---|" * len(ARCHETYPES)]
        for truth, row in r.confusion.iterrows():
            lines.append(f"| {truth} | " + " | ".join(str(int(v)) for v in row) + " |")
    for note in r.notes:
        lines += ["", f"_{note}_"]
    lines.append("")
    return "\n".join(lines)


def render_model_card(
    r: EvalResult, settings: Settings, holdout_months: int = HOLDOUT_MONTHS, validation_months: int = VALIDATION_MONTHS
) -> str:
    days = settings.label.horizon_days
    m = r.metrics
    bd = m["boundary"]
    boundary = (
        f"{bd['threshold']:.3f} (recommended only, chosen on validation data)"
        if bd["threshold"] is not None
        else "none (" + str(bd["reason"]) + ")"
    )
    return "\n".join(
        [
            "# Model card: churn risk model",
            "",
            f"- **Model version:** `{r.model_version}`",
            ("- **Type:** LightGBM gradient-boosted trees, isotonic-calibrated when enough calibration churners exist; "
            "TreeSHAP factors; a multinomial logistic archetype classifier on top."),
            "- **Report:** `docs/eval-report.md` (regenerate with `churn evaluate`).",
            "",
            "## Intended use",
            "",
            (f"Rank active subscribers by the probability of a voluntary cancellation in the next {days} days, so the "
            "retention agent can pick an intervention for the at-risk ones. Not for pricing, credit or any decision "
            "outside retention outreach, and not a statement about any individual's intent."),
            "",
            "## Label and data",
            "",
            (f"- Positive: voluntary cancellation within {days} days after the scoring date (A1). Involuntary "
            "cancellations are excluded."),
            ("- Training data: monthly point-in-time snapshots of active subscribers built by the feature SQL, "
            "so each row only sees data from before its scoring date."),
            ("- Data in this repository is synthetic. Numbers below say nothing about real Thesis customers until the "
            "model is evaluated on their data. The 90-day impact target can only be checked after go-live."),
            "",
            f"## Evaluation (holdout {r.holdout_start:%Y-%m-%d} to {r.holdout_end:%Y-%m-%d})",
            "",
            (f"- Splits: training {r.train_start:%Y-%m-%d} to {r.train_end:%Y-%m-%d}; validation "
            f"{r.validation_start:%Y-%m-%d} to {r.validation_end:%Y-%m-%d} ({validation_months} months, boundary "
            f"selection only); holdout the last {holdout_months} months. Synthetic scale: "
            f"{m['splits']['scale'] or 'not given'}."),
            (f"- The numbers come from an evaluation model fitted on the training split only, not from the promoted "
            f"`{r.model_version}` bundle. The production model is refit on all snapshots."),
            f"- Rows: {r.n_rows}; churn labels: {r.n_positives} (base rate {_pct(m['base_rate'])})",
            f"- ROC-AUC {_num(m['roc_auc'])}, PR-AUC {_num(m['pr_auc'])}, Brier {_num(m['brier'])}",
            f"- Recommended at-risk boundary: {boundary}. The runtime tiers are not changed by it.",
            (f"- Top-decile precision {_pct(m['top_decile_precision'])} against a ceiling of "
            f"{_pct(m['ceilings']['top_decile_precision'])} at the measured base rate; the earlier 0.70 top-decile "
            "target was replaced because it is unreachable under this label."),
            "",
            "| Requirement | Target | Measured | Status | Reason |",
            "|---|---|---|---|---|",
            *_target_rows(r),
            "",
            f"Status summary: {_summary(r)}.",
            "",
            "## Limitations",
            "",
            "- A target marked MISS is a missed target. It is reported as measured, not tuned away.",
            "- Metrics from a small or synthetic population are noisy; read the churn-label counts next to them.",
            ("- Archetypes come from weak labels (cancel reason codes and ticket categories), so they are only as good "
            "as those signals; low-confidence archetypes are flagged at scoring time."),
            "- The model has not been audited for differences in accuracy between customer groups.",
            "",
        ]
    )
