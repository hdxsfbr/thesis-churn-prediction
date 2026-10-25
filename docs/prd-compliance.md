# PRD compliance

Status of the plan requirements after the 2026-10-24 submission run. Counts and timings are in `docs/test-results.md`. A synthetic pass is not real-world accuracy and is not the 90-day impact number.

| ID | Status | Evidence |
|---|---|---|
| R1.1 | Met | Eight contract views in `config/source_contract.yaml`, documented in `docs/data-contract.md` and `docs/deployment.md`. The submission warehouse is DuckDB, not a live Snowflake schema. |
| R1.2 | Met on DuckDB | Scale run: 215,649 active subscribers and 19,883,149 quiz rows. Edge-case shares are covered by the default suite. |
| R1.3 | Met | The pipeline reads contract views. The contract-swap test passes in the default suite. |
| R1.4 | Met | `docs/deployment.md` names each view and its columns. Those steps are a config edit. |
| R1.5 | Partial | Reads and writes go through the warehouse. There is no Shopify, Klaviyo, or Zendesk client. The submission run used DuckDB. Live Snowflake was not executed. |
| R1.6 | Met | All eight views are mapped. `customers_pii` stays `pii: true` and is not rendered. The default suite includes the domain and PII tests. |
| R1.7 | Met | The full synthetic source is on main. The scale run is the volume check. The default suite includes the latent-archetype tests. |
| R2.1–R2.4 | Met | Feature tests in the default suite, including leakage and incomplete-data scoring. |
| R3.1 | Met | A run records active and scored counts and fails if they differ. |
| R3.2 | Met | Each score stores top factors. The factor test is in the default suite. |
| R3.3 | Met | At-risk rows carry an archetype and a confidence. The archetype report is part of `churn evaluate`. |
| R3.4 | Met | Tier bounds live in `AGENT_CONFIG`. |
| R3.5 | Partial | Scores, factors, archetype, tier, model version, and run id are written to `CHURN_OPS.RISK_SCORES` and kept across runs. No Thesis Metabase login is wired up. |
| R3.6 | Met on synthetic data | The small-scale slow run passed the three amended targets: top-2% precision 0.7608, lift 5.5891, recall 0.6032. The report and the card say this is not real-world accuracy. The brief's 70% top-decile target was replaced because the label makes it unreachable. |
| R3.7 | Partial | The daily and weekly crons are registered from settings. That is a unit check, not a live scheduler firing two days. |
| R3.8 | Partial | The small-scale rerun passed the three targets and names the synthetic limit. It is not a claim that the holdout is larger than the earlier 55,536-row baseline. |
| R4.1 | Met | Check-in, formulation, retention offer, and escalation each have tests. |
| R4.2 | Met | Policy tests cover archetype and tenure. Each decision stores a rationale. |
| R4.3 | Met | The cycle reads the quiz view before `as_of`. No quiz uses the most reordered blend's `goal_category` and overall email engagement. The logged `params` name `quiz` or `purchase_history`. A subscriber with neither still gets a suggestion. |
| R4.4 | Met | Outbox bodies keep `{{first_name}}`. The PII scan is in the default suite. |
| R4.5 | Met | Dispatch writes one outbox row. A replay does not duplicate it. |
| R4.6 | Met | Cap, unsubscribe, and discount tests pass. A clipped offer code names the percent that was applied. The guardrail removes an over-cap arm before the agent sees it. |
| R5.1–R5.4 | Met | Gate, config-table thresholds, approval edit, and expiry tests pass in the default suite. |
| R5.5 | Met | Autonomy on the small evaluation run was 1.0000, above 0.70. Escalations are in neither the numerator nor the denominator. A run of only escalations does not report 100%. |
| R6.1 | Met | The evaluation log fills the audit fields. Pending response and outcome are explicit. |
| R6.2 | Met | The synthetic outcome window closes retained, churned, or unknown. |
| R6.3 | Met | Retrain fits on training and validation snapshots only, scores the holdout, and promotes within 0.01. A shortfall of 0.02 rejects. The tolerance is recorded in `docs/decisions.md`. |
| R6.4 | Met | The policy-shift test is in the default suite. |
| R6.5 | Met | Coverage on the small evaluation run was 1.0000, above 0.85. Gaps are logged with a reason. |
| R6.6 | Partial | Holdout assignment and the treated-versus-control query exist. The 90-day reduction is not measured. |
| R7.1–R7.2 | Met | Pseudonymous keys. Prompt tests exclude raw PII. |
| R8.1–R8.3 | Met | Fault injection, idempotent reruns, and structured logs are in the default suite. |
| R8.4 | Met on DuckDB | Full cycle 60.03 seconds for 215,649 active subscribers, inside the daily interval. Not a Snowflake timing. |
| R8.5 | Met | A dummy arm is added from a temporary copy of the intervention config. The real file is not rewritten. |
| R8.6 | Met | `GET /health` returns ok when `X-API-Key` is sent. The key is required on every route, including health. |
| R8.7 | Partial | Image `churn-submission:submit` was built with the `snowflake` and `llm` extras. It was not started as a cycle. The Snowflake smoke did not run. `docs/deployment.md` keeps the clean-machine checklist. |
| R9.1 | Met | Default, slow, and scale runs passed. `docs/test-results.md` is that report. |
| R9.2 | Met | Architecture, data contract, model card, agent logic, output tables, runbook, and deployment guide are in `docs/`. |
| R9.3 | Partial | Source, docs, deployment guide, test results, and the AI usage log are in the repo. The demo script is `docs/demo-script.md`. The video is not recorded. |

Out of scope, disclosed and not built: the 90-day live impact number, a Thesis Metabase login, a downstream sender, and a choice of cloud host.
