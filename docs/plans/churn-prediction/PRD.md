# PRD: Churn Prediction & Intervention Agent (delivery plan)

Source of truth: `docs/PRD.md` (Thesis partner brief, Version 1). This document keeps every requirement of that brief, turns each into a testable requirement, and records the decisions the operator (Andre) made on the points the brief left open. Where this document and `docs/PRD.md` seem to differ, `docs/PRD.md` wins unless a recorded decision below says otherwise.

**Revision 2 (2026-10-21, change request during the build).** Three problems found while building waves 1–5 are fixed here: the R3.6 precision target was mathematically unreachable (D4–D7, R3.6 rewritten), the full synthetic source delivered under R1.1/R1.2 is no longer on main (R1.7 added), and the source contract maps only two of the eight views, so four feature domains run on stub defaults (R1.6 added). R3.8 adds a re-evaluation on the restored data. Every other requirement is unchanged. See §9 for the change log.

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

Revision 2 decisions. Andre delegated the first four in the change request; they are recorded in its words. D7–D9 are his answers to this step's questions.

- **D4. Churn label kept:** "keep label A1 (30-day voluntary churn) unchanged, and replace the precision target with targets that are achievable and still meaningful: (a) precision in the top 2% of risk scores > 0.50, and (b) top-decile lift >= 4x the base rate."
- **D5. Recall target kept, boundary from validation:** "Keep pre-cancellation recall > 0.60, measured at the at-risk tier boundary, and choose that boundary on a validation split (not the holdout) rather than the fixed 0.20; report the precision obtained at that boundary."
- **D6. Rebuild the lost synthetic source:** T2's full synthetic source "was merged as MR !5 but its code is NOT on main today"; a new ticket "rebuilds T2's full synthetic source on top of current main (TASKS.md T2 slice is the spec), reconciles it with T7's interim latent archetype (keep the latent_archetypes() interface T7 uses; replace the interim model with the full one), uses the next free migration number instead of V002 if V002 is now taken, and re-checks T7's test_archetype_beats_chance_on_latent_truth. Mark T2 in the plan as superseded by the new ticket." The source contract must also map "all contract views (subscriptions, orders, subscription_events, products, email_events, support_tickets, quiz_responses, customers_pii) … for the synthetic schema, with a test that every T4 feature domain is populated (has_<domain> = 1) on synthetic data." Afterwards "the T8 evaluation is rerun on the full synthetic data with all views mapped, and the eval report and model card regenerated."
- **D7. Boundary rule:** "Highest boundary with validation recall >= 0.65 (safety margin)"
- **D8. Boundary use:** "Evaluation only; report it as a recommended value, runtime tiers unchanged"
- **D9. Re-evaluation outcome:** "Done with an honest MISS and stated reason"

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
- **R1.6 Every contract view mapped (Revision 2, D6).** The source contract configured for the synthetic schema maps all eight contract views: subscriptions, orders, subscription_events, products, email_events, support_tickets, quiz_responses and customers_pii. No feature domain runs on stub defaults in the real pipeline when the source has the data. customers_pii stays declared PII and is never selected by pipeline feature, prompt or log code (R7.1).
  *AC:* (a) Contract validation at cycle start passes with all eight views mapped to the synthetic schema. (b) An automated test generates synthetic data, builds features through the configured contract (not through stubs), and shows that for every feature domain with a `has_<domain>` indicator (email, subscription events, support, quiz and any other domain the feature catalogue flags) at least one scored subscriber has `has_<domain> = 1`. (c) The existing test that customers_pii is never read by pipeline code still passes.
- **R1.7 Full synthetic source restored on main (Revision 2, D6).** The full synthetic source that R1.1 and R1.2 require (all eight views, latent archetypes with latent truth, noisy reason codes, independent involuntary churn, the edge-case categories, the data profile command and its stored profile and report) is present on main again. It is rebuilt on top of the current code rather than restored over it, so later work is not lost. It replaces the interim latent archetype model added with the archetype classifier while keeping the interface the archetype classifier and its tests use to read the latent archetype. Stored-schema changes take the next free migration number. The original delivery (T2) is marked superseded in the plan; no other merged ticket's scope changes.
  *AC:* (a) On main, every R1.1 and R1.2 acceptance criterion passes again, including the T2 acceptance tests named in the plan (every domain has a populated view, edge-case shares above 0%, churn events spanning the label and holdout window, determinism for a seed, reason codes following the latent archetype with about 25% noise, synthetic code not importing features or model). (b) The archetype classifier's test that per-archetype precision against latent truth beats chance (above 0.25) passes against the full latent model, unchanged. (c) The full test suite passes with no migration number reused. (d) The plan documents show T2 as superseded by the new ticket.

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
- **R3.6 Accuracy (amended in Revision 2, D4, D5, D7, D8).** Measured against historical churn with the label unchanged (30-day voluntary churn, A1) on the held-out final time period, the model meets three targets:
  1. precision among the top 2% of risk scores above 0.50;
  2. top-decile lift of at least 4× the base rate (top-decile precision divided by the holdout base rate);
  3. pre-cancellation recall above 0.60, measured at the at-risk boundary chosen on a validation split that does not overlap the holdout. The boundary is the highest score threshold whose recall on the validation split is at least 0.65 (D7). The precision at that boundary on the holdout is reported, with no target.

  The boundary is used only for evaluation: it is reported as a recommended at-risk boundary, and the runtime risk tiers (R3.4) are not changed by it (D8).
  *Why the old target was replaced:* the brief's "top-decile precision above 70%" cannot be met under this label. With a base rate of about 3.1%, a perfect model's top decile holds at most all churners, so its precision is at most 3.1% / 10% = 0.31, a lift of 10×. The new targets have reachable ceilings: the top 2% is smaller than the churner share, so a perfect model reaches precision 1.0 there; 4× lift equals a top-decile precision of about 0.124 at a 3.1% base rate, against a 10× ceiling. The earlier evaluation (T8, MR !18) measured top-decile precision 0.17 and recall 0.40 at the fixed 0.20 boundary and reported an honest MISS.
  *AC:* The evaluation report and model card, built from the held-out period, show: the label definition, label window, holdout period and validation period; the holdout base rate; top-2% precision, top-decile precision and top-decile lift; the validation-chosen boundary, the validation recall that selected it, and the holdout recall and precision at that boundary; and a PASS or MISS line for each of the three targets, where every MISS gives its reason. The ceiling arithmetic above, computed from the measured base rate, appears in the report. The report states that accuracy on synthetic data is not real-world accuracy, and that the 90-day impact target (R6.6) can only be checked after go-live. Unit tests check each metric on a hand-built fixture with a known value, check that the boundary is chosen from validation data only (changing holdout data does not move it), and check that a missed target is printed as MISS with a reason.
- **R3.7 Scheduled or continuous operation.** The scoring pipeline runs automatically on a schedule (cadence OQ4) or continuously, and feeds the agent directly. It is not a one-off batch report or CSV.
  *AC:* Two consecutive scheduled runs happen without manual action, and the agent consumes each run's output.
- **R3.8 Re-evaluation on the full synthetic data (Revision 2, D6, D9).** After R1.6 and R1.7 are met, the R3.6 evaluation is rerun on the full synthetic data with every contract view mapped, and the evaluation report and model card are regenerated from that run.
  *AC:* The regenerated report and model card state that they come from the full synthetic source with all eight views mapped, use a synthetic dataset at least as large as the one behind the earlier T8 report, and show every R3.6 metric and PASS/MISS line. The feature summary in the report shows every feature domain populated (R1.6). A target still missed on this run is acceptable when it is reported as MISS with its reason (D9).

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
- (Revision 2) No score threshold reaches 0.65 recall on the validation split, or the validation split holds too few churners to choose one. The report must say so and print the recall target as MISS with that reason, not fall back to the holdout or to the fixed runtime boundary silently.
- (Revision 2) The top 2% of the holdout holds very few subscribers (small synthetic runs). The report shows the count behind each precision figure so a small-sample result is visible.
- (Revision 2) The holdout base rate differs from the 3.1% assumed here. The ceiling arithmetic is computed from the measured rate, not hard-coded.
- (Revision 2) Rebuilding the synthetic source changes the latent model that the archetype classifier was tested against. Its latent-truth test must still pass without loosening its threshold.

## 8. Open questions

The three questions allowed this step were used for D1–D3. The points below are still unresolved and need the operator before or during architecture. Where a requirement depends on one, it refers to its OQ number.

- **OQ1. Churn label and evaluation window:** What counts as churn (e.g. voluntary cancellation within N days of the scoring date; are payment failures, pauses or skips included?) and which window and holdout period apply to precision/recall? *Revision 2:* Andre kept ARCHITECTURE A1 (30-day voluntary churn, 3-month time holdout) as the label (D4); the precision/recall targets that use it are now set by R3.6.
- **OQ2. Active subscriber definition:** Do paused subscriptions or subscriptions with failed payments count as "active" for scoring?
- **OQ3. Archetype ground truth:** No archetype labels exist. Should archetypes come from cancellation reasons/support text (e.g. LLM-derived weak labels), from heuristic rules over features, or from something else? How should archetype accuracy be judged?
- **OQ4. Cadence and deployment:** What scoring/agent cadence (daily batch, hourly, continuous)? Which deployment target for this build (local Docker Compose, or a specific cloud)?
- **OQ5. Scale:** What is the size of Thesis's active subscriber base, so that synthetic data volume and throughput tests match it?
- **OQ6. Guardrail defaults:** What should the default approval risk/cost thresholds be? What is the contact-frequency cap, the offer catalogue and maximum discount, and the approval expiry period?
- **OQ7. Outcome window and feedback mechanism:** How long after an intervention is the outcome measured? Is the agent allowed to explore (e.g. a bandit approach) when choosing interventions, and what holdout-control percentage is acceptable?
- **OQ8. LLM provider and PII policy:** Anthropic Claude or OpenAI? May subscriber first names or other identifying data be sent to the LLM for personalisation, or should identifying fields only be merged downstream?
- **OQ9. Submission deliverables:** Should the build produce the demo video script/recording and the AI usage log, or does Andre produce these himself?
- **OQ10. Snowflake environment:** Which account, warehouse, database and role will the build use to create the synthetic schema and output tables, and who provides the credentials?

## 9. Change log

- **Revision 2 (2026-10-21).** R3.6 rewritten (top-2% precision > 0.50, top-decile lift ≥ 4×, pre-cancellation recall > 0.60 at a validation-chosen boundary, ceiling arithmetic). R1.6 (all eight contract views mapped, every feature domain populated), R1.7 (full synthetic source restored on main, T2 superseded) and R3.8 (re-evaluation and regenerated report and model card) added. Decisions D4–D9 and four edge cases added; OQ1 annotated. No other requirement, number or acceptance criterion changed. Plan constraints from the change request: the new work goes in the next wave to be built (waves 1–4 shipped; wave 5 with T8, T9, T11 and T14 is in progress), the re-evaluation comes after it, wave numbering stays consistent with what shipped, and the wave count stays within the platform limit.
