# Tasks: Churn Prediction & Intervention Agent

Inputs: `docs/plans/churn-prediction/PRD.md` (requirements R1–R9) and `docs/plans/churn-prediction/ARCHITECTURE.md` (design and defaults A1–A12). Waves come later, in `WAVES.md`.

**Revision 2 (2026-10-21, change request during wave 5).** What changed in this file:
- **T2** is marked *Superseded by T29*. It was merged as MR !5, but its code is no longer on main. Its slice, files and tests stay below for reference, and T29 is the spec that gets built.
- **T8** (Revision 2 amendment delivered, MR !23): the slice and acceptance tests are amended for the new R3.6 targets. The label stays A1. The new targets are top-2% precision > 0.50, top-decile lift ≥ 4× and pre-cancellation recall > 0.60 at a boundary chosen on a validation split. The ceiling arithmetic is in ARCHITECTURE A1. The files are unchanged.
- **T29** (new) rebuilds T2's full synthetic source on current main, reconciled with T7's interim `latent_archetypes()`. It uses migrations `synth/V003` and `V015` instead of V002, and maps all eight contract views (R1.6, R1.7).
- **T30** (new) reruns the T8 evaluation on the full synthetic data with every view mapped, and regenerates the eval report and model card (R3.8).
- The coverage and shared-files tables are updated. No other ticket changes.

Conventions for every ticket:
- Each ticket is a thin end-to-end slice: data (migration or synthetic data), logic, interface (CLI step, runner step, API or document) and the tests that prove it.
- All tests run with `python -m pytest` from the repo root with no network and no Snowflake credentials (DuckDB backend and `FakeLLM`). The exceptions are tests marked `slow`, `scale` or `snowflake`.
- Every ticket adds its own migration file. Migrations are never edited after they ship, so tickets do not share migration files.
- "Files" lists every path the ticket creates or changes. Two tickets that share a file must go in different waves.
- The defaults (tiers, thresholds, caps, windows) are the A1–A12 values from ARCHITECTURE.md §2.

---

## T1 — Thinnest path: synthetic subscriptions scored into RISK_SCORES on DuckDB

**Slice:** `churn synth --scale tiny` writes synthetic subscriptions and orders into `THESIS_SYNTH` on DuckDB, read through the `subscriptions`/`orders` entries of the source contract. `churn train` fits a calibrated LightGBM model on point-in-time snapshots built from one feature SQL file and registers it. `churn cycle --as-of D` scores every active subscriber and merges `churn_prob`, `model_version` and `run_id` into `CHURN_OPS.RISK_SCORES`. It also records a `RUNS` row with `active_in_source` and `scored` and fails if the two differ. The ticket also sets up the project skeleton (pyproject, warehouse protocol with a DuckDB backend, sqlglot dialect rendering, MERGE writer, migration runner, CI).

**Files:**
- `pyproject.toml`
- `.gitignore`
- `.gitlab-ci.yml`
- `config/settings.yaml`
- `config/source_contract.yaml`
- `sql/synth/V001__synth_schema.sql`
- `sql/migrations/V001__ops_schema.sql`
- `sql/features/subscriptions.sql`
- `src/churn/__init__.py`
- `src/churn/settings.py`
- `src/churn/warehouse/__init__.py`
- `src/churn/warehouse/base.py`
- `src/churn/warehouse/duckdb.py`
- `src/churn/warehouse/dialect.py`
- `src/churn/warehouse/bulk.py`
- `src/churn/warehouse/migrate.py`
- `src/churn/contract/__init__.py`
- `src/churn/contract/loader.py`
- `src/churn/contract/views.py`
- `src/churn/synth/__init__.py`
- `src/churn/synth/generator.py`
- `src/churn/synth/latent.py`
- `src/churn/features/__init__.py`
- `src/churn/features/registry.py`
- `src/churn/features/builder.py`
- `src/churn/model/__init__.py`
- `src/churn/model/dataset.py`
- `src/churn/model/train.py`
- `src/churn/model/score.py`
- `src/churn/model/registry.py`
- `src/churn/orchestration/__init__.py`
- `src/churn/orchestration/runner.py`
- `src/churn/cli.py`
- `tests/conftest.py`
- `tests/integration/test_thin_cycle.py`

**Acceptance tests:**
- `test_thin_cycle.py::test_every_active_subscriber_scored`: the RISK_SCORES row count for the run equals the active subscribers in the source, and RUNS records both counts.
- `test_thin_cycle.py::test_score_history_query`: two cycles on different as_of dates; a Metabase-style SQL query returns both scores for one subscriber.
- `test_thin_cycle.py::test_same_run_id_rerun_identical`: rerunning the same run id gives identical row counts and keys.
- `test_thin_cycle.py::test_migrations_idempotent`: applying migrations twice is a no-op, and SCHEMA_MIGRATIONS stores checksums.
- `test_thin_cycle.py::test_scored_count_mismatch_fails_run`: a forced mismatch marks the run FAILED.

**Covers:** R3.1, R3.5, R8.2 (base), R9.1 (harness, CI).

---

## T2 — Full synthetic source: all domains, latent archetypes, edge cases, data profile

**Status: Superseded by T29 (Revision 2).** This ticket was merged as MR !5, but a later commit removed its code from main, so it is no longer there. It is not rebuilt under this id. T29 rebuilds this slice on current main, and the text below is kept only as T29's reference spec.

**Slice:** extend the generator into the latent-state simulator (ARCHITECTURE §5.2). It covers all eight contract views (subscription_events, products, email_events, support_tickets, quiz_responses, customers_pii, plus the T1 views), noisy cancellation reason codes drawn from a latent archetype, independent involuntary churn, and injected edge cases (no quiz, no email, no tickets, single order, tenure < 30 d, nulls, duplicates, late-arriving rows, timezone offsets). `churn synth --profile` writes `docs/data-profile.md` and `CHURN_OPS.DATA_PROFILE`.

**Files:**
- `config/source_contract.yaml`
- `sql/synth/V002__synth_all_domains.sql`
- `sql/migrations/V002__data_profile.sql`
- `src/churn/synth/generator.py`
- `src/churn/synth/latent.py`
- `src/churn/synth/edge_cases.py`
- `src/churn/synth/profile.py`
- `src/churn/cli.py`
- `docs/data-profile.md`
- `tests/unit/test_synth_latent.py`
- `tests/unit/test_synth_import_boundary.py`
- `tests/integration/test_synth_profile.py`

**Acceptance tests:**
- `test_synth_profile.py::test_every_domain_has_view`: each R1.1 domain has at least one populated contract view.
- `test_synth_profile.py::test_edge_case_shares_above_zero`: the profile shows a share above 0% for every edge-case category.
- `test_synth_profile.py::test_churn_events_span_label_and_eval_window`: voluntary cancellations cover at least the 30-day horizon plus the 3-month holdout.
- `test_synth_latent.py::test_deterministic_for_seed`: the same seed gives identical output.
- `test_synth_latent.py::test_reason_codes_follow_latent_archetype_with_noise`: about 25% noise.
- `test_synth_import_boundary.py::test_synth_does_not_import_features_or_model`

**Covers:** R1.1, R1.2 (content; full scale in T27).

---

## T3 — Source contract hardening: validation, dedup/UTC, pseudonymous key, swap by config

**Slice:**
- `contract.validate` checks at cycle start that every mapped object and column exists with compatible types, and fails the run with `ContractError` before anything is written.
- `contract.views` adds column renames, UTC normalisation, event-id dedup and `subscriber_key = SHA2(pepper || customer_id)` (pepper from env `CHURN_KEY_PEPPER`).
- `CHURN_PRIVATE.KEY_MAP` is populated.
- Pointing the config at a second schema with the same contract produces a successful run.
- `docs/data-contract.md` lists every view and column.

**Files:**
- `src/churn/contract/validate.py`
- `src/churn/contract/views.py`
- `src/churn/contract/loader.py`
- `src/churn/settings.py`
- `src/churn/orchestration/runner.py`
- `sql/migrations/V003__private_key_map.sql`
- `docs/data-contract.md`
- `tests/unit/test_contract_views.py`
- `tests/integration/test_contract_validate.py`
- `tests/integration/test_contract_swap.py`

**Acceptance tests:**
- `test_contract_swap.py::test_second_schema_by_config_only`: a copy of the source under another schema plus a changed YAML gives a successful scoring run with no code change.
- `test_contract_validate.py::test_missing_column_fails_before_write`: ContractError, and no RISK_SCORES rows for the run.
- `test_contract_views.py::test_duplicate_events_deduped_latest_loaded_at_wins`
- `test_contract_views.py::test_timestamps_normalised_to_utc`
- `test_contract_views.py::test_ops_tables_carry_subscriber_key_only`: no customer_id column in any CHURN_OPS table.
- `test_contract_views.py::test_subscriber_key_depends_on_pepper`

**Covers:** R1.3, R1.4 (contract doc), R7.1 (keys).

---

## T4 — Feature catalogue: five leading indicators, multi-window trends, no leakage, missing data

**Slice:**
- Add per-domain feature SQL (email, subscription events, orders, support, quiz) bounded by `:as_of`, with the five required leading indicators and at least one multi-window or slope feature per domain.
- Missing domains produce `has_<domain>` indicators and NULL rates, never errors.
- Brand-new subscribers use tenure-truncated windows.
- The registry generates `docs/features.md`.
- Scoring a fixture with one subscriber in each edge-case category returns a score for every one of them.

**Files:**
- `sql/features/subscriptions.sql`
- `sql/features/subscription_events.sql`
- `sql/features/orders.sql`
- `sql/features/email.sql`
- `sql/features/support.sql`
- `sql/features/quiz.sql`
- `src/churn/features/registry.py`
- `src/churn/features/builder.py`
- `docs/features.md`
- `tests/fixtures/subscribers.py`
- `tests/unit/test_leading_indicators.py`
- `tests/unit/test_feature_windows.py`
- `tests/integration/test_feature_leakage.py`
- `tests/integration/test_edge_case_scoring.py`

**Acceptance tests:**
- `test_leading_indicators.py::test_email_open_rate_delta_30_90`: known value on a hand-built fixture.
- `test_leading_indicators.py::test_skip_rate_60_vs_prior_60`: known value.
- `test_leading_indicators.py::test_ticket_escalation_score`: known value.
- `test_leading_indicators.py::test_days_since_last_quiz`: known value.
- `test_leading_indicators.py::test_blend_switch_count_90`: known value.
- `test_feature_windows.py::test_each_domain_has_multi_window_feature`: checked from the registry.
- `test_feature_leakage.py::test_post_as_of_events_do_not_change_features`
- `test_edge_case_scoring.py::test_every_edge_case_subscriber_scored`: no quiz, no email, no tickets, single order, new, partial.
- `test_feature_windows.py::test_features_md_matches_registry`

**Covers:** R2.1, R2.2, R2.3, R2.4.

---

## T5 — Runtime config table drives risk tiers (AGENT_CONFIG, snapshots, fail-safe validation)

**Slice:**
- `churn bootstrap` seeds `CHURN_OPS.AGENT_CONFIG` from `config/runtime_defaults.yaml` (A6/A7 defaults).
- At the start of every run, the runner loads and validates it (pydantic `RuntimeConfig`), writes `CONFIG_SNAPSHOTS` and pins the snapshot to the run.
- Scoring assigns the tier from the snapshot boundaries.
- Invalid gate thresholds set mode `approve_all` and mark the run DEGRADED. Invalid tiers or guardrails mark the agent step to halt.

**Files:**
- `config/runtime_defaults.yaml`
- `sql/migrations/V004__runtime_config.sql`
- `src/churn/runtime_config.py`
- `src/churn/model/score.py`
- `src/churn/orchestration/runner.py`
- `src/churn/cli.py`
- `tests/unit/test_runtime_config.py`
- `tests/integration/test_tier_config_change.py`

**Acceptance tests:**
- `test_tier_config_change.py::test_changing_boundary_changes_tier_next_run`: UPDATE AGENT_CONFIG, then the next run reassigns the tier with no code change.
- `test_tier_config_change.py::test_rerun_reuses_pinned_snapshot`: a config edit after the first execution does not affect a rerun of the same run id.
- `test_runtime_config.py::test_invalid_thresholds_yield_approve_all_and_degraded`
- `test_runtime_config.py::test_invalid_tiers_or_guardrails_halt_agent`
- `test_runtime_config.py::test_missing_keys_fall_back_failsafe_not_autonomous`

**Covers:** R3.4, R5.2 (mechanism), PRD §7 config fail-safe.

---

## T6 — Explainable scores: TreeSHAP top factors written with every score

**Slice:** `model.explain` takes LightGBM `pred_contrib` contributions and renders the top-k factors (k=5, at least 3 when enough features are non-missing) as `{feature, label, direction, contribution, value}` through label templates in the feature registry. Scoring merges them into `RISK_SCORES.top_factors`.

**Files:**
- `src/churn/model/explain.py`
- `src/churn/model/score.py`
- `src/churn/features/registry.py`
- `tests/unit/test_explain_consistency.py`
- `tests/integration/test_top_factors_written.py`

**Acceptance tests:**
- `test_explain_consistency.py::test_contributions_plus_bias_equal_margin`: within 1e-6.
- `test_explain_consistency.py::test_factor_direction_matches_sign`
- `test_explain_consistency.py::test_factors_ranked_by_abs_contribution`
- `test_top_factors_written.py::test_every_score_row_has_at_least_3_named_factors`: fewer only when fewer features are non-missing.
- `test_top_factors_written.py::test_factor_labels_human_readable`: no raw feature names left in the label.

**Covers:** R3.2.

---

## T7 — Archetype classifier from weak labels

**Slice:**
- `config/archetype_labels.yaml` maps cancel reason codes and ticket categories to the four archetypes (A3).
- `churn train` also fits a multinomial logistic regression on weak-labelled churned snapshots.
- Scoring writes `archetype`, `archetype_conf`, `archetype_probs` and a low-confidence flag (`min_conf` 0.40, `min_margin` 0.10) for every at-risk subscriber.

**Files:**
- `config/archetype_labels.yaml`
- `src/churn/model/archetype.py`
- `src/churn/model/train.py`
- `src/churn/model/score.py`
- `tests/unit/test_archetype_weak_labels.py`
- `tests/integration/test_archetype_scoring.py`

**Acceptance tests:**
- `test_archetype_weak_labels.py::test_reason_codes_and_ticket_categories_map_to_archetypes`
- `test_archetype_weak_labels.py::test_unmapped_reason_has_no_label`
- `test_archetype_scoring.py::test_every_at_risk_row_has_archetype_and_confidence`
- `test_archetype_scoring.py::test_low_confidence_flag_on_tied_probs`
- `test_archetype_scoring.py::test_archetype_beats_chance_on_latent_truth`: per-archetype precision above 0.25 on synthetic data.

**Covers:** R3.3.

---

## T8 — Model evaluation report and model card with honest target check

**Amended in Revision 2** (delivered, MR !23). The label is unchanged (A1, 30-day voluntary churn), and so are the files.

**Slice:**
- `churn evaluate` splits the monthly snapshots by date into training, a 3-month validation split and the 3-month holdout, and purges snapshots whose label window crosses into the next split (ARCHITECTURE A1, §6.3).
- It fits an evaluation-only model on the training split with `train.fit()`. This model is never registered.
- It chooses the at-risk boundary on the validation split only: the highest threshold with validation recall ≥ 0.65, and no boundary when there are fewer than 20 validation churners.
- On the holdout it computes:
  - the base rate b;
  - top-2% precision, top-decile precision and top-decile lift;
  - pre-cancellation recall at the boundary, and the precision at the boundary, with counts;
  - PR-AUC/ROC-AUC and calibration;
  - per-archetype precision/recall and a confusion matrix against the latent archetype;
  - the ceilings min(1, b/0.10) and min(1, b/0.02), computed from the measured b.
- It writes `EVAL_REPORTS`, `docs/eval-report.md` and `docs/model-card.md`.
- Each R3.6 target is printed as PASS, or as MISS with a stated reason: top-2% precision > 0.50, top-decile lift ≥ 4×, pre-cancellation recall > 0.60.
- The report labels the boundary "recommended" and never writes it to `AGENT_CONFIG`. The report records the scale and row counts, explains why the 0.70 top-decile target was replaced, and keeps the notes that synthetic accuracy is not real-world accuracy and that the 90-day impact target can only be checked after go-live.

**Files:**
- `src/churn/model/evaluate.py`
- `sql/migrations/V005__eval_reports.sql`
- `src/churn/cli.py`
- `docs/eval-report.md`
- `docs/model-card.md`
- `tests/unit/test_evaluate_metrics.py`
- `tests/integration/test_eval_report.py`

**Acceptance tests:**
- `test_evaluate_metrics.py::test_top_share_precision_known_value`: top-2% precision on a hand-built frame.
- `test_evaluate_metrics.py::test_top_decile_lift_known_value`
- `test_evaluate_metrics.py::test_pre_cancellation_recall_uses_last_score_before_cancel`
- `test_evaluate_metrics.py::test_boundary_is_highest_threshold_with_validation_recall_at_least_065`
- `test_evaluate_metrics.py::test_boundary_ignores_holdout_data`
- `test_evaluate_metrics.py::test_too_few_validation_churners_is_recall_miss_with_reason`
- `test_evaluate_metrics.py::test_ceilings_from_measured_base_rate`
- `test_evaluate_metrics.py::test_splits_are_disjoint_ordered_and_purged`
- `test_evaluate_metrics.py::test_missed_target_reported_as_miss_with_reason`
- `test_eval_report.py::test_report_states_label_window_validation_and_holdout`
- `test_eval_report.py::test_report_has_per_archetype_precision`
- `test_eval_report.py::test_report_shows_counts_ceiling_and_recommended_boundary`
- `test_eval_report.py::test_evaluate_does_not_write_agent_config`
- `test_eval_report.py::test_targets_on_medium_synth` (marked slow): asserts top-2% precision > 0.50, top-decile lift ≥ 4× and pre-cancellation recall > 0.60, or xfails with the stated reason.

**Covers:** R3.6, R3.3 (evaluation), R9.2 (model card).

---

## T9 — Structured logging and error taxonomy across the cycle

**Slice:**
- Add structlog JSON logging bound with `run_id`, `component` and `step`, plus the error taxonomy (`SourceUnavailable`, `ContractError`, `ConfigError`, `WarehouseWriteError`, `LLMError`, `GuardrailBlocked`, `ModelError`).
- The runner logs step start and end and classifies failures into `RUNS.status` and `error_class`.
- A cycle with no promoted model fails as `ModelError` with a runbook pointer.
- Zero active subscribers succeeds with a warning.

**Files:**
- `pyproject.toml`
- `src/churn/obs/__init__.py`
- `src/churn/obs/logging.py`
- `src/churn/obs/errors.py`
- `src/churn/orchestration/runner.py`
- `tests/unit/test_logging.py`
- `tests/integration/test_error_classification.py`
- `src/churn/interventions/retention_offer.py` (operator-authorized compatibility repair: selected Action and AgentContext cost callers)
- `tests/unit/test_intervention_types.py` (action-cost regression)
- `tests/integration/test_four_paths_outbox.py` (real active pseudonymous subscriber fixture; stale-source protection retained)

**Acceptance tests:**
- `test_logging.py::test_log_lines_parse_as_json_with_run_id_and_component`
- `test_error_classification.py::test_missing_model_fails_with_model_error`
- `test_error_classification.py::test_zero_active_subscribers_succeeds_with_warning`
- `test_error_classification.py::test_failed_step_records_error_class`
- `test_error_classification.py::test_success_preserves_queued_counts_and_step_order`: returned RunResult and persisted RUNS queued counts equal approval rows; reconcile → approvals → agent order is preserved.
- `test_intervention_types.py::test_offer_cost_accepts_selected_action`: selected offer costs and existing context callers remain compatible.
- `test_four_paths_outbox.py::test_each_type_writes_valid_outbox_row`: all four paths retain payload assertions with real active pseudonymous subscriber keys (operator-authorized repair).

**Covers:** R8.3.

---

## T10 — Agent thin path: at-risk subscriber → check-in email → OUTBOX and INTERVENTION_LOG

**Slice:**
- Add the intervention registry loaded from `config/interventions.yaml` and the first type, `checkin_email`, with template content (subject and body with the `{{first_name}}` merge token).
- `agent.cycle` builds an `AgentContext` per at-risk subscriber from the run's scores and features and produces a decision with `decision_id = sha256(run_id|subscriber_key)`.
- `execution.outbox` MERGEs one insert-only OUTBOX row per decision.
- `INTERVENTION_LOG` gets the full R6.1 schema, with response and outcome set to `PENDING`.
- The runner gains the decide/dispatch step. In this slice every decision is dispatched autonomously; the gate arrives in T14.

**Files:**
- `sql/migrations/V006__intervention_log_outbox.sql`
- `config/interventions.yaml`
- `src/churn/interventions/__init__.py`
- `src/churn/interventions/base.py`
- `src/churn/interventions/checkin_email.py`
- `src/churn/agent/__init__.py`
- `src/churn/agent/context.py`
- `src/churn/agent/content.py`
- `src/churn/agent/cycle.py`
- `src/churn/execution/__init__.py`
- `src/churn/execution/outbox.py`
- `src/churn/orchestration/runner.py`
- `tests/integration/test_agent_thin_path.py`
- `tests/integration/test_outbox_idempotency.py`

**Acceptance tests:**
- `test_agent_thin_path.py::test_at_risk_subscriber_gets_one_outbox_row_and_log_row`
- `test_agent_thin_path.py::test_log_row_has_all_r6_1_fields_or_pending`
- `test_agent_thin_path.py::test_outbox_row_contains_everything_sender_needs`: channel, subject, body, merge token, subscriber_key.
- `test_outbox_idempotency.py::test_replaying_run_creates_no_duplicate_outbox_rows`
- `test_agent_thin_path.py::test_agent_consumes_same_runs_scores`

**Covers:** R4.5, R4.1 (path 1), R6.1 (schema), R3.7 (agent consumes run output).

---

## T11 — Remaining intervention paths: formulation (with quiz fallback), retention offer, escalation

**Slice:**
- `formulation` uses quiz goals and the recommended SKU when `has_quiz`, otherwise the most-reordered blend's goal category and goal-email engagement, and records `data_source`.
- `retention_offer` picks from the A6 offer catalogue and clips to `max_discount_pct`.
- `escalation` sets reason and priority and uses channel `human_queue`.
- Each type has templates and a cost function, is registered in `config/interventions.yaml`, and produces a valid OUTBOX payload.

**Files:**
- `src/churn/interventions/formulation.py`
- `src/churn/interventions/retention_offer.py`
- `src/churn/interventions/escalation.py`
- `config/interventions.yaml`
- `src/churn/agent/content.py`
- `tests/unit/test_intervention_types.py`
- `tests/unit/test_formulation_fallback.py`
- `tests/integration/test_four_paths_outbox.py`

**Acceptance tests:**
- `test_intervention_types.py::test_each_of_four_types_built_from_suitable_profile`
- `test_formulation_fallback.py::test_with_quiz_uses_quiz_source`
- `test_formulation_fallback.py::test_without_quiz_uses_purchase_history_source`
- `test_intervention_types.py::test_offer_never_exceeds_max_discount`
- `test_four_paths_outbox.py::test_each_type_writes_valid_outbox_row`: offer_code/discount for offers, escalation_reason and human_queue for escalation.

**Covers:** R4.1, R4.3.

---

## T12 — Guardrails and coverage gaps

**Slice:**
- `agent.guardrails` removes arms by rule, with each rule read from the run's config snapshot: contact cap (1/14 d, 3/90 d, counted from dispatched and pending log rows), email-unsubscribed (blocks email arms only), active outstanding offer, discount above max, and an existing pending approval (`DUPLICATE_PENDING`).
- Each removal is written to `guardrail_events` with its reason.
- When no arm remains, a `COVERAGE_GAPS` row records the reason.

**Files:**
- `src/churn/agent/guardrails.py`
- `src/churn/agent/cycle.py`
- `sql/migrations/V007__coverage_gaps.sql`
- `tests/unit/test_guardrails.py`
- `tests/integration/test_coverage_gaps.py`

**Acceptance tests:**
- `test_guardrails.py::test_over_frequency_cap_blocked_with_reason`
- `test_guardrails.py::test_unsubscribed_gets_no_email_arm_escalation_still_allowed`
- `test_guardrails.py::test_offer_beyond_limit_clipped_or_blocked`
- `test_guardrails.py::test_active_offer_blocks_new_offer`
- `test_guardrails.py::test_pending_approval_blocks_duplicate`
- `test_coverage_gaps.py::test_no_eligible_arm_writes_coverage_gap_with_reason`

**Covers:** R4.6, R6.5 (gap logging), PRD §7 edge cases.

**Status (2026-10-23):** merged as !29 and shipped in wave 7. Migration is V017. Builder reported 188 passed, 1 deselected, and ruff passing.

---

## T13 — Context-aware selection: contextual Thompson-sampling policy with rationale

**Slice:**
- `agent.policy` draws uplift from `POLICY_STATS` Beta posteriors per (archetype, intervention, tier × tenure bucket), seeded from the priors in `runtime_defaults.yaml`. It scores `P(reach) × uplift × churn_prob × value(tenure) − cost`.
- The mixture over the top-2 archetypes is used at low confidence, escalation is forced when confidence is low and the tier is critical, and exploration is bounded by `max_explore_share`.
- Draws are seeded per `(run_id, subscriber_key)`.
- `agent.rationale` writes structured JSON plus one-line text to every decision.

**Files:**
- `src/churn/agent/policy.py`
- `src/churn/agent/rationale.py`
- `src/churn/agent/cycle.py`
- `sql/migrations/V016__policy_stats.sql`
- `src/churn/runtime_config.py`
- `tests/integration/test_agent_thin_path.py`
- `tests/integration/test_approval_lifecycle.py`
- `tests/integration/test_threshold_change_routing.py`
- `config/runtime_defaults.yaml`
- `tests/unit/test_policy_routing.py`
- `tests/unit/test_policy_determinism.py`
- `tests/unit/test_rationale.py`

**Acceptance tests:**
- `test_policy_routing.py::test_labelled_routing_scenarios_pick_expected_intervention`: price → offer, efficacy → formulation, fatigue → check-in, high-value life change → escalation.
- `test_policy_routing.py::test_same_score_different_archetype_or_tenure_different_action`
- `test_policy_routing.py::test_low_confidence_critical_forces_escalation`
- `test_policy_routing.py::test_not_a_score_threshold_rule`: varying only the score across a threshold does not alone determine the arm.
- `test_policy_determinism.py::test_same_run_and_key_same_choice`
- `test_policy_routing.py::test_explore_share_bounded`
- `test_rationale.py::test_every_decision_has_structured_and_text_rationale`

**Status (2026-10-23):** T13 merged as !26 and shipped in wave 6. Ticket audit reports all named acceptance tests and safety regressions passing: 38 targeted tests passed. Builder reported 167 passed / 1 deselected and lint passing. Integration updates preserve contextual-action assertions and the approved human-queue escalation bypass, not an every-action-is-email assumption. V016 follows ARCHITECTURE §5.3; POLICY_STATS starts empty and missing observations use pinned priors.

**Covers:** R4.2, PRD §7 low-confidence archetype.

---

## T14 — Threshold gate and Snowflake approval table lifecycle

**Slice:**
- `agent.gate` routes decisions with `risk ≥ T_risk`, `cost ≥ T_cost`, `discount ≥ T_discount` or an always-approve type to `APPROVALS` (PENDING, `expires_at`, `payload_hash`) with no outbox row. The rest go to the outbox. Thresholds come from the run snapshot, and in `approve_all` mode everything goes to approval.
- `execution.approvals.process` runs at the start of each agent step:
  - It normalises `DECISION`.
  - `APPROVED`: re-checks eligibility, then writes exactly one outbox row, or `CANCELLED_STALE`.
  - `REJECTED`: logs only.
  - Past expiry: `EXPIRED`.
  - Any other value: `INVALID_VALUE`.
  - Edits after processing: `LATE_EDIT`. Changed context: `TAMPERED`.
  - It copies approver identity and time.
- At dispatch, a subscriber who has cancelled since scoring gets `CANCELLED_STALE`.

**Files:**
- `src/churn/agent/gate.py`
- `src/churn/execution/approvals.py`
- `src/churn/agent/cycle.py`
- `sql/migrations/V009__approvals.sql`
- `tests/unit/test_gate.py`
- `tests/integration/test_approval_lifecycle.py`
- `tests/integration/test_threshold_change_routing.py`

**Acceptance tests:**
- `test_gate.py::test_above_threshold_pending_no_outbox_below_threshold_outbox`
- `test_gate.py::test_approve_all_mode_routes_everything_to_approval`
- `test_threshold_change_routing.py::test_editing_agent_config_threshold_changes_next_run_routing`
- `test_approval_lifecycle.py::test_approved_creates_exactly_one_outbox_row_next_cycle`
- `test_approval_lifecycle.py::test_rejected_creates_none_and_logs_approver`
- `test_approval_lifecycle.py::test_expired_after_expiry_no_outbox`
- `test_approval_lifecycle.py::test_invalid_value_stays_pending_logged`
- `test_approval_lifecycle.py::test_late_edit_ignored`
- `test_approval_lifecycle.py::test_tampered_context_treated_as_rejected`
- `test_approval_lifecycle.py::test_cancelled_between_scoring_and_dispatch_not_sent`

**Covers:** R5.1, R5.2, R5.3, R5.4, PRD §7 cancel/reactivate and approval edge cases.

---

## T15 — LLM-personalised content with a PII-free prompt and template fallback

**Slice:**
- Add the `LLMClient` protocol with Anthropic (default Haiku-class), OpenAI and Fake adapters, plus a per-run call budget.
- `agent.content` builds prompts only from the whitelisted `PromptContext` dataclass and validates the output (length, `{{first_name}}` token, no email/phone patterns, offer terms match the params).
- It retries with tenacity. On error, timeout, rate limit, validation failure or exhausted budget it falls back to the template and logs `content_source`.
- Generated content is persisted in INTERVENTION_LOG before dispatch and reused on rerun.

**Files:**
- `pyproject.toml`
- `config/settings.yaml`
- `src/churn/llm/__init__.py`
- `src/churn/llm/client.py`
- `src/churn/llm/anthropic.py`
- `src/churn/llm/openai.py`
- `src/churn/llm/fake.py`
- `src/churn/llm/budget.py`
- `src/churn/agent/content.py`
- `tests/unit/test_prompt_whitelist.py`
- `tests/unit/test_content_validator.py`
- `tests/integration/test_llm_fallback.py`

**Acceptance tests:**
- `test_prompt_whitelist.py::test_no_raw_pii_fields_in_llm_request`: FakeLLM captures the requests, which are checked against the synthetic customers_pii values.
- `test_prompt_whitelist.py::test_prompt_context_has_no_free_form_or_id_fields`
- `test_content_validator.py::test_rejects_email_phone_patterns_and_mismatched_offer`
- `test_llm_fallback.py::test_llm_timeout_falls_back_to_template_and_logs_source`
- `test_llm_fallback.py::test_budget_exhausted_uses_template`
- `test_llm_fallback.py::test_rerun_reuses_persisted_content_no_new_llm_calls`

**Covers:** R4.4, R7.2, R8.1 (LLM failure).

**Status (2026-10-23):** merged as !30 and shipped in wave 7. A missing provider key or SDK stays on the template. Builder reported 194 passed, 1 deselected, and ruff passing.

---

## T16 — Holdout, simulated responses and outcome tracking with treated-vs-control report

**Slice:**
- `feedback.holdout` assigns 10% of at-risk subscribers to control by deterministic hash (logged in `HOLDOUT_ASSIGNMENTS`). Control members get a `HOLDOUT` decision with no action.
- `synth.responder` reads new outbox rows and appends simulated opens, redemptions and cancellations into `THESIS_SYNTH`.
- `feedback.outcomes` closes decisions after the 30-day window through the contract views (response, plus outcome of retained, churned or unknown) into `OUTCOMES` and `INTERVENTION_LOG`.
- `churn simulate --days N` advances the clock day by day.
- `sql/reports/impact.sql` computes the churn rate of treated vs control.

**Files:**
- `src/churn/feedback/__init__.py`
- `src/churn/feedback/holdout.py`
- `src/churn/feedback/outcomes.py`
- `src/churn/synth/responder.py`
- `src/churn/agent/cycle.py`
- `src/churn/orchestration/runner.py`
- `src/churn/cli.py`
- `sql/migrations/V010__outcomes_holdout.sql`
- `sql/reports/impact.sql`
- `tests/unit/test_holdout.py`
- `tests/integration/test_outcomes_window.py`
- `tests/integration/test_impact_report.py`

**Acceptance tests:**
- `test_holdout.py::test_holdout_share_matches_config_and_is_deterministic`
- `test_outcomes_window.py::test_after_window_every_intervention_has_outcome`: retained, churned or unknown.
- `test_outcomes_window.py::test_before_window_outcome_stays_pending`
- `test_outcomes_window.py::test_response_from_email_events_and_redemptions`
- `test_impact_report.py::test_impact_sql_returns_treated_and_control_churn_rate`
- `test_holdout.py::test_control_members_get_no_outbox_row`

**Covers:** R6.2, R6.6.

**Status (2026-10-23):** merged as !35 and shipped in wave 8. The migration is V019, not V010. The assignment column is `arm_group`. Builder reported 209 passed, 3 deselected, and ruff passing. A synthetic treated-versus-control rate is not the 90-day impact target.

---

## T17 — Feedback into selection: outcomes update policy posteriors

**Slice:**
- `feedback.policy_stats` updates the Beta posteriors per (archetype, intervention, bucket) from newly closed treated outcomes, with optional decay and idempotency per decision.
- The runner gains a `policy_update` step after outcomes. Posteriors are pinned per run for the decide step.

**Files:**
- `src/churn/feedback/policy_stats.py`
- `src/churn/orchestration/runner.py`
- `tests/unit/test_policy_stats_update.py`
- `tests/integration/test_policy_shift.py`

**Acceptance tests:**
- `test_policy_stats_update.py::test_retained_increments_alpha_churned_increments_beta`
- `test_policy_stats_update.py::test_same_outcome_not_counted_twice`
- `test_policy_shift.py::test_selection_shifts_toward_better_intervention`: formulation retains 80% vs check-in 20% for efficacy, and formulation's share rises over later simulated runs.

**Covers:** R6.4.

**Status (2026-10-23):** merged as !37 and shipped in wave 9. No new migration. The stored posterior includes the prior, and `policy.decay` defaults to 1. Builder reported 214 passed, 3 deselected, 1 warning, and ruff passing. A shift test starts from a flat prior. There is no applied-decision ledger.

---

## T18 — Feedback into scoring: retrain with intervention history and promotion gate

**Slice:**
- Intervention-history features (`n_interventions_90d`, `last_intervention_type`, `days_since_last_intervention`) come from INTERVENTION_LOG, and treated rows carry their observed outcome label.
- `churn retrain` builds a versioned candidate and promotes it only if top-decile precision and PR-AUC on the same holdout are at least the current model's minus 0.01. Otherwise it is rejected, with the metrics recorded in `MODEL_REGISTRY`.
- Retrain is triggered by schedule, by `retrain.min_new_outcomes`, or by the CLI.

**Files:**
- `src/churn/feedback/retrain.py`
- `src/churn/model/registry.py`
- `src/churn/model/dataset.py`
- `sql/features/interventions.sql`
- `src/churn/features/registry.py`
- `src/churn/cli.py`
- `tests/unit/test_promotion_rule.py`
- `tests/integration/test_retrain_promotion.py`

**Acceptance tests:**
- `test_retrain_promotion.py::test_retrain_consumes_intervention_outcomes_and_versions_model`
- `test_retrain_promotion.py::test_worse_candidate_rejected_current_kept`
- `test_promotion_rule.py::test_promote_when_within_tolerance`
- `test_retrain_promotion.py::test_registry_records_metrics_and_reason`
- `test_retrain_promotion.py::test_intervention_history_features_present`

**Covers:** R6.3, PRD §7 retrain-worse edge case.

**Status (2026-10-23):** merged as !38 and shipped in wave 9. Builder reported 215 passed, 3 deselected, 1 warning, and ruff passing. That branch was cut at T16, so the count does not include T25 or T17. A one-row move on the tiny holdout can exceed the 0.01 tolerance, and the rejection keeps the promoted model.

---

## T19 — Run metrics: coverage, autonomy rate, audit completeness, evaluation run

**Slice:**
- `feedback.metrics` computes per run and stores in `RUN_METRICS`: coverage = (dispatched + queued) / (at-risk − holdout), autonomy rate, audit completeness, LLM calls/fallbacks, duration, and treated-vs-control churn.
- The runner adds the metrics step and fills the RUNS counts.
- A slow evaluation test runs the medium synth for 45 simulated days.

**Files:**
- `src/churn/feedback/metrics.py`
- `sql/migrations/V011__run_metrics.sql`
- `src/churn/orchestration/runner.py`
- `tests/unit/test_metrics.py`
- `tests/integration/test_audit_completeness.py`
- `tests/integration/test_evaluation_run.py`

**Acceptance tests:**
- `test_metrics.py::test_coverage_excludes_holdout_from_denominator`
- `test_metrics.py::test_autonomy_rate_formula`
- `test_audit_completeness.py::test_every_intervention_has_complete_log_row`: 100% completeness.
- `test_evaluation_run.py::test_coverage_above_85_percent` (slow)
- `test_evaluation_run.py::test_autonomy_above_70_percent_with_default_thresholds` (slow)

**Covers:** R5.5, R6.1, R6.5.

---

## T20 — Failure recovery: step/shard checkpoints, run lock, fault injection

**Slice:**
- The runner records `RUN_STEPS` per step and shard, skips completed work on rerun, and pins model version, config snapshot and posteriors.
- `orchestration.lock` takes the `RUN_LOCKS` row by MERGE with a heartbeat and takes over stale locks. A concurrent cycle exits as skipped.
- Source reads retry with backoff, then fail as `SourceUnavailable`.
- The agent's decide step runs per shard.
- A `FaultyWarehouse` test wrapper injects failures.

**Files:**
- `src/churn/orchestration/runner.py`
- `src/churn/orchestration/lock.py`
- `src/churn/agent/cycle.py`
- `sql/migrations/V012__run_steps_locks.sql`
- `tests/fixtures/faulty_warehouse.py`
- `tests/integration/test_fault_injection.py`
- `tests/integration/test_run_lock.py`

**Acceptance tests:**
- `test_fault_injection.py::test_source_unavailable_then_rerun_recovers`
- `test_fault_injection.py::test_write_failure_mid_batch_no_lost_no_duplicate_rows`
- `test_fault_injection.py::test_llm_outage_mid_run_recovers`
- `test_fault_injection.py::test_kill_between_steps_resumes_from_checkpoint`
- `test_fault_injection.py::test_cycle_twice_identical_counts_and_keys`
- `test_run_lock.py::test_concurrent_cycle_skipped`
- `test_run_lock.py::test_stale_lock_taken_over`

**Covers:** R8.1, R8.2.

---

## T21 — FastAPI service: health, readiness, trigger and inspect runs

**Slice:**
- `api/app.py` with `X-API-Key` auth exposes:
  - `GET /health` and `GET /ready` (warehouse reachable, migrations current, promoted model);
  - `POST /runs` (starts a run in a background thread guarded by the lock, returns run_id);
  - `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/metrics` and `GET /config/runtime`.
- `churn serve` starts uvicorn.

**Files:**
- `pyproject.toml`
- `src/churn/api/__init__.py`
- `src/churn/api/app.py`
- `src/churn/cli.py`
- `tests/integration/test_api.py`

**Acceptance tests:**
- `test_api.py::test_health_returns_ok`
- `test_api.py::test_triggered_run_appears_in_run_log`
- `test_api.py::test_missing_api_key_rejected`
- `test_api.py::test_ready_reports_missing_model`
- `test_api.py::test_runtime_config_endpoint_shows_validation_errors`

**Covers:** R8.6.

---

## T22 — Scheduled operation: worker with daily cycle, retrain cron and catch-up

**Slice:**
- `churn worker` runs an APScheduler `BlockingScheduler` with `schedule.cron` (daily 06:00 UTC) and `schedule.retrain_cron`.
- On start it catches up once if today's run is missing or unfinished.
- Overlap is prevented by the warehouse lock.
- Two consecutive scheduled runs happen without manual action, and each run's agent step consumes that run's scores.

**Files:**
- `pyproject.toml`
- `config/settings.yaml`
- `src/churn/orchestration/scheduler.py`
- `src/churn/cli.py`
- `tests/unit/test_scheduler.py`
- `tests/integration/test_consecutive_runs.py`

**Acceptance tests:**
- `test_consecutive_runs.py::test_two_scheduled_runs_without_manual_action`: simulated clock.
- `test_consecutive_runs.py::test_agent_consumes_each_runs_scores`
- `test_scheduler.py::test_catch_up_once_when_todays_run_missing`
- `test_scheduler.py::test_retrain_job_registered_from_settings`

**Covers:** R3.7, R6.3 (trigger).

---

## T23 — Containerised deployment and deployment guide (operator-verified)

**Slice:**
- One `python:3.12-slim` non-root image is used by the `api` and `worker` compose services, configured through env (`.env.example` documents every variable, including the Snowflake key pair, pepper, API key and `CHURN_BACKEND`).
- `docs/deployment.md` covers a clean-machine deploy and the "Switch to production views" section, which names each contract item with config-only steps.
- R8.7 end-to-end is verified by Andre (no Docker in the sandbox).

**Files:**
- `docker/Dockerfile`
- `docker-compose.yml`
- `.env.example`
- `docs/deployment.md`
- `tests/unit/test_deploy_artifacts.py`

**Acceptance tests:**
- `test_deploy_artifacts.py::test_compose_parses_and_defines_api_and_worker`
- `test_deploy_artifacts.py::test_dockerfile_non_root_and_python_312`
- `test_deploy_artifacts.py::test_env_example_lists_every_settings_env_var`
- `test_deploy_artifacts.py::test_switch_section_names_every_contract_view_and_no_code_steps`
- Operator check: following `docs/deployment.md` on a clean machine with Snowflake credentials gives a successful cycle.

**Covers:** R8.7, R1.4.

---

## T24 — Snowflake backend, bootstrap of schemas/roles, and SQL portability check

**Slice:**
- `warehouse/snowflake.py` implements the protocol: `write_pandas` into a temp table, then MERGE, with env-only credentials.
- `churn bootstrap` creates `THESIS_SYNTH`, `CHURN_OPS` and `CHURN_PRIVATE`, the roles `CHURN_APP`, `CHURN_ANALYST`, `CHURN_APPROVER` (UPDATE on APPROVALS only) and `CHURN_SENDER`, and the `OUTBOX_FOR_SENDER` view.
- Every `sql/` file is transpiled in a test.
- A live smoke test is marked `snowflake` and skipped without credentials.

**Files:**
- `pyproject.toml`
- `src/churn/warehouse/snowflake.py`
- `src/churn/warehouse/bulk.py`
- `sql/bootstrap/roles_and_grants.sql`
- `sql/migrations/V013__sender_view.sql`
- `src/churn/cli.py`
- `tests/unit/test_sql_transpile.py`
- `tests/unit/test_snowflake_merge_sql.py`
- `tests/snowflake/test_snowflake_smoke.py`

**Acceptance tests:**
- `test_sql_transpile.py::test_every_sql_file_transpiles_to_duckdb`
- `test_snowflake_merge_sql.py::test_generated_merge_is_keyed_and_idempotent`
- `test_snowflake_merge_sql.py::test_credentials_only_from_env`
- `test_snowflake_smoke.py::test_migrate_and_small_cycle` (marker snowflake, skipped without SNOWFLAKE_* env)

**Covers:** R1.5, A10.

---

## T25 — PII proof: log redaction, full-cycle PII scan, no external API clients

**Slice:**
- A structlog processor redacts email and phone patterns as a backstop.
- A full simulated cycle is regex-scanned: captured logs, FEATURES, the training frame, RISK_SCORES, INTERVENTION_LOG, OUTBOX, APPROVALS and model artefact bytes are checked for emails, phones, street addresses and every synthetic first/last name.
- Code checks assert that no pipeline module reads `customers_pii` and that no HTTP client other than the LLM SDKs is imported (no Shopify, Klaviyo or Zendesk calls).

**Files:**
- `src/churn/obs/logging.py`
- `tests/integration/test_pii_scan.py`
- `tests/unit/test_pii_code_paths.py`
- `tests/unit/test_no_external_apis.py`

**Acceptance tests:**
- `test_pii_scan.py::test_no_pii_in_logs_tables_or_model_artefacts`
- `test_pii_code_paths.py::test_customers_pii_never_selected_by_pipeline_sql_or_code`
- `test_no_external_apis.py::test_no_shopify_klaviyo_zendesk_or_http_clients`
- `test_pii_scan.py::test_log_redaction_processor_masks_email_and_phone`

**Covers:** R7.1, R4.4, R1.5.

**Status (2026-10-23):** merged as !36 and shipped in wave 8. The redaction processor was already T9; this ticket added the proof. Builder reported 213 passed, 3 deselected, 1 warning, and ruff passing. A passing scan on the tiny draw is not a live-data proof.

---

## T26 — Extensibility: module boundaries and a dummy intervention added through config only

**Slice:**
- The intervention registry loads types by dotted path from `config/interventions.yaml`.
- `tests/fixtures/dummy_intervention.py`, added through config and a prior row only, is chosen and dispatched end to end.
- An AST-based boundary test enforces the import directions of ARCHITECTURE §3.1.
- `docs/extending.md` explains how to add an intervention type and a data source.

**Files:**
- `src/churn/interventions/base.py`
- `tests/fixtures/dummy_intervention.py`
- `tests/integration/test_extensibility.py`
- `tests/unit/test_boundaries.py`
- `docs/extending.md`

**Acceptance tests:**
- `test_extensibility.py::test_dummy_intervention_dispatched_via_config_only`
- `test_boundaries.py::test_model_does_not_import_agent_or_execution`
- `test_boundaries.py::test_agent_does_not_import_execution_outbox`
- `test_boundaries.py::test_execution_does_not_import_agent_policy`
- `test_boundaries.py::test_synth_imports_only_warehouse_and_contract`

**Covers:** R8.5.

**Status (2026-10-23):** merged as !31 and shipped in wave 7. Builder reported 199 passed, 1 deselected, and ruff passing.

---

## T27 — Full-scale throughput: 200k actives, 7.5M+ quiz rows, timed cycle

**Slice:**
- The generator writes A5 scale (`synth.scale=full`: 200k active, about 350k total, at least 7.5M quiz rows) in chunks.
- `churn cycle --timed` records the duration in RUNS and RUN_METRICS.
- A `scale`-marked test generates full-scale data on DuckDB, runs one end-to-end cycle, asserts it finishes within the 24 h interval and records the duration for the test-results report.

**Files:**
- `src/churn/synth/generator.py`
- `src/churn/cli.py`
- `tests/scale/test_full_cycle_timed.py`

**Acceptance tests:**
- `test_full_cycle_timed.py::test_full_scale_volumes` (scale): at least 200k active and at least 7.5M quiz rows.
- `test_full_cycle_timed.py::test_full_cycle_within_interval_and_duration_recorded` (scale)

**Covers:** R8.4, R1.2 (volume).

**Status (2026-10-23):** merged as !32 and shipped in wave 7. On this machine the full draw had 215,649 active subscribers and 19,883,149 quiz rows, and `churn cycle --timed` finished SUCCEEDED in 54.67s. Builder reported 199 passed, 3 deselected (the slow test and the two scale tests), and ruff passing.

---

## T28 — Documentation set, test-results report and submission deliverables

**Slice:**
- Write the documents:
  - `docs/output-tables.md`: every CHURN_OPS table and column, the outbox sender contract, and example Metabase queries including score history;
  - `docs/agent-logic.md`;
  - `docs/runbook.md`: operations, Metabase approval steps and failure playbooks;
  - `docs/decisions.md`: A1–A12 for Andre to confirm;
  - `docs/ai-usage-log.md`;
  - `docs/demo-script.md`;
  - `README.md`: entry point.
- `churn report-tests` turns JUnit XML into `docs/test-results.md`, including the slow and scale metrics when present.
- CI publishes it.
- A docs test checks that every table in the migrations and every runtime config key is documented.

**Files:**
- `docs/output-tables.md`
- `docs/agent-logic.md`
- `docs/runbook.md`
- `docs/decisions.md`
- `docs/ai-usage-log.md`
- `docs/demo-script.md`
- `docs/test-results.md`
- `README.md`
- `src/churn/reporting.py`
- `src/churn/cli.py`
- `.gitlab-ci.yml`
- `tests/unit/test_docs_coverage.py`
- `tests/unit/test_report_tests.py`

**Acceptance tests:**
- `test_docs_coverage.py::test_every_ops_table_documented`
- `test_docs_coverage.py::test_every_runtime_config_key_documented`
- `test_docs_coverage.py::test_required_documents_exist`: architecture, data contract, model card, agent logic, output tables, runbook, deployment, AI usage log, demo script.
- `test_report_tests.py::test_junit_xml_rendered_to_test_results_md`
- Operator check: Andre records the demo video from `docs/demo-script.md` (A9).

**Covers:** R9.1, R9.2, R9.3.

---

## T29 — Rebuild the full synthetic source on main and map every contract view (supersedes T2)

**Slice:**
- Rebuild T2's slice (T2 above is the spec) on top of current main.
  - Check first that T2's files are absent from main and that T7's interim `latent.py` / `latent_archetypes()` is present.
  - Keep the `latent_archetypes(settings, scale)` interface and T7's RNG streams, so each customer's latent archetype is unchanged (ARCHITECTURE §5.2, as-built note).
  - Replace the interim emission model with one per-archetype model that drives all eight contract views (subscription_events, products, email_events, support_tickets, quiz_responses, customers_pii, plus subscriptions and orders).
  - Add a `THESIS_SYNTH.latent_truth` table, noisy reason codes and ticket categories, an independent involuntary-churn hazard and injected edge cases.
- `churn synth --profile` writes `docs/data-profile.md` and `CHURN_OPS.DATA_PROFILE`.
- `config/source_contract.yaml` maps all eight views for the synthetic schema using T4's column names. `customers_pii` gets `pii: true`, so it is validated but never rendered.
- The migrations take the next free numbers. MR !5 already used V002, so these are `sql/synth/V003` and `sql/migrations/V015`, re-checked on main before use.
- The ticket re-checks T7's `test_archetype_beats_chance_on_latent_truth` at its unchanged 0.25 threshold. If it fails, the generator is tuned, not the test.

**Files:**
- `config/source_contract.yaml`
- `sql/synth/V003__synth_all_domains.sql`
- `sql/migrations/V015__data_profile.sql`
- `src/churn/synth/generator.py`
- `src/churn/synth/latent.py`
- `src/churn/synth/edge_cases.py`
- `src/churn/synth/profile.py`
- `src/churn/contract/loader.py`
- `src/churn/contract/views.py`
- `src/churn/cli.py`
- `docs/data-profile.md`
- `docs/data-contract.md`
- `tests/unit/test_synth_latent.py`
- `tests/unit/test_synth_import_boundary.py`
- `tests/integration/test_synth_profile.py`
- `tests/unit/test_contract_pii.py`
- `tests/integration/test_contract_all_views.py`
- `tests/integration/test_contract_swap.py`
- `docs/plans/churn-prediction/tickets/T29.md`

**Acceptance tests:**
- `test_synth_profile.py::test_every_domain_has_view`: each R1.1 domain has at least one populated contract view.
- `test_synth_profile.py::test_edge_case_shares_above_zero`: the profile shows a share above 0% for every edge-case category.
- `test_synth_profile.py::test_churn_events_span_label_and_eval_window`: voluntary cancellations cover the 30-day horizon plus the 3-month validation and 3-month holdout.
- `test_synth_latent.py::test_deterministic_for_seed`: the same seed gives identical output.
- `test_synth_latent.py::test_reason_codes_follow_latent_archetype_with_noise`: about 25% noise.
- `test_synth_latent.py::test_latent_truth_table_matches_latent_archetypes`: `latent_truth` equals `latent_archetypes(settings, scale)`, and each customer's archetype is unchanged from the interim model for the same seed.
- `test_synth_import_boundary.py::test_synth_does_not_import_features_or_model`
- `test_contract_all_views.py::test_all_eight_views_mapped_and_validate`: the contract maps exactly the eight views, and `contract.validate` passes on synthetic data.
- `test_contract_all_views.py::test_no_stub_cte_used_with_default_contract`
- `test_contract_all_views.py::test_every_feature_domain_populated_on_synthetic_data`: every registry `has_<domain>` feature is 1 for at least one subscriber, and `orders_total > 0` for at least one.
- `test_contract_pii.py::test_pii_view_never_rendered`: neither the feature SQL nor the key-map SQL contains `customers_pii`, and `view_cte` on it raises `ContractError`.
- `test_contract_swap.py::test_second_schema_by_config_only`: still passes with every mapped view copied to the second schema; the assertion is unchanged.
- `test_archetype_scoring.py::test_archetype_beats_chance_on_latent_truth`: re-run unchanged (threshold 0.25) against the full model and passes.
- The full suite and ruff pass, and no migration number is reused.

**Status (2026-10-23):** T29 merged as !27 and shipped in wave 6. All named acceptance tests passed according to the ticket audit, including unchanged T7 precision >0.25 for every archetype. Auditor's full default suite: 163 passed / 1 deselected; ruff passed. Slow/scale/snowflake tests were excluded. T2 remains superseded; full-scale volume/throughput stays with T27, full-source re-evaluation with T30, and live Snowflake verification with A10.

**Covers:** R1.1, R1.2 (content; full scale in T27), R1.6, R1.7.

---

## T30 — Re-evaluate on the full synthetic data and regenerate the eval report and model card

**Slice:**
- Check first that main contains T8 and T29.
- Extend `model/evaluate.py` additively with a data-provenance section: the mapped contract views (all eight), the synth scale and seed, the snapshot and holdout row counts, and a feature-domain coverage table (the share of holdout rows with each `has_<domain> = 1` and with `orders_total > 0`).
- Regenerate the synthetic data with every view mapped, at T8's scale or larger and at least `small`, and rerun `churn evaluate`.
- Commit the regenerated `docs/eval-report.md` and `docs/model-card.md`. Every R3.6 target is PASS, or MISS with its reason (D9).

**Files:**
- `src/churn/model/evaluate.py`
- `docs/eval-report.md`
- `docs/model-card.md`
- `tests/integration/test_eval_full_source.py`
- `docs/plans/churn-prediction/tickets/T30.md`

**Acceptance tests:**
- `test_eval_full_source.py::test_report_states_full_source_all_eight_views`: the provenance section lists the eight mapped views and the scale.
- `test_eval_full_source.py::test_domain_coverage_every_domain_populated`: every `has_<domain>` share and the orders share are above 0.
- `test_eval_full_source.py::test_committed_report_has_every_r3_6_line`: the committed report has top-2% precision, top-decile lift, recall and precision at the validation-chosen boundary, the ceiling section and a PASS/MISS line per target. Its holdout row count is at least T8's, as recorded in `tickets/T30.md`.
- The committed `docs/model-card.md` keeps the notes that synthetic accuracy is not real-world accuracy and that the 90-day impact target can only be checked after go-live.

**Covers:** R3.8, R3.6 (on the full data).

**Status (2026-10-23):** merged as !33 and shipped in wave 7. The committed rerun is 36,000 customers and 63,166 holdout rows. All three R3.6 targets PASS. Builder reported 202 passed, 1 deselected, and ruff passing.

---

## Requirement coverage

| Req | Tickets |
|---|---|
| R1.1 | T29 (supersedes T2) |
| R1.2 | T29 (supersedes T2), T27 |
| R1.3 | T3 |
| R1.4 | T3, T23 |
| R1.5 | T24, T25 |
| R1.6 | T29 |
| R1.7 | T29 |
| R2.1–R2.4 | T4 |
| R3.1 | T1 |
| R3.2 | T6 |
| R3.3 | T7, T8 |
| R3.4 | T5 |
| R3.5 | T1 |
| R3.6 | T8 (amended), T30 |
| R3.7 | T10, T22 |
| R3.8 | T30 |
| R4.1 | T10, T11 |
| R4.2 | T13 |
| R4.3 | T11 |
| R4.4 | T15, T25 |
| R4.5 | T10 |
| R4.6 | T12 |
| R5.1–R5.4 | T14 (R5.2 mechanism in T5) |
| R5.5 | T19 |
| R6.1 | T10, T19 |
| R6.2 | T16 |
| R6.3 | T18, T22 |
| R6.4 | T17 |
| R6.5 | T12, T19 |
| R6.6 | T16 |
| R7.1 | T3, T25 |
| R7.2 | T15 |
| R8.1–R8.2 | T20 (base in T1, LLM in T15) |
| R8.3 | T9 |
| R8.4 | T27 |
| R8.5 | T26 |
| R8.6 | T21 |
| R8.7 | T23 (operator-verified) |
| R9.1 | T1, T28 |
| R9.2 | T28 (plus T3, T4, T8, T23, T26 docs) |
| R9.3 | T28 (video by Andre, A9) |

## Shared files (must not share a wave)

| File | Tickets |
|---|---|
| `src/churn/orchestration/runner.py` | T1, T3, T5, T9, T10, T16, T17, T19, T20 |
| `src/churn/cli.py` | T1, T2, T5, T8, T16, T18, T21, T22, T24, T27, T28, T29 |
| `src/churn/agent/cycle.py` | T10, T12, T13, T14, T16, T20 |
| `src/churn/model/score.py` | T1, T5, T6, T7 |
| `src/churn/features/registry.py` | T1, T4, T6, T18 |
| `pyproject.toml` | T1, T9, T15, T21, T22, T24 |
| `config/settings.yaml` | T1, T15, T22 |
| `config/source_contract.yaml` | T1, T2, T29 |
| `config/interventions.yaml` | T10, T11 |
| `config/runtime_defaults.yaml` | T5, T13 |
| `src/churn/agent/content.py` | T10, T11, T15 |
| `src/churn/synth/generator.py`, `src/churn/synth/latent.py` | T1, T2, T29 (generator also T27) |
| `src/churn/contract/views.py`, `src/churn/contract/loader.py` | T1, T3, T29 |
| `tests/integration/test_contract_swap.py` | T3, T29 |
| `docs/data-contract.md` | T3, T29 |
| `src/churn/model/evaluate.py`, `docs/eval-report.md`, `docs/model-card.md` | T8, T30 |
| `synth/edge_cases.py`, `synth/profile.py`, `docs/data-profile.md`, T2's three test files | T2, T29 |
| `src/churn/settings.py` | T1, T3 |
| `src/churn/model/train.py` | T1, T7 |
| `src/churn/model/registry.py`, `src/churn/model/dataset.py` | T1, T18 |
| `src/churn/features/builder.py` | T1, T4 |
| `sql/features/subscriptions.sql` | T1, T4 |
| `src/churn/warehouse/bulk.py` | T1, T24 |
| `src/churn/interventions/base.py` | T10, T26 |
| `src/churn/obs/logging.py` | T9, T25 |
| `.gitlab-ci.yml` | T1, T28 |
