# Test results

Generated 2026-10-24 by `churn report-tests`.

- tests: 267
- failures: 0
- errors: 0
- skipped: 0

## Failures

None.

## Slow and scale

| test | marker | metrics |
|---|---|---|
| tests.integration.test_eval_report::test_targets_on_medium_synth | slow | top_share_precision=0.7608, top_decile_lift=5.5891, pre_cancellation_recall=0.6032 |
| tests.integration.test_evaluation_run::test_coverage_above_85_percent | slow | coverage=1.0000 |
| tests.integration.test_evaluation_run::test_autonomy_above_70_percent_with_default_thresholds | slow | autonomy_rate=1.0000 |
| tests.scale.test_full_cycle_timed::test_full_scale_volumes | scale | active=215649, quiz=19883149 |
| tests.scale.test_full_cycle_timed::test_full_cycle_within_interval_and_duration_recorded | scale | duration_s=60.03, status=SUCCEEDED |

## Commands

The 267 tests are the ones that ran: the default suite plus the slow and scale tests. The default command on its own reported 262 passed, 6 deselected, 1 warning in 93.50s. Those six deselected tests are the three slow tests, the two scale tests, and the Snowflake smoke.

- `ruff check src tests` — passed.
- `pytest` — 262 passed, 6 deselected, 1 warning in 93.50s on 2026-10-24. The warning is the existing Pandas4 inplace drop.
- `pytest` after the Snowflake merge fix — 264 passed, 6 deselected, 289 warnings in 93.74s on 2026-10-25. The two extra tests cover comment splitting and timestamp casts. The warnings are the existing NumPy timedelta deprecations.
- `pytest -o addopts= -m slow` — 3 passed in 29.53s.
- `pytest -o addopts= -m scale` — 2 passed in 169.79s.
- `pytest -o addopts= -m snowflake` — 1 passed, 269 deselected, 18 warnings in 196.85s on 2026-10-25. The trial account ran migrate, tiny synth, train, and one cycle. The test asserts status SUCCEEDED and scored greater than 0. This is not the 2026-10-24 DuckDB report above.
- `docker build -f docker/Dockerfile -t churn-submission:submit .` — image built. It was not started as a cycle.

CI on the merge request runs the default suite only and publishes its own `docs/test-results.md` artifact. That artifact does not replace this file.
