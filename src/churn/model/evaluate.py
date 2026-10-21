"""Honest model evaluation (R3.6): time holdout, A1 label, PASS / MISS-with-reason per target.

The evaluated model is refit with the training recipe on the snapshots *before* the holdout, so the holdout
months are never seen by the model that scores them. Pre-cancellation recall uses, for each churner, the last
holdout score made before the cancellation.
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

from churn import runtime_config
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

PRECISION_TARGET = 0.70
RECALL_TARGET = 0.60
HOLDOUT_MONTHS = 3  # A1: eval.holdout_months
DECILE = 0.10
MIN_STABLE_POSITIVES = 30
MIN_TRUTH_COVERAGE = 0.5  # below this share the synthetic scale clearly does not match the warehouse


# --- metrics -----------------------------------------------------------------------------------------------


def top_decile_precision(y_true, probs) -> float:
    """Share of churners among the ceil(10%) highest-scored rows (NaN for an empty frame)."""
    y = np.asarray(y_true)
    p = np.asarray(probs, dtype=float)
    if len(y) == 0:
        return float("nan")
    k = max(1, math.ceil(len(y) * DECILE))
    top = np.argsort(-p, kind="stable")[:k]
    return float(y[top].mean())


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


def _fmt(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.2f}"


def check_targets(
    precision: float,
    recall: float,
    base_rate: float,
    n_positives: int,
    n_rows: int,
    recall_breakdown: dict | None = None,
) -> list[TargetResult]:
    """R3.6: both targets must be strictly exceeded; every miss states why."""
    out = []
    for name, target, value in (
        ("top_decile_precision", PRECISION_TARGET, precision),
        ("pre_cancellation_recall", RECALL_TARGET, recall),
    ):
        reason = None
        if value is None or math.isnan(value):
            reason = "no churners in the holdout, so the metric is undefined"
        elif not value > target:
            reason = f"{name.replace('_', ' ')} {_fmt(value)} is not above the target {_fmt(target)}"
            context = [f"holdout has {n_positives} churn labels in {n_rows} scored rows (base rate {base_rate:.1%})"]
            if name == "top_decile_precision" and n_rows and n_positives / (n_rows * DECILE) < target:
                ceiling = min(1.0, n_positives / (n_rows * DECILE))
                context.append(f"the best possible top-decile precision is {_fmt(ceiling)} at this base rate")
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
        out.append(
            TargetResult(name, "R3.6", target, value, "MISS" if reason else "PASS", reason)
        )
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


def _promoted_version(wh: Warehouse, settings: Settings) -> str:
    df = wh.query(
        f"SELECT model_version FROM {settings.schemas.ops}.MODEL_REGISTRY WHERE status = 'promoted' "
        "ORDER BY created_at DESC"
    )
    return str(df.iloc[0]["model_version"]) if len(df) else "unregistered"


def _at_risk_threshold(wh: Warehouse, settings: Settings) -> tuple[float, str | None]:
    """The at-risk boundary, and a note when the runtime tiers were invalid and the defaults were used."""
    cfg = runtime_config.load(wh, settings)
    note = None
    tiers = cfg.tiers
    if tiers is None:
        note = "Runtime tier config is missing or invalid; the default tiers were used for the at-risk boundary."
        tiers = runtime_config.TierConfig.model_validate(runtime_config.defaults(settings)["tiers"])
    return float(tiers.boundaries[tiers.at_risk_from]), note


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
) -> EvalResult:
    """Score the time holdout, check the R3.6 targets and write EVAL_REPORTS and the two docs.

    ``scale`` names the synthetic population whose latent archetypes judge the archetype classifier; without it
    the archetype section is skipped.
    """
    if scale is not None and scale not in settings.synth.scales:
        raise ModelError(f"unknown scale {scale!r}; choose one of {sorted(settings.synth.scales)}")
    contract = contract_for(settings)
    train_end = train_end or settings.synth.end_date
    df = build_dataset(wh, settings, contract, train_end)
    dates = sorted(df["snapshot_date"].unique()) if not df.empty else []
    if len(dates) <= holdout_months:
        raise ModelError(f"need more than {holdout_months} snapshots to hold out; found {len(dates)}")
    hold_dates = set(dates[-holdout_months:])
    is_hold = df["snapshot_date"].isin(hold_dates)
    train_df, hold = df[~is_hold], df[is_hold].reset_index(drop=True)

    model, _ = fit(train_df, settings)
    probs = model.predict_proba(hold[FEATURE_COLUMNS])[:, 1]
    y = hold["label"].to_numpy()
    n_pos = int(y.sum())
    threshold, tier_note = _at_risk_threshold(wh, settings)
    holdout_start, holdout_end = pd.Timestamp(min(hold_dates)).to_pydatetime(), pd.Timestamp(max(hold_dates)).to_pydatetime()

    scores = pd.DataFrame(
        {"subscriber_key": hold["subscriber_key"], "as_of": pd.to_datetime(hold["snapshot_date"]), "churn_prob": probs}
    )
    cancels = _cancellations(wh, settings, contract, holdout_start, holdout_end)
    cancels = cancels[cancels["cancelled_at"] > pd.Timestamp(holdout_start)]

    breakdown = pre_cancellation_breakdown(scores, cancels, threshold)
    both = n_pos > 0 and n_pos < len(y)
    metrics = {
        "top_decile_precision": top_decile_precision(y, probs),
        "pre_cancellation_recall": pre_cancellation_recall(scores, cancels, threshold),
        "recall_breakdown": breakdown,
        "roc_auc": float(roc_auc_score(y, probs)) if both else float("nan"),
        "pr_auc": float(average_precision_score(y, probs)) if n_pos else float("nan"),
        "brier": float(brier_score_loss(y, probs)),
        "base_rate": float(y.mean()) if len(y) else float("nan"),
        "at_risk_threshold": threshold,
        "n_churners_cancelling": int(cancels["subscriber_key"].nunique()),
    }
    calibration = calibration_table(y, probs)
    filled = calibration[calibration["n"] > 0]
    metrics["expected_calibration_error"] = float(
        (filled["n"] * (filled["mean_predicted"] - filled["observed_rate"]).abs()).sum() / max(1, filled["n"].sum())
    )
    targets = check_targets(
        metrics["top_decile_precision"], metrics["pre_cancellation_recall"], metrics["base_rate"], n_pos, len(y), breakdown
    )

    notes: list[str] = [tier_note] if tier_note else []
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
    )
    result.report_md = render_report(result, settings, holdout_months)
    result.model_card_md = render_model_card(result, settings, holdout_months)
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


def _target_rows(r: EvalResult) -> list[str]:
    rows = []
    for t in r.targets:
        goal = f"> {t.target:.0%}"
        detail = t.reason or "target exceeded"
        rows.append(f"| {t.requirement} {t.name} | {goal} | {_pct(t.value)} | {t.status} | {detail} |")
    return rows


def _summary(r: EvalResult) -> str:
    return "; ".join(f"{t.name} {t.status}" for t in r.targets)


def render_report(r: EvalResult, settings: Settings, holdout_months: int = HOLDOUT_MONTHS) -> str:
    m = r.metrics
    days = settings.label.horizon_days
    lines = [
        "# Model evaluation report",
        "",
        f"Generated by `churn evaluate`. Promoted model: `{r.model_version}`.",
        "",
        "## Label, window and holdout",
        "",
        (f"- **Label (A1):** a subscriber is a churner when they have a voluntary cancellation within {days} days "
        "after the scoring date. Involuntary cancellations (payment failure) are not positives."),
        f"- **Window:** {days} days from each scoring date.",
        (f"- **Holdout:** time-based, the last {holdout_months} months of monthly snapshots: "
        f"{r.holdout_start:%Y-%m-%d} to {r.holdout_end:%Y-%m-%d}. The evaluated model is refit with the training "
        "recipe on earlier snapshots only, so the holdout is never seen in training."),
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
        ("Pre-cancellation recall counts a churner as caught when their last holdout score before cancelling was at "
        f"or above the at-risk boundary ({m['at_risk_threshold']:.2f}). Churners with no score before cancelling "
        "count as missed."),
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
    for _, c in r.calibration[r.calibration["n"] > 0].iterrows():
        lines.append(
            f"| {c.bin_low:.1f} to {c.bin_high:.1f} | {int(c.n)} | {_pct(c.mean_predicted)} | {_pct(c.observed_rate)} |"
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


def render_model_card(r: EvalResult, settings: Settings, holdout_months: int = HOLDOUT_MONTHS) -> str:
    days = settings.label.horizon_days
    m = r.metrics
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
            f"## Evaluation (last {holdout_months} months, {r.holdout_start:%Y-%m-%d} to {r.holdout_end:%Y-%m-%d})",
            "",
            (f"- Numbers come from a holdout refit twin (trained on snapshots before the holdout), not from the "
            f"promoted `{r.model_version}` bundle itself."),
            f"- Rows: {r.n_rows}; churn labels: {r.n_positives} (base rate {_pct(m['base_rate'])})",
            f"- ROC-AUC {_num(m['roc_auc'])}, PR-AUC {_num(m['pr_auc'])}, Brier {_num(m['brier'])}",
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
