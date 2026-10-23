# Deployment

One `python:3.12-slim` image serves both processes. `api` runs `churn serve`. `worker` runs `churn worker`. The image user is `churn`, not root. LightGBM needs `libgomp1`, which the image installs.

This sandbox does not run Docker. The steps below are the operator checklist. A successful live cycle is not claimed here.

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

- `subscription_events`: set `object` to the production subscription-event relation.
- `products`: set `object` to the production product catalog.
- `email_events`: set `object` to the production email-event relation.
- `support_tickets`: set `object` to the production support-ticket relation.
- `quiz_responses`: set `object` to the production quiz-response relation.
- `customers_pii`: leave `pii: true`. The pipeline validates the metadata and does not render this view. Do not map it into a feature.
- `subscriptions`: set `object` to the production subscription relation.
- `orders`: set `object` to the production order relation.

`CHURN_CONTRACT_PATH` can point at a second contract file if you want to keep the synthetic mapping beside the production one. That is still a config change.

## Live checklist

Not run in this sandbox. On a machine with the Snowflake trial exported into the environment:

```
docker compose run --rm api churn bootstrap
pytest -o addopts= -m snowflake
```

The snowflake marker migrates, generates a tiny population, trains, and runs one cycle. A green default suite, including a skipped snowflake test, is not that proof. R8.7 is confirmed only when Andre follows this page on a clean machine and the cycle succeeds.
