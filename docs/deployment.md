# Deployment

One `python:3.12-slim` image serves both processes. `api` runs `churn serve`. `worker` runs `churn worker`. The image user is `churn`, not root. LightGBM needs `libgomp1`, which the image installs. The image installs the `snowflake` and `llm` extras. Without a provider key, mail stays on the template even though the SDK is installed. Compose restarts `api` and `worker` unless they are stopped. A blank `CHURN_KEY_PEPPER` with `CHURN_BACKEND=snowflake` refuses to start. The local DuckDB default pepper stays when that variable is unset.

The image was built locally as `churn-submission:submit`. That build is not a deployed cycle. The steps below are the clean-machine checklist. The 2026-10-25 smoke ran from the local checkout, not from this image.

## Clean-machine deploy

1. Install Docker Engine and the Compose plugin.
2. Clone the repository and check out the release you intend to run.
3. Copy `.env.example` to `.env`. Fill `CHURN_KEY_PEPPER`, `CHURN_API_KEY`, and either the DuckDB path or the Snowflake variables. Leave the LLM keys blank to keep every email on its template.
4. For Snowflake, set `CHURN_BACKEND=snowflake` and mount the private key at `SNOWFLAKE_PRIVATE_KEY_PATH`. Leave `SNOWFLAKE_PASSWORD` empty when the key is set.
5. Build and start both services:

```
docker compose up -d --build
```

6. On a new warehouse, bootstrap once, then train:

```
docker compose run --rm api churn bootstrap
docker compose run --rm api churn train
```

7. `GET /health` with header `X-API-Key` returns `{"status":"ok"}`. `GET /ready` stays 503 until a promoted model exists. The worker catches up today's scheduled cycle if that run is missing.

Local DuckDB keeps the warehouse file on the `churn-data` volume. Both services share it.

## Switch to production views

The pipeline reads logical views. Physical names live only in `config/source_contract.yaml`. Pointing the cycle at the real Thesis warehouse is a config edit. Do not change Python, feature SQL, or the model code.

Set `schema` to the production schema, then set each view's `object` to the production relation. Keep the logical column names. Change `source` only when the production column has a different name. Run `churn cycle` after the edit. `contract.validate` fails the run before any write if an object or column is missing.

- `subscription_events`: columns `subscription_id`, `event_id`, `event_type`, `event_ts`. Set `object` to the production subscription-event relation.
- `products`: columns `blend_sku`, `blend_name`, `goal_category`, `price_usd`. Set `object` to the production product catalog.
- `email_events`: columns `customer_id`, `event_id`, `event_type`, `campaign_id`, `flow_id`, `event_ts`. Set `object` to the production email-event relation.
- `support_tickets`: columns `customer_id`, `ticket_id`, `created_at`, `category`, `escalated`, `resolution_hours`, `sentiment_score`. Set `object` to the production support-ticket relation.
- `quiz_responses`: columns `answer_id`, `customer_id`, `quiz_session_id`, `question_key`, `answer_value`, `answered_at`, `recommended_sku`. Set `object` to the production quiz-response relation.
- `customers_pii`: columns `customer_id`, `first_name`, `email`, `phone`, `address`. Leave `pii: true`. The pipeline validates the metadata and does not render this view. Do not map it into a feature.
- `subscriptions`: columns `customer_id`, `subscription_id`, `status`, `started_at`, `cancelled_at`, `cancel_type`, `cancel_reason_code`, `price_usd`, `interval_days`. Set `object` to the production subscription relation.
- `orders`: columns `order_id`, `customer_id`, `ordered_at`, `blend_sku`, `amount_usd`, `discount_usd`, `refund_usd`, `refunded_at`. Set `object` to the production order relation.

`CHURN_CONTRACT_PATH` can point at a second contract file if you want to keep the synthetic mapping beside the production one. That is still a config change.

## Live checklist

On 2026-10-25, `pytest -o addopts= -m snowflake` passed in 196.85s against the trial account: migrate, tiny synth, train, one cycle, status SUCCEEDED, scored greater than 0. That run used the local checkout and key-pair auth. It was not `docker compose`. On a clean machine, with the trial exported into the environment:

```
docker compose run --rm api churn bootstrap
pytest -o addopts= -m snowflake
```

The snowflake marker migrates, generates a tiny population, trains, and runs one cycle. A green default suite, including a skipped snowflake test, is not that proof. R8.7 is confirmed only when Andre follows this page on a clean machine and the cycle succeeds.
