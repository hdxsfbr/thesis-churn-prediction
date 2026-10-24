## Rules for parallel ticket runs (read before every ticket)

- Doc Auditor and Builder in a ticket run must NOT edit `docs/plans/<feature>/ARCHITECTURE.md`, `PRD.md`, `TASKS.md` or `WAVES.md`. Put every as-built note, deviation and follow-up for the ticket only in `docs/plans/<feature>/tickets/<ticket>.md` under a heading "Proposed ARCHITECTURE notes". Only plan revisions and the wave wrap-up (Ship) run apply such notes to the shared plan documents.
- Before building, check that main actually contains every ticket this one depends on (per WAVES.md/TASKS.md). If a dependency's files are missing, stop and escalate instead of re-creating another ticket's files.
- Only change files listed in your ticket's slice (plus the minimum needed in shared files). Never delete or rewrite files that belong to other tickets; if a file outside your slice looks wrong or missing, report it, do not fix it.
- Shared hot files (`src/churn/cli.py`, `src/churn/orchestration/runner.py`, `tests/conftest.py`): keep edits small and additive (new functions/commands appended, no reordering or reformatting) to keep merges clean.
- Migrations: use the next free version number on main at build time; never edit a migration that already shipped.
- Code Reviewer and Doc Auditor review by reading. They must NOT run the test suite, the slow tests, `churn` commands, or create/install Python environments. The Builder runs ruff and the full suite and reports the results; CI on the merge request is the test gate.
- Reviewers may use cheap read-only inspection to verify the working tree matches the Builder's report: `git status`, `git diff`, `ls`, `grep`, `cat`. Use these to confirm files were really added, changed or deleted as claimed.
- If a reviewer believes a test result is wrong or missing, say so in the review and send it back to the Builder; do not run it yourself.
- Builder: when a round only changes Markdown files, do not rerun the full suite or slow tests; say "docs-only round, tests not rerun" in your summary.

## Lessons

### churn-prediction, wave 5 (2026-10-22)

- When changing runner logging or gate wiring, retain a regression asserting returned RunResult and persisted RUNS queued counts equal approval rows, and reconcile → approvals → agent step order. Test retention-offer cost with the selected Action as well as AgentContext; four-path fixtures must use real active pseudonymous subscriber keys without weakening stale-source checks.
- For T30, compare full-source evaluation against T8's small-scale baseline of 55,536 holdout rows; preserve validation-only boundary selection and the 30-day split purge. Assert validation recall and holdout recall/precision in both report and model card, not just the report; synthetic PASS is not evidence of real-world accuracy or the 90-day impact target.

### churn-prediction, wave 1 (2026-10-20)

- cli.py (11 tickets) and orchestration/runner.py (9) are shared hot files; check TASKS.md "Shared files" before building a ticket and keep edits to those files minimal and additive to avoid merge conflicts.
- LightGBM needs libgomp1 at runtime: .gitlab-ci.yml installs it via apt-get and runs `ruff check src tests` plus pytest. Run ruff and pytest locally before opening a MR, and keep libgomp1 in any new image (T23 Dockerfile).
- T1 created the skeleton (warehouse protocol, MERGE writer, migrations, RISK_SCORES, RUNS) that all later tickets build on. Reuse these abstractions and add new migrations with the next version numbers rather than changing T1 interfaces.

### churn-prediction, wave 2 (2026-10-20)

- Wave 2 (T2 synth, T3 contract validation, T6 TreeSHAP) built on the wave 1 skeleton: keep validation in contract/validate.py and extend the contract views additively; check the T3 contract layout notes before touching loader or views.
- Before opening a MR run `ruff check src tests` and pytest with LD_LIBRARY_PATH=/workspace/runs/.libs (libgomp); the TreeSHAP and LightGBM tests need it, and CI installs libgomp1.

### churn-prediction, wave 3 (2026-10-21)

- T4 took four attempts because earlier attempts were never merged and main had no T4 code. Before building, check that main actually contains the dependencies, and follow ARCHITECTURE 4.2 column names from the start.
- T5 runtime config added migration V004 and RunResult gate_mode/agent_halted. Keep adding new migrations with the next version number and keep RunResult/runner edits additive; check config error handling (gate invalid -> approve_all, others -> agent_halt, DEGRADED run) before changing it.

### churn-prediction, wave 7 (2026-10-23)

- Contact caps and pending approvals are point-in-time at the score `as_of`. Counting wall-clock `decided_at` exhausts the shared test warehouse, because dispatch stamps `datetime.now()`.
- Email copy stays on the template unless a provider key and its SDK are both present. A rerun of the same `run_id` reuses the subject and body already on OUTBOX or APPROVALS.
- Populations above 40,000 customers are generated in chunks. `tiny` and `small` stay one draw. `full` is 350,000. The measured DuckDB cycle was 54.67s for 215,649 actives. That is not a Snowflake timing.
- The committed T30 evaluation is a 36,000-customer draw because `small` produced 53,056 holdout rows, under T8's 55,536. All three R3.6 targets PASS. A synthetic PASS is not real-world accuracy and does not settle the 90-day impact target.

### churn-prediction, wave 6 (2026-10-23)

- Preserve scored archetype_low_conf alongside the policy confidence threshold (OR them), require explicit positive finite default priors for every enabled arm, and test critical escalation and a hard floor(N × share) exploration budget when extending policy/registry behavior.
- Keep T29's latent_archetypes interface and independent [seed,4242]/[seed,4243] streams stable; verify all eight mappings and non-stub feature coverage after generator changes. Tiny profile/default-suite success is not full-scale or live Snowflake proof: T27 owns volume/throughput, T30 full-source re-evaluation, and A10 live verification.

### churn-prediction, wave 8 (2026-10-23)

- The holdout migration is V019, not V010, and the assignment column is `arm_group`. Control is logged as HOLDOUT and is not dispatched. `RUNS.at_risk` stays the full count; T19 subtracts holdout. `churn simulate` is the clock, and the responder is not on the cycle path. A synthetic treated-versus-control rate is not the 90-day impact target.
- The tiny scored set often contains no control member, so the outbox test uses known keys. Do not delete another test's rows by subscriber_key.
- There is no FEATURES table. The PII scan reads the feature frame, the training frame, the ops tables, logs, prompts, and the model bytes. `customers_pii` is named only under synth. A phone regex on decoded joblib bytes is binary noise, not a PII signal. A passing tiny scan is not a live-data proof.

### churn-prediction, wave 9 (2026-10-23)

- `policy_update` runs after outcomes and after decide. The stored posterior includes the prior. `policy.decay` defaults to 1. There is no applied-decision ledger and no new migration. A shift test needs a flat prior because the shipped efficacy prior already prefers formulation.
- Retrain promotes only when top-decile precision and PR-AUC are each at least the current model minus 0.01. Rejection keeps the promoted row. `retrain.min_new_outcomes` is 100. Three history features use numeric type codes, holdout is excluded, and a closed treated outcome replaces the label. A one-row change on the tiny holdout can exceed 0.01. The weekly cron stays with the scheduler.

### churn-prediction, wave 10 (2026-10-23)

- Run metrics live in the existing V018 `RUN_METRICS` table. Coverage subtracts holdout from the denominator, and `RUNS.at_risk` stays the full count. The metrics step still runs when the agent is halted. The small-scale 45-day evaluation passing is not the 90-day impact target.
- The Snowflake connector is an optional extra. V013 is the sender view `CHURN_OPS.OUTBOX_FOR_SENDER`. The grants script is not a migration and DuckDB bootstrap does not run it. `CHURN_APPROVER`'s only write grant is `UPDATE` on `APPROVALS`. A skipped `snowflake` test is not live proof.

### churn-prediction, wave 11 (2026-10-23)

- Checkpoints and the cycle lock are V020. V012 stays unused. Resume skips finished score and agent shards only when the previous run is `FAILED` or `RUNNING`. A succeeded replay runs again, and insert-only keys keep the first rows.
- The lock updates only for the same holder or a heartbeat older than 300 seconds. Otherwise the cycle is `SKIPPED` and writes no `RUNS` row. `shards` defaults to 1. Source reads retry three times, then the run fails with no scores. A synthetic checkpoint recovery is not a live kill or a live Snowflake outage.

### churn-prediction, wave 12 (2026-10-23)

- `CHURN_API_KEY` is checked on every route, including `/health`. There is no approval route. `/ready` stays 503 until the warehouse, the migrations, and a promoted model all succeed, and the body lists the reasons.
- `POST /runs` returns the id and starts the cycle on a thread. The cycle lock stops a second trigger. The DuckDB connection uses an `RLock`, and a transaction holds it. Pin `fastapi` below 0.116.
