## Lessons

### churn-prediction, wave 1 (2026-10-20)

- cli.py (11 tickets) and orchestration/runner.py (9) are shared hot files; check TASKS.md "Shared files" before building a ticket and keep edits to those files minimal and additive to avoid merge conflicts.
- LightGBM needs libgomp1 at runtime: .gitlab-ci.yml installs it via apt-get and runs `ruff check src tests` plus pytest. Run ruff and pytest locally before opening a MR, and keep libgomp1 in any new image (T23 Dockerfile).
- T1 created the skeleton (warehouse protocol, MERGE writer, migrations, RISK_SCORES, RUNS) that all later tickets build on. Reuse these abstractions and add new migrations with the next version numbers rather than changing T1 interfaces.

### churn-prediction, wave 2 (2026-10-20)

- Wave 2 (T2 synth, T3 contract validation, T6 TreeSHAP) built on the wave 1 skeleton: keep validation in contract/validate.py and extend the contract views additively; check the T3 contract layout notes before touching loader or views.
- Before opening a MR run `ruff check src tests` and pytest with LD_LIBRARY_PATH=/workspace/runs/.libs (libgomp); the TreeSHAP and LightGBM tests need it, and CI installs libgomp1.
