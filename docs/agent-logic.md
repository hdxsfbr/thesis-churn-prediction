# Agent logic

The cycle scores every active subscriber, then the agent acts on the at-risk tiers. Holdout control members are logged and not contacted. Everyone else is filtered by guardrails, chosen by the policy, and routed by the gate.

## Runtime keys

`churn bootstrap` seeds `CHURN_OPS.AGENT_CONFIG` from `config/runtime_defaults.yaml`. The keys are:

- `tiers`: boundaries and `at_risk_from`. Medium, high, and critical are at risk.
- `gate`: `t_risk`, `t_cost_usd`, `t_discount_pct`, and `always_approve`. A decision needs approval when risk, cost, or discount crosses its threshold.
- `guardrails`: contact caps, `max_discount_pct`, `offer_catalogue`, `approval_expiry_h`, and `approval_grace_h`.
- `holdout`: `share` and `salt_version`. Assignment is a hash of the subscriber key and is sticky.
- `policy`: exploration share, low-confidence threshold, reach and tenure weights, and Beta priors per archetype and arm.
- `outcomes`: the window used to close `retained`, `churned`, or `unknown`.

A missing or invalid `gate` sends every decision to approval. A missing or invalid `tiers` or `guardrails` block halts the agent. The run is `DEGRADED` in either case. The snapshot for the run is `CONFIG_SNAPSHOTS`.

## Order

Reconcile counts at-risk subscribers. Approvals from the previous cycle are applied next. The agent then decides and dispatches. Outcomes close after the window. Policy posteriors fold after that, so this run still uses the posteriors it loaded. Metrics are written even when the agent is halted.

Autonomous decisions go to `OUTBOX` with `status = READY`. Gated decisions go to `APPROVALS` and are not sent. A subscriber who cancelled after scoring is logged `CANCELLED_STALE` and is not sent.

## Selection

Eligible arms are the intervention types that survive the guardrails. The policy samples one arm from a Beta posterior for the archetype, the arm, and the context bucket. A stored `POLICY_STATS` row replaces the prior. Exploration is capped by `policy.max_explore_share`. A critical subscriber with low archetype confidence is escalated when that arm is still available.

Email copy uses the configured model when a key and the SDK are present. Otherwise the template is used. A second run of the same `run_id` reuses the subject and body already stored.
