## Lessons

### churn-prediction, wave 1 (2026-10-20)

- cli.py (11 tickets) and orchestration/runner.py (9) are shared hot files; check TASKS.md "Shared files" before building a ticket and keep edits to those files minimal and additive to avoid merge conflicts.
- LightGBM needs libgomp1 at runtime: .gitlab-ci.yml installs it via apt-get and runs `ruff check src tests` plus pytest. Run ruff and pytest locally before opening a MR, and keep libgomp1 in any new image (T23 Dockerfile).
- T1 created the skeleton (warehouse protocol, MERGE writer, migrations, RISK_SCORES, RUNS) that all later tickets build on. Reuse these abstractions and add new migrations with the next version numbers rather than changing T1 interfaces.
