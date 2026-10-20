# PRD: Churn Prediction & Intervention Agent (delivery plan)

Source of truth: `docs/PRD.md` (Thesis partner brief, Version 1). This document keeps every requirement of that brief, turns each into a testable requirement, and records the decisions the operator (Andre) made on the points the brief left open. Where this document and `docs/PRD.md` seem to differ, `docs/PRD.md` wins unless a recorded decision below says otherwise.

## 1. Problem

Thesis has no unified way to predict which subscribers are about to cancel, and no automated response when they are. Churn signals sit in separate Snowflake views (subscriptions and orders, Klaviyo email engagement, Zendesk support, quiz responses) with no predictive layer joining them. Retention is entirely reactive. Every lost subscriber costs a full acquisition to replace, and a 2–3 percentage point drop in monthly churn would change LTV materially.

## 2. Users

| User | Need |
|---|---|
| **Thesis retention / growth team** (business owner) | Fewer cancellations. They need at-risk subscribers contacted with the right intervention, without doing it by hand. |
| **Approver** (Thesis staff member who reviews high-stakes actions) | Approve or reject the queued interventions that sit above the configured risk/cost threshold. Per the decision below, they do this by editing a Snowflake approval table, for example through Metabase. |
| **Thesis data analysts** (Metabase users, e.g. Raphael Cattan) | Query risk scores, archetypes, intervention history and outcomes in Snowflake next to the existing business data. |
| **Thesis engineer / maintainer** | Deploy, run, maintain and extend the system (new data sources, new intervention types) from day one using the documentation. |
| **Downstream sending tool** (system, not yet chosen) | Reads approved and autonomous interventions from the Snowflake outbox and delivers them (email, offer). This is outside the build. |
| **Gauntlet evaluators** | Judge prediction accuracy, intervention relevance, architecture and production readiness from the submission deliverables. |

## 3. Goals

- G1. Score churn risk for every active subscriber on a schedule or continuously, with explainable top factors and a churn archetype.
- G2. Choose and execute (by writing to the outbox) a context-appropriate intervention per at-risk subscriber, with no human involved for low-stakes actions and an approval gate for high-stakes ones.
- G3. Log every intervention and its outcome to Snowflake, and feed those outcomes back into scoring and intervention selection.
- G4. Be production-grade: full-scale, fault-tolerant, PII-safe, tested, documented, deployable. Switching from the synthetic schema to Thesis's real views must be a configuration change only.
- G5. Meet the benchmarks in `docs/PRD.md` §8 when measured on the data that is available.

## 4. Non-goals

- NG1. Sending emails or offers directly. The build writes to a Snowflake outbox table only, and a downstream tool sends later (decision D2).
- NG2. A dashboard or report as the deliverable. Snowflake tables queryable in Metabase are the reporting surface (brief §11).
- NG3. A purpose-built approval UI, Slack bot or web app for approvals. Approval happens in a Snowflake table (decision D3).
- NG4. Direct API connectors to Shopify, Klaviyo or Zendesk, unless warehouse data is shown to be insufficient for a real-time need (brief §4, §11).
- NG5. Fine-tuning LLMs or ML models where simpler methods are enough (brief §11).
- NG6. A static rules engine posing as an agent, i.e. fixed "if score > x then send email" routing (brief §11).
- NG7. One generic retention treatment for all churn (brief §11).
- NG8. Connecting to Thesis's real Snowflake views during this build. The build runs on synthetic data in a schema we create (decision D1). The swap to real views comes later.

## 5. Operator decisions (recorded in the operator's words)

- **D1. Data access:** "Synthetic data in a Snowflake schema we create, mirroring assumed views, swap to real views later"
- **D2. Intervention execution:** "Write to a Snowflake outbox table only (downstream tool sends later)"
- **D3. Human approval surface:** "Edit a Snowflake approval table (e.g., via Metabase)"

Note on D1 and brief §11 ("do not ship demo-quality output that only works on synthetic or sampled data"): these are reconciled by requiring that nothing in the system depends on the data being synthetic. The synthetic schema mirrors the assumed real views, the generator produces data at full production scale with realistic edge cases, and the swap to real views changes configuration and view mappings only (R1.3, R1.4).

## 6. Requirements

Each requirement has an acceptance criterion (AC). "Snowflake" means the build's Snowflake account and schemas.

### R1. Data layer

- **R1.1 Synthetic source schema.** A Snowflake schema created by the build holds synthetic source views that mirror the domains in brief §4.1: subscriptions and orders (status, order history, shipment modifications, skips, cancellations, refunds, product catalog/blends), email engagement (opens, clicks, unsubscribes, campaigns, flows), support interactions (tickets, categories, resolution times, sentiment) and quiz data (responses, recommendation outputs).
  *AC:* Every domain listed has at least one view in the schema, and the assumed column contract of each view is documented.
- **R1.2 Realistic, full-scale synthetic data.** The synthetic data contains historical cancellations so precision and recall can be measured. Its volume is at least the scale of Thesis's active base (see OQ5), the quiz domain is in the millions of rows (brief: 7.3M+), and it includes edge cases: subscribers with no quiz data, no email history, no support tickets, partial records, nulls, duplicates and late-arriving rows.
  *AC:* A data profile report shows row counts per view, the share of subscribers in each edge-case category (each above 0%), and historical churn events spanning at least the label and evaluation window.
- **R1.3 Read through a source contract.** The system reads source data only through a defined input contract (view names and columns) that is set in configuration.
  *AC:* Pointing the system at a second schema with the same contract, by changing configuration only and no code, produces a successful scoring run.
- **R1.4 Swap-to-real readiness.** Documentation lists every assumed view and column and explains how to map Thesis's real views onto the contract.
  *AC:* The deployment guide has a "switch to production views" section that names each contract item and the steps to follow. No code change appears in those steps.
- **R1.5 Warehouse first.** All reads come from Snowflake and all outputs are written to Snowflake.
  *AC:* The code has no outbound calls to Shopify, Klaviyo or Zendesk APIs. Scores, archetypes, interventions, approvals and outcomes are all Snowflake tables.

### R2. Feature engineering

- **R2.1 Leading indicators.** Features include at least: email engagement velocity (change over time), shipment skip frequency trend, support ticket escalation pattern, time since last quiz retake, and blend-switching frequency.
  *AC:* Each of the five named indicators exists as a documented feature and has a unit test on a hand-built fixture with a known expected value.
- **R2.2 Trends over time.** Features describe behaviour across multiple time windows, not only a point-in-time snapshot.
  *AC:* At least one feature per domain is computed over two or more windows or as a slope/delta, and this is documented.
- **R2.3 No leakage.** Features for a scoring date use only data available before that date.
  *AC:* A test shows that adding events dated after the scoring date leaves that date's feature values unchanged.
- **R2.4 Incomplete data.** Missing domains (no quiz, limited engagement, partial records) produce defined feature values or missing-value indicators, never errors.
  *AC:* A scoring run over a fixture with one subscriber in each edge-case category completes and returns a score for every one of them.

### R3. Risk scoring

- **R3.1 Full-base scoring.** Every active subscriber gets a churn probability in every run. There is no sampling.
  *AC:* After each run, the number of scored subscribers equals the number of active subscribers in the source, and the run log records both counts.
- **R3.2 Explainability.** Each score comes with its top contributing factors, each with a direction and magnitude, in human-readable form.
  *AC:* Every score row has at least 3 named factors (fewer only when fewer features are non-missing), and a test checks that the factors are consistent with the model output.
- **R3.3 Archetype classification.** Each at-risk subscriber gets one primary archetype from {price sensitivity, efficacy dissatisfaction, subscription fatigue, life change}, with a confidence value.
  *AC:* Every at-risk score row has an archetype and confidence. The archetype evaluation report (see OQ3) shows per-archetype precision against the chosen ground truth.
- **R3.4 Risk tiers.** Subscribers are put into risk tiers whose boundaries are set in configuration.
  *AC:* Changing a tier boundary in configuration changes the tier assignment on the next run, with no code change.
- **R3.5 Write-back.** Scores, factors, archetype, tier, model version and run identifier are written to Snowflake for each run, and history is kept.
  *AC:* A Metabase-style SQL query can return the score history of one subscriber across runs.
- **R3.6 Accuracy.** Measured against historical churn with the agreed label and window (OQ1): top-risk-decile precision above 70% and pre-cancellation recall above 60%.
  *AC:* An evaluation report from a held-out time period shows both metrics, the label definition and the window. If a target is missed, the report says so and gives the reason; it does not hide the miss.
- **R3.7 Scheduled or continuous operation.** The scoring pipeline runs automatically on a schedule (cadence OQ4) or continuously, and feeds the agent directly. It is not a one-off batch report or CSV.
  *AC:* Two consecutive scheduled runs happen without manual action, and the agent consumes each run's output.

### R4. Intervention agent

- **R4.1 Four intervention paths.** At minimum: (1) personalized check-in email, (2) formulation adjustment suggestion, (3) targeted retention offer, (4) escalation flag for human follow-up.
  *AC:* A test suite produces each of the four types from suitable input profiles.
- **R4.2 Context-aware selection.** The selection takes into account risk score, archetype, engagement history and tenure, and records its reasoning. It is not a fixed score-threshold rule (NG6).
  *AC:* For a set of labelled routing scenarios, the agent picks the expected intervention, and two subscribers with the same score but different archetype or tenure get different, justified actions. Every decision row stores a rationale.
- **R4.3 Formulation fallback.** Formulation suggestions use quiz data when it exists and fall back to purchase history and engagement signals when it does not.
  *AC:* Tests cover a subscriber with quiz data and one without. Both get a valid suggestion, and the suggestion records which data source it used.
- **R4.4 Personalisation without PII leakage.** Message content is personalised to the subscriber's context. Raw PII is not written to logs or training data (see R7).
  *AC:* Generated content is stored in the outbox. A PII scan of logs and training tables finds no raw names, emails, addresses or phone numbers.
- **R4.5 Execution through the outbox.** Executing an intervention means writing one row to a Snowflake outbox table, containing everything a downstream tool needs to send it (D2).
  *AC:* Every autonomous or approved intervention has exactly one outbox row, and replaying the same run creates no duplicate rows.
- **R4.6 Guardrails.** The agent applies configurable guardrails: a contact-frequency cap per subscriber, limits on offers/discounts, and exclusions (e.g. subscribers who unsubscribed from email do not get email interventions). Default values: OQ6.
  *AC:* Tests show that a subscriber over the frequency cap, an unsubscribed subscriber and an offer beyond the limit are each blocked or rerouted, and the reason is logged.

### R5. Human-in-the-loop approval

- **R5.1 Threshold gate.** Interventions above a configurable risk and/or cost threshold go to approval. Those below it execute autonomously.
  *AC:* With threshold T, an intervention above T has status "pending approval" and no outbox row, and one below T has an outbox row straight away.
- **R5.2 No-code thresholds.** Thresholds can be changed without code changes or redeployment (for example, a Snowflake configuration table or a config file read at run time).
  *AC:* Changing the threshold value outside the code changes the routing on the next agent run.
- **R5.3 Snowflake approval table.** Pending interventions appear in a Snowflake approval table. The approver approves or rejects by editing a decision column, e.g. through Metabase (D3).
  *AC:* Setting a row to "approved" causes exactly one outbox row on the next agent cycle. Setting it to "rejected" causes none. Both outcomes are logged with the approver identity and time if available.
- **R5.4 Stale approvals.** Pending items that stay unanswered past a configurable expiry are expired and logged, not executed. Expiry default: OQ6.
  *AC:* A test with an item past expiry shows status "expired" and no outbox row.
- **R5.5 Autonomy rate.** With the default thresholds, more than 70% of dispatched interventions run without approval.
  *AC:* Shown on the evaluation run over synthetic data and reported in test results.

### R6. Audit trail, outcomes and feedback loop

- **R6.1 Full-context log.** Every intervention decision is logged to Snowflake with: subscriber key (pseudonymous), run id, risk score, tier, archetype, top factors, chosen action, rationale, approval status, execution status, timestamps, and later the subscriber response and outcome.
  *AC:* 100% of interventions in an evaluation run have a log row with every required field filled in (or explicitly set to "pending" for response/outcome).
- **R6.2 Outcome tracking.** Subscriber responses and retention outcomes are attached to the intervention log after an outcome window (OQ7). Until a real sending tool and real views exist, responses and outcomes come from the synthetic data.
  *AC:* After the outcome window passes in the synthetic timeline, every logged intervention has an outcome (retained / churned / unknown).
- **R6.3 Feedback into scoring.** Logged interventions and outcomes feed back into model retraining, which runs on a defined trigger. A new model version is promoted only if it is not worse than the current one on held-out data.
  *AC:* A retraining run consumes intervention/outcome data, produces a versioned model, and records promote/reject with metrics.
- **R6.4 Feedback into intervention selection.** Observed outcomes per archetype × intervention change future intervention choices.
  *AC:* A test in which one intervention clearly performs better for an archetype shows the agent's selection shifting toward it in later runs.
- **R6.5 Coverage.** More than 85% of subscribers flagged at-risk receive an intervention or an approval-queue entry within one cycle. Anything not covered is logged with a reason.
  *AC:* The coverage metric is computed per run and stored in Snowflake, and it exceeds 85% on the evaluation run.
- **R6.6 Business impact measurement.** The system can measure churn-rate reduction (target ≥2–3 pp within 90 days) by comparing treated subscribers with a holdout control group whose size is set in configuration.
  *AC:* A holdout group is assigned and logged, and a SQL query or report computes the churn rate of treated vs control. Note: the 90-day real-world target can only be verified after production go-live.

### R7. Privacy

- **R7.1 No raw PII in logs or training data.** Logs, training sets, model artefacts and the audit trail use pseudonymous subscriber keys only.
  *AC:* An automated test scans structured logs and training/audit tables and finds no email addresses, phone numbers, names or street addresses.
- **R7.2 PII to external LLMs.** What subscriber data may be sent to the LLM provider is governed by OQ8. Until that is resolved, only non-identifying context is sent.
  *AC:* A test of prompt construction shows that no raw PII fields appear in LLM requests.

### R8. Reliability and operations

- **R8.1 Failure recovery without state loss.** If a data source is unavailable, a Snowflake write fails or the LLM call fails, the run stops or retries cleanly and resumes from its last checkpoint. Nothing is lost or duplicated.
  *AC:* Fault-injection tests (source unavailable, write failure mid-batch, LLM error/timeout, process kill) each recover on rerun with no lost and no duplicated scores, decisions or outbox rows.
- **R8.2 Idempotency.** Rerunning a scoring or agent cycle for the same run id gives the same persisted state.
  *AC:* Running a cycle twice gives identical row counts and keys.
- **R8.3 Structured logging.** All components write structured logs with run id and component. Errors are classified.
  *AC:* Log lines parse as structured records and include run id. Error paths are covered by tests.
- **R8.4 Full-scale throughput.** One end-to-end cycle handles the whole active base within the cycle interval (OQ4).
  *AC:* A timed run on full-scale synthetic data completes within the interval, and the duration is recorded in the test results.
- **R8.5 Modularity and extensibility.** Prediction pipeline, agent logic and intervention execution are separate modules. Adding a new data source or intervention type does not require changes to unrelated modules.
  *AC:* The documentation describes how to add an intervention type and a data source. A test or example adds a dummy intervention type by touching only the intervention module and configuration.
- **R8.6 Service interface.** A FastAPI service exposes at least health/status and the ability to trigger or inspect runs (brief §10 dev tools).
  *AC:* The health endpoint returns OK, and a run triggered through the API shows up in the run log.
- **R8.7 Containerised deployment.** The system runs from Docker images with environment-based configuration. The cloud target is open (OQ4).
  *AC:* Following the deployment guide on a clean machine with Snowflake credentials produces a successful end-to-end cycle.

### R9. Quality and deliverables

- **R9.1 Automated tests.** pytest covers the critical paths: scoring accuracy, intervention routing, approval gating, failure recovery, PII safety.
  *AC:* The test suite passes in CI or locally, and a test results report is produced.
- **R9.2 Documentation.** Technical documentation (architecture, data contract, model card, agent decision logic, Snowflake output tables, operations runbook) and a deployment guide.
  *AC:* A Thesis engineer can follow the documents to deploy and run a cycle. Every output table and config parameter is documented.
- **R9.3 Submission deliverables.** Source code, technical documentation, demo video, deployment guide, test results, AI usage log (brief §12). Which of these the build produces versus Andre: OQ9.
  *AC:* Each deliverable that falls to the build exists in the repository.

## 7. Edge cases

- Subscriber with no quiz data, no email history, no support tickets, or a single order.
- Brand-new subscriber (tenure shorter than the feature windows).
- Subscriber who cancels or reactivates between scoring and intervention. The intervention must be dropped or rerouted, not sent.
- Subscriber unsubscribed from email, or already holding an active offer.
- Subscriber who already has a pending approval or recent intervention (frequency cap, no duplicates).
- Approval row edited to an invalid value, edited twice, or never answered (expiry).
- Duplicate or late-arriving source rows, and clock/timezone differences in event timestamps.
- Snowflake unavailable at start of a run or partway through a write. LLM provider rate limiting or outage, in which case falling back to templated content or deferring is logged.
- Threshold configuration missing or invalid. The system must fail safe (route to approval or halt) rather than act autonomously.
- Model retraining produces a worse model (not promoted).
- Archetype confidence low or tied. Handling must be defined, e.g. escalate or pick the safest intervention.

## 8. Open questions

The three questions allowed this step were used for D1–D3. The points below are still unresolved and need the operator before or during architecture. Where a requirement depends on one, it refers to its OQ number.

- **OQ1. Churn label and evaluation window:** What counts as churn (e.g. voluntary cancellation within N days of the scoring date; are payment failures, pauses or skips included?) and which window and holdout period apply to precision/recall?
- **OQ2. Active subscriber definition:** Do paused subscriptions or subscriptions with failed payments count as "active" for scoring?
- **OQ3. Archetype ground truth:** No archetype labels exist. Should archetypes come from cancellation reasons/support text (e.g. LLM-derived weak labels), from heuristic rules over features, or from something else? How should archetype accuracy be judged?
- **OQ4. Cadence and deployment:** What scoring/agent cadence (daily batch, hourly, continuous)? Which deployment target for this build (local Docker Compose, or a specific cloud)?
- **OQ5. Scale:** What is the size of Thesis's active subscriber base, so that synthetic data volume and throughput tests match it?
- **OQ6. Guardrail defaults:** What should the default approval risk/cost thresholds be? What is the contact-frequency cap, the offer catalogue and maximum discount, and the approval expiry period?
- **OQ7. Outcome window and feedback mechanism:** How long after an intervention is the outcome measured? Is the agent allowed to explore (e.g. a bandit approach) when choosing interventions, and what holdout-control percentage is acceptable?
- **OQ8. LLM provider and PII policy:** Anthropic Claude or OpenAI? May subscriber first names or other identifying data be sent to the LLM for personalisation, or should identifying fields only be merged downstream?
- **OQ9. Submission deliverables:** Should the build produce the demo video script/recording and the AI usage log, or does Andre produce these himself?
- **OQ10. Snowflake environment:** Which account, warehouse, database and role will the build use to create the synthetic schema and output tables, and who provides the credentials?
