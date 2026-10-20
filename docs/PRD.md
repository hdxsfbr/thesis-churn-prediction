# PRD: Subscription Churn Prediction & Intervention Agent

**Partner:** Thesis (takethesis.com)
**Program:** Gauntlet AI partner project (hiring partnership capstone)
**Tier / Type:** Silver, AI Solution, Version 1
**Portal brief last updated:** 10/12/2026
**Partner brief prepared by:** Raphael Z. Cattan, Director of Data Analytics (April 15, 2026)
**Time constraint:** 3 to 5 days
**Technical contact:** Raphael Cattan (raphael@takethesis.com), available for questions about the data environment, Snowflake schema, and project scope

**Sources compiled:**
1. Gauntlet portal project brief: https://portal.gauntletai.com/projects/8Osm3MC2m1XK3VoPHAr8
2. Attachment: `thesis_gauntlet_churn_project_brief.docx` (Thesis partner project brief)

> One-line goal: Predict which subscribers are at risk of canceling and autonomously intervene to retain them, before they ever hit the cancel button.

---

## 1. Summary

Thesis has no unified system for predicting which subscribers are at risk of canceling or for intervening before they churn. Churn signals are fragmented across subscription data, email engagement, support interactions, and quiz responses in Snowflake, with no predictive layer connecting them and zero automated response.

This project builds an end-to-end system that:
1. Scores churn risk for every active subscriber,
2. Classifies the reason (archetype) behind the risk, and
3. Autonomously takes action to retain the subscriber, logging everything for auditability and continuous improvement.

## 2. Problem & Business Context

- Subscription revenue is Thesis's economic foundation. Every subscriber lost requires a full replacement acquisition cost.
- Even a 2 to 3 percentage point reduction in monthly churn compounds into a significant shift in LTV and acquisition efficiency.
- Today, retention is entirely reactive. This project turns it into a proactive, AI-driven function that runs continuously against the full subscriber base.

## 3. Purpose of the Project (Partner Perspective)

The project is scoped as a capstone evaluation within the Gauntlet timeline, but Thesis wants a system with real, immediate business value: something usable at scale, not a proof of concept that has to be rebuilt. Thesis welcomes Gauntlet's input on scoping and implementation approach. Specific tables and views will be reviewed during onboarding.

### 3.1 What Thesis is evaluating in the candidate

The project is as much an evaluation tool as a business deliverable. How the candidate approaches it signals whether they can operate across Thesis's broader AI roadmap.

| Trait | What it means here |
|---|---|
| **Systems thinking over isolated features** | Architect an end-to-end system with clear data flows, modular components, and extensibility, not a single-purpose script. |
| **Autonomous decision-making** | The agent takes action; it is not a tool that surfaces information for humans to act on. Human-in-the-loop is a safety mechanism, not the core workflow. |
| **Production instinct** | Handle real data at real scale with real edge cases. Happy-path demos will not pass evaluation. |
| **Feedback loops** | The system must improve over time. A static model with no iteration mechanism is incomplete regardless of initial accuracy. |

## 4. Data Environment

- All production data is centralized in **Snowflake**, with curated views and models already built and accessible through **Metabase** (SQL).
- Treat Snowflake as the **primary data source**. Clean, structured views exist for subscriptions, orders, email engagement, support interactions, quiz data, and ad performance.
- Direct API connections to source systems (Shopify, Klaviyo, Zendesk, etc.) only when real-time data is required and the Snowflake views do not suffice.
- **Outputs** (risk scores, intervention logs, outcome tracking) are written back to Snowflake so they are queryable in Metabase alongside existing business data.

### 4.1 Key data domains

| Domain | Contents |
|---|---|
| **Subscriptions & orders** | Subscription status, order history, shipment modifications, skips, cancellations, refunds, product catalog |
| **Email engagement** (Klaviyo sync) | Opens, clicks, unsubscribes, campaign performance, flow metrics, customer segmentation |
| **Support interactions** (Zendesk sync) | Ticket history, categories, resolution times, sentiment signals |
| **Quiz data** (7.3M+ data points) | Quiz responses (cognitive goals, lifestyle factors, sleep, caffeine habits), recommendation outputs |

## 5. Production-Ready Standard

The system must be deployable against Thesis's live data and live customer base at full scale with minimal modification. It must handle edge cases, fail gracefully, log actions for auditability, and be documented well enough that a Thesis engineer can maintain and extend it from day one. This is not a demo and not an MVP; the expectation is that it goes into production and shows results immediately.

## 6. Technical Scope

### Phase 1: Churn Prediction Pipeline

- **Data source:** Query Snowflake views covering:
  - Subscription lifecycle: order frequency, shipment skips/modifications, cancellations, refund history
  - Email engagement: open/click rates, unsubscribe events, flow engagement
  - Support interactions: ticket frequency, categories, sentiment
  - Quiz responses: cognitive goals, lifestyle inputs, recommendation history

  Use existing Snowflake models as the primary data layer. Connect to source APIs only if real-time signals are needed beyond what the warehouse provides.
- **Feature engineering:** Extract leading churn indicators, including:
  - Declining email engagement velocity
  - Increasing shipment skip frequency
  - Support ticket escalation patterns
  - Time since last quiz retake
  - Blend-switching frequency

  Features must capture behavioral **trends over time**, not just point-in-time snapshots.
- **Risk scoring:** Per-subscriber churn probability with **explainable risk factors** (top contributing factors per subscriber). The model must distinguish between churn archetypes, because each requires a different intervention:
  - Price sensitivity
  - Efficacy dissatisfaction
  - Subscription fatigue
  - Life change
- **Output:** A continuously running or scheduled pipeline that scores the entire active subscriber base, segments by risk tier, writes scores back to Snowflake (queryable in Metabase), and feeds directly into the intervention agent. Not a batch report. Not a CSV.

### Phase 2: Autonomous Intervention Agent

- **Decision logic:** The agent selects context-appropriate interventions based on risk score, churn archetype, engagement history, and subscription tenure. Minimum 4 distinct intervention paths:
  1. Personalized check-in email
  2. Formulation adjustment suggestion (uses quiz data when available; falls back to purchase history and engagement signals when quiz data is absent)
  3. Targeted retention offer
  4. Escalation flag for human follow-up
- **Human-in-the-loop:** Interventions above a configurable risk/cost threshold require human approval before execution. Lower-stakes actions (for example, a check-in email to a moderate-risk subscriber) execute autonomously. Thresholds must be adjustable **without code changes**.
- **Action logging & feedback:** Every intervention is logged with full context (risk score, archetype, action taken, subscriber response, outcome) and written back to Snowflake so intervention history is queryable alongside subscriber data. This log feeds back into the prediction model to improve future scoring and intervention selection.
- **Output:** A production-ready agent that processes the risk-scored subscriber list, selects and executes interventions, queues high-stakes actions for approval, and logs everything for continuous improvement.

## 7. Functional Requirements

- [ ] End-to-end pipeline: data ingestion → feature engineering → risk scoring → intervention selection → action execution → outcome logging
- [ ] Snowflake as the primary data source: read from existing views, write scores and logs back to the warehouse
- [ ] Explainable risk scores: not just a number, but the top contributing factors for each subscriber
- [ ] Churn archetype classification (price sensitivity, efficacy dissatisfaction, subscription fatigue, life change) routing to distinct intervention paths
- [ ] Minimum 4 intervention types: personalized check-in email, formulation adjustment suggestion, targeted retention offer, escalation to human
- [ ] Graceful handling of incomplete data: works for subscribers with no quiz data, limited engagement history, or partial records
- [ ] Configurable human-in-the-loop approval thresholds, adjustable without code changes
- [ ] Full action audit trail with outcome tracking written to Snowflake, for model improvement and Metabase reporting
- [ ] Graceful failure handling with state recovery: if a data source is unavailable or an intervention fails, the system recovers without losing state
- [ ] Feedback loop: intervention outcomes feed back into scoring and intervention selection
- [ ] Production-ready at full scale: handles the entire active subscriber base, not a sampled subset

## 8. Performance Benchmarks & Success Metrics

**Evaluation criteria:** prediction accuracy (precision/recall against historical churn data), intervention relevance (does the agent select the right action for the right reason), system architecture (modularity, scalability, extensibility to new data sources and intervention types), and production readiness (error handling, logging, documentation, deployability).

| Metric | Target |
|---|---|
| Monthly churn rate reduction | ≥ 2 to 3 percentage points within 90 days |
| Model precision (top risk decile, vs. historical churn) | > 70% |
| Model recall (pre-cancellation churn detection) | > 60% |
| Intervention coverage (% of identified at-risk subscribers reached) | > 85% |
| Autonomous action rate (no human approval required) | > 70% of dispatched interventions |
| Audit trail completeness | 100% of interventions logged to Snowflake with full context |
| Scoring throughput | Entire active subscriber base on a scheduled or continuous basis |

## 9. Code Quality Expectations

- Modular architecture with clear separation between **prediction pipeline**, **agent logic**, and **intervention execution**
- Documentation sufficient for a Thesis engineer to maintain and extend from day one
- Automated tests for critical paths: scoring accuracy, intervention routing, failure recovery
- No raw customer PII in logs or training data
- Clean error handling with structured logging
- Code organized for extensibility to new data sources and intervention types

## 10. Technology

| Category | Requirement |
|---|---|
| **Languages** | Python, SQL |
| **AI / ML frameworks** | LLM APIs (Anthropic Claude or OpenAI); scikit-learn or equivalent for churn modeling; LangChain/LangGraph or equivalent for agent orchestration |
| **Dev tools** | Git, Docker, FastAPI, Snowflake connector (`snowflake-connector-python` or Snowpark), pytest |
| **Cloud platforms** | Snowflake (primary data platform). Cloud deployment platform TBD during onboarding. |
| **Reporting** | All outputs queryable in Metabase |

### 10.1 Other requirements

- All data reads come from existing Snowflake views covering subscriptions, orders, email engagement (Klaviyo sync), support interactions (Zendesk sync), and quiz data (7.3M+ data points).
- All outputs (risk scores, intervention logs, outcome tracking) are written back to Snowflake and queryable in Metabase.
- Direct API connections to source systems (Shopify, Klaviyo, Zendesk) only when real-time data is required beyond what the warehouse provides.
- Graceful handling of subscribers with incomplete data (no quiz responses, limited engagement history).
- Human-in-the-loop thresholds configurable without code changes.

## 11. Off-Limits & Anti-Patterns

| Do not | Why |
|---|---|
| Build a static rules engine disguised as an agent | "if score > 0.7, send email" is not intelligent intervention |
| Deliver a dashboard or report as the output | The deliverable is autonomous action, not a visualization for a human to interpret |
| Treat all churn the same | A one-size-fits-all retention email ignores why the subscriber is leaving |
| Build direct API connectors (Shopify, Klaviyo, Zendesk) when data already exists in Snowflake | Use the warehouse first |
| Fine-tune models where simpler approaches suffice | Use the right tool for the job, not the most complex one |
| Put raw customer PII in logs or training data | The feedback loop must track patterns and outcomes without creating a privacy liability |
| Ship demo-quality output that only works on synthetic or sampled data | It must run against the full subscriber base on day one |

## 12. Submission & AI Policy

- **AI usage documentation:** Required
- **Required deliverables:**
  1. Source code
  2. Technical documentation
  3. Demo video
  4. Deployment guide
  5. Test results
  6. AI usage log
- Submission is made from the portal project page ("Open Submission Interface") and is tied to this project version (Version 1).

## 13. Open Questions for Onboarding

These items are explicitly deferred to onboarding in the source briefs:

- Exact Snowflake tables and views available (and their schemas)
- Cloud deployment platform (TBD)
- How interventions are actually executed downstream (e.g., which system sends emails and offers) given the "warehouse first, no direct connectors unless real-time" constraint
- Definition of the historical churn label and the evaluation window used for precision/recall
