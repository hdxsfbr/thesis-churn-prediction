# Decisions for Andre to confirm

These are A1–A12 from the architecture. Each line is the choice the build follows. Confirm or correct it before the submission is treated as final.

## A1

The positive label is a voluntary cancellation inside 30 days. Involuntary churn is reported separately. Evaluation holds out the last three snapshot months. Validation is the three months before that and is used only to pick the at-risk boundary.

Confirm: the 30-day voluntary label and the three-month holdout stay.

## A2

Active subscribers are `ACTIVE`, `PAUSED`, and `PAST_DUE`.

Confirm: paused and past-due subscribers stay in the scored population.

## A3

Archetypes use weak labels from reason codes and ticket categories. On synthetic data the score is also compared with the generator's latent archetype.

Confirm: weak labels are the real-data ground truth, and latent truth stays out of the pipeline.

## A4

The cycle runs daily at 06:00 UTC. Deployment is one Compose stack with `api` and `worker`.

Confirm: daily is the cadence, and the cloud host stays unnamed.

## A5

The default synthetic population is 350,000 subscribers, with a quiz volume above 7.5 million rows at full scale. The committed full-scale timing is a DuckDB measurement, not a Snowflake one.

Confirm: 350,000 remains the default until Thesis states its live count.

## A6

Approval starts at risk 0.85, cost 15 USD, or discount 20 percent. Contact caps are 1 in 14 days and 3 in 90 days. The offer catalogue and the 25 percent discount ceiling are the guardrails in `runtime_defaults.yaml`.

Confirm: those thresholds still match the review policy.

## A7

Outcomes close after 30 days. Exploration is on, with a cap. Ten percent of at-risk subscribers are holdout control, by a sticky hash.

Confirm: the 10 percent holdout and the 30-day outcome window stay.

## A8

The default model is Anthropic Claude Haiku-class. Prompts carry archetype, factor labels, tenure, and blend names. They do not carry email, phone, or street address. `{{first_name}}` is filled by the sender.

Confirm: no identifier is added to the prompt.

## A9

The build produces the source, the docs, the deployment guide, `docs/test-results.md`, `docs/ai-usage-log.md`, and `docs/demo-script.md`. Andre records the demo video.

Confirm: the video is recorded from `docs/demo-script.md`.

## A10

Snowflake credentials come only from the environment. Tests run on DuckDB. A live cycle is a separate check and is not implied by a green default suite.

Confirm: the trial account is used for that live check, not pasted into the repo.

## A11

The cycle is a plain Python step runner with warehouse checkpoints. It is not a graph runtime.

Confirm: checkpoints and the run lock are the recovery mechanism.

## A12

Deploy-time files live in `config/`. Tiers, the gate, guardrails, holdout, and policy live in `CHURN_OPS.AGENT_CONFIG` and are snapshotted per run.

Confirm: Metabase remains the editor for `AGENT_CONFIG` and `APPROVALS`.
