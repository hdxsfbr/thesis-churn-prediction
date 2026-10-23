# Extending the pipeline

Two changes stay in config. Neither one edits the runner, the policy, or the feature builder.

## Add an intervention type

1. Subclass `InterventionType` in a new module. Set `name` and `channel`, and implement `action` and `template`. `template` returns a `Content` whose subject and body keep the `{{first_name}}` merge token. Do not put an email, a phone number, or a subscriber key in an email template.
2. Add the dotted path to `config/interventions.yaml`. `load_registry` instantiates every path in that list. A duplicate `name` is an error.
3. Give the new name a default prior under `policy.priors.default` in `CHURN_OPS.AGENT_CONFIG` (seeded from `config/runtime_defaults.yaml`). Validation refuses to run the agent until every enabled arm has that prior. Add archetype-specific priors only when that archetype should start from a different belief.
4. Optional: a `CHURN_OPS.POLICY_STATS` row (`archetype`, `intervention_type`, `context_bucket`, `alpha`, `beta`) replaces the prior for that bucket. The context bucket is `{tier}:{tenure band}`, and the tenure band is `<90`, `90-365`, or `>365`.

Email arms go through the content step. With no provider key they use the template. `human_queue` is not sent to the model.

## Add a data source

1. Add a logical view to `config/source_contract.yaml`: physical `object`, `dedup_key`, `loaded_at`, and the columns the feature SQL will read. Column names match what the feature SQL already selects. A view that holds names, emails, phones, or addresses is `pii: true`. Pipeline SQL never selects a PII view. `customers_pii` is that view.
2. Point `object` at the real table or view when production data replaces the synthetic source. That mapping is the only edit. Feature SQL keeps selecting the contract CTE, not the physical name.
3. Put the new feature statement in `sql/features/` and register its columns in `churn.features.registry`. The statement selects from the contract CTE and filters on the point-in-time timestamps (`event_ts` and `loaded_at`). Do not read `CHURN_PRIVATE.KEY_MAP` from a feature.

`churn` validates the contract at the start of every cycle. A missing object or a column of the wrong type fails the run before any score is written.
