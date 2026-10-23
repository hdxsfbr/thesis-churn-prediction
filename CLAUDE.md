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

### churn-prediction, wave 6 (2026-10-23)

- Preserve scored archetype_low_conf alongside the policy confidence threshold (OR them), require explicit positive finite default priors for every enabled arm, and test critical escalation and a hard floor(N × share) exploration budget when extending policy/registry behavior.
- Keep T29's latent_archetypes interface and independent [seed,4242]/[seed,4243] streams stable; verify all eight mappings and non-stub feature coverage after generator changes. Tiny profile/default-suite success is not full-scale or live Snowflake proof: T27 owns volume/throughput, T30 full-source re-evaluation, and A10 live verification.
