# Waves: Churn Prediction & Intervention Agent

Input: `docs/plans/churn-prediction/TASKS.md` (tickets T1–T30). A wave is built in parallel, and the next wave starts once the previous one is merged.

**Revision 2 (2026-10-21, change request during wave 5).** What changed in this file:
- Waves 1–4 are finished and keep their numbers and tickets. T2 stays in wave 2 as the record of MR !5; it is superseded by T29 and not rebuilt.
- **T8** (amendment delivered in MR !23) stays in wave 5, now shipped. T9, T11 and T14 stay in wave 5.
- **T29** (new) goes in wave 6. T27 (wave 7, quiz rows at full scale) and T16 (wave 8, reads `email_events` and redemptions through the contract) need all eight views mapped, so T29 has to come before them. T29 shares `src/churn/cli.py` with T8 (wave 5), T24 (wave 6) and T27 (wave 7).
- **T24** moves from wave 6 to wave 10 to make room for T29. The operator chose this on 2026-10-21. Only T23 (wave 14) depends on T24, so it still comes first. In wave 10 T24 shares no file with T19.
- **T30** (new) goes in wave 7. It needs T8 (wave 5) and T29 (wave 6), and its files are separate from those of T12, T15, T26 and T27.
- No other ticket moves, and the number of waves stays at 15.
- T27 (wave 7) changes `synth/generator.py` after T29 has rebuilt it, so T27 adds chunking to the full generator and not to the interim one.

Rules this plan follows:
- Every ticket is in exactly one wave, and waves are numbered 1–15 with no gaps.
- A ticket comes after every ticket it depends on.
- No two tickets in the same wave list the same file. The "Shared files" table in TASKS.md was checked wave by wave.

The plan has 15 waves. The minimum is set by the shared files: `src/churn/cli.py` is touched by 11 tickets and `src/churn/orchestration/runner.py` by 9, so those tickets have to go in different waves.

## Overview

| Wave | Tickets | Goal |
|---|---|---|
| 1 | T1 | Thinnest end-to-end path: synth → train → score into RISK_SCORES on DuckDB, plus the project skeleton |
| 2 | T2, T3, T6 | Full synthetic source, hardened source contract, explainable scores |
| 3 | T4, T5 | Full feature catalogue; the runtime config table drives risk tiers |
| 4 | T7, T10 | Archetype classifier; agent thin path to OUTBOX and INTERVENTION_LOG |
| 5 | T8, T9, T11, T14 | Evaluation report, logging and error taxonomy, all four intervention paths, approval gate |
| 6 | T13, T29 | Contextual Thompson-sampling policy; full synthetic source with all eight contract views mapped (supersedes T2) |
| 7 | T12, T15, T26, T27, T30 | Guardrails, LLM content with fallback, extensibility, full-scale throughput, re-evaluation on the full data |
| 8 | T16, T25 | Holdout, simulated responses, outcome tracking; PII proof |
| 9 | T17, T18 | Feedback loops into selection (posteriors) and scoring (retrain) |
| 10 | T19, T24 | Run metrics and the evaluation run; Snowflake backend and bootstrap |
| 11 | T20 | Failure recovery: checkpoints, run lock, fault injection |
| 12 | T21 | FastAPI service |
| 13 | T22 | Scheduled worker |
| 14 | T23 | Containerised deployment and deployment guide |
| 15 | T28 | Documentation set and submission deliverables |

## Wave 1 — prove the design

**Tickets:** T1

**Goal:** the thinnest slice that proves the architecture. Synthetic subscriptions pass through the source contract and one feature SQL file into a calibrated LightGBM model. Every active subscriber is scored into `CHURN_OPS.RISK_SCORES`, and a `RUNS` row is written, through the warehouse protocol, MERGE writer and migrations.

**Why alone:** every other ticket builds on the skeleton T1 creates.

## Wave 2 — widen the data and the score

**Tickets:** T2, T3, T6

**Goal:**
- T2: all source domains with latent archetypes and edge cases.
- T3: the source contract fails safely, deduplicates events, normalises to UTC and pseudonymises keys, and can be swapped by config.
- T6: every score carries its top factors.

**Why parallel:** each needs only T1. They touch separate files:
- T2: synth, `source_contract.yaml`, `cli.py`
- T3: contract modules, `settings.py`, `runner.py`
- T6: `model/explain.py`, `model/score.py`, `features/registry.py`

## Wave 3 — features and runtime config

**Tickets:** T4, T5

**Goal:**
- T4: the full feature catalogue (five leading indicators, multi-window trends, no leakage, missing-domain handling). It needs T2's domains.
- T5: `AGENT_CONFIG` with pinned snapshots and fail-safe validation, which drives the risk tiers.

**Why parallel:** T4 touches the feature SQL, `features/registry.py` and `features/builder.py`. T5 touches the runtime config, `model/score.py`, `runner.py` and `cli.py`. No file overlaps. T6 already changed `registry.py` and `score.py`, but in wave 2.

## Wave 4 — archetypes and the agent thin path

**Tickets:** T7, T10

**Goal:**
- T7: archetype, confidence and a low-confidence flag on every at-risk score. It needs T2's reason codes.
- T10: an at-risk subscriber, identified through T5's tiers, gets a check-in email written to OUTBOX and INTERVENTION_LOG by the new decide/dispatch step.

**Why parallel:** T7 stays inside `model/` (`train.py`, `score.py`, `archetype.py`). T10 creates `interventions/`, `agent/` and `execution/` and changes `runner.py`. No file overlaps.

## Wave 5 — widen model, agent and operations

**Tickets:** T8, T9, T11, T14

**Status (2026-10-22):** shipped. T9's audit recorded completed local repair evidence but left publication and remote CI pending; those historical caveats are superseded by this ship run's input confirming all wave tickets merged, default-branch pipeline [65604](https://labs.gauntletai.com/andrebatista/thesis-churn-prediction/-/pipelines/65604) passed, and the operator smoke tested the deployed app. Closure is based on that ship evidence, not local tests alone. The platform closed four tickets and released two for wave 6.

**Goal:**
- T8 (amended in Revision 2): the honest evaluation report and model card against the new R3.6 targets. The boundary is chosen on a validation split and the report shows ceilings from the measured base rate. It needs T7 for per-archetype metrics.
- T9: structured logging and the error taxonomy in the runner.
- T11: the formulation, retention-offer and escalation paths (needs T10 and T4's `has_quiz`).
- T14: the threshold gate and the approval table lifecycle (needs T10 and T5's snapshot).

**Why parallel:** the four file sets are disjoint:
- T8: `model/evaluate.py`, V005, `cli.py`
- T9: `pyproject.toml`, `obs/`, `runner.py`
- T11: new intervention modules, `interventions.yaml`, `agent/content.py`
- T14: `agent/gate.py`, `execution/approvals.py`, `agent/cycle.py`, V009

## Wave 6 — context-aware selection and the full synthetic source

**Tickets:** T13, T29

**Status (2026-10-23): shipped.** Platform input confirms !26 (T13) and !27 (T29) merged, default-branch pipeline 65760 passed, and the operator smoke tested the deployed app. T13's audit verified its named acceptance tests (38 targeted tests passed; Builder reported 167 passed / 1 deselected and lint passing). T29's audit verified its named acceptance tests and full default suite (163 passed / 1 deselected), with ruff passing. These are ticket-specific results, not a new combined-suite run. T2 remains superseded; T27 owns full-scale volume/throughput and T30 owns full-source re-evaluation.

**Goal:**
- T13: replaces the deterministic choice with the contextual Thompson-sampling policy and per-decision rationale. It needs T7, T10 and T11.
- T29 (new in Revision 2): rebuilds T2's full synthetic source on current main. It keeps T7's `latent_archetypes()` interface and RNG streams, adds `latent_truth`, noisy reason codes, involuntary churn and edge cases, and maps all eight contract views (`customers_pii` flagged pii). It needs T3, T4 and T7, which are all merged. It comes before T27 and T16, which need the full views.

**Why parallel:** T13 touches `agent/` (`policy.py`, `rationale.py`, `cycle.py`), `sql/migrations/V016__policy_stats.sql`, `runtime_config.py`, `runtime_defaults.yaml` and existing agent/approval/routing integration tests. V008 is a historical hole; V015 was reserved for T29 under ARCHITECTURE §5.3. T29 touches `synth/`, `contract/loader.py`, `contract/views.py`, `source_contract.yaml`, `cli.py`, synth/V003, V015, its docs and tests, and `tickets/T29.md`. No file overlaps. T29 cannot go in wave 5 because T8 also changes `cli.py`. T24 was in this wave and also changes `cli.py`, so it moved to wave 10.

## Wave 7 — guardrails, LLM content, extensibility, scale, re-evaluation

**Tickets:** T12, T15, T26, T27, T30

**Goal:**
- T12: guardrails and coverage gaps. It comes after T14 because the duplicate-pending rule reads APPROVALS.
- T15: LLM-personalised content behind a PII-free prompt, with template fallback for all four types from T11.
- T26: interventions loaded by dotted path, plus the module-boundary test (needs T13's priors for the dummy arm).
- T27: full-scale data and a timed cycle. It adds chunking to the full generator that T29 rebuilt in wave 6, so the full-scale quiz volumes exist.
- T30 (new in Revision 2): reruns the evaluation on the full synthetic data with every view mapped, adds data provenance and domain coverage to the report, and commits the regenerated eval report and model card. It needs T8 (wave 5) and T29 (wave 6).

**Why parallel:** the file sets are disjoint:
- T12: `agent/guardrails.py`, `agent/cycle.py`, V007
- T15: `llm/`, `agent/content.py`, `pyproject.toml`, `config/settings.yaml`
- T26: `interventions/base.py`, the test fixture, `docs/extending.md`
- T27: `synth/generator.py`, `cli.py`
- T30: `model/evaluate.py`, `docs/eval-report.md`, `docs/model-card.md`, `test_eval_full_source.py`, `tickets/T30.md`

## Wave 8 — outcomes and PII proof

**Tickets:** T16, T25

**Goal:**
- T16: the holdout, simulated responses, outcome closing after 30 days, `churn simulate` and the treated-vs-control report. It reads `email_events` and redemptions through the contract views that T29 mapped in wave 6.
- T25: log redaction and a full-cycle PII scan. The scan covers APPROVALS (T14) and the LLM prompts (T15), so it waits for both.

**Why parallel:** T16 touches `feedback/`, `synth/responder.py`, `agent/cycle.py`, `runner.py`, `cli.py`, V010 and `impact.sql`. T25 touches only `obs/logging.py` and its own tests.

## Wave 9 — feedback loops

**Tickets:** T17, T18

**Goal:**
- T17: closed outcomes update the policy posteriors, so selection shifts toward what works.
- T18: intervention history and observed outcomes enter retraining, behind a promotion gate that uses T8's metrics.

Both need T16's outcomes.

**Why parallel:** T17 touches `feedback/policy_stats.py` and `runner.py`. T18 touches `feedback/retrain.py`, `model/registry.py`, `model/dataset.py`, `features/registry.py`, `interventions.sql` and `cli.py`. No file overlaps.

## Wave 10 — run metrics and Snowflake

**Tickets:** T19, T24

**Goal:**
- T19: coverage, autonomy rate, audit completeness, LLM usage and treated-vs-control churn for each run, plus the 45-day evaluation run. It needs the holdout (T16), approvals (T14), coverage gaps (T12) and LLM fallbacks (T15).
- T24 (moved from wave 6 in Revision 2): the Snowflake backend, bootstrap of schemas and roles, the `OUTBOX_FOR_SENDER` view (needs T10's OUTBOX) and the SQL portability test. By this wave the test also covers every SQL file added up to wave 9. Its migration keeps the number V013. The runner applies versions it has not seen yet, so landing V013 after V015 is fine.

**Why parallel:** T19 touches `feedback/metrics.py`, V011, `runner.py` and its tests. T24 touches the warehouse, bootstrap SQL, V013, `pyproject.toml`, `cli.py` and its tests. No file overlaps. `runner.py` keeps T19 apart from T17 (wave 9) and T20 (wave 11). `cli.py` and `pyproject.toml` keep T24 apart from T18 (wave 9) and T21 (wave 12).

## Wave 11 — failure recovery

**Tickets:** T20

**Goal:** step and shard checkpoints, the run lock, source retries, and fault injection over the full cycle, including an LLM outage (T15) and the error taxonomy (T9).

**Why alone:** it changes `runner.py` and `agent/cycle.py`. Every remaining ticket that could run beside it either touches those files or depends on T20's lock.

## Wave 12 — API service

**Tickets:** T21

**Goal:** health, readiness, triggering runs behind the lock (T20) and inspecting runs and metrics (T19) over FastAPI.

**Why alone:** T22 shares `pyproject.toml` and `cli.py` with it.

## Wave 13 — scheduled operation

**Tickets:** T22

**Goal:** a worker with the daily cycle, the retrain cron (T18) and catch-up, guarded by the lock (T20).

**Why alone:** it shares `pyproject.toml` and `cli.py` with T21, and T23 deploys the worker it adds.

## Wave 14 — deployment

**Tickets:** T23

**Goal:** a non-root image for the `api` (T21) and `worker` (T22) services, `.env.example` covering the Snowflake settings (T24, wave 10), and the deployment guide with the config-only switch to production views. The end-to-end check is done by the operator.

**Why alone:** it needs T21, T22 and T24 merged. T28 checks that `docs/deployment.md` exists, so T28 must come after it.

## Wave 15 — documentation and deliverables

**Tickets:** T28

**Goal:** the full document set and the test-results report. The docs test checks every ops table and runtime config key added by all earlier tickets.

**Why last:** it documents the finished system.
