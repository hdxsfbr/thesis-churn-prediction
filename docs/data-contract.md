# Source data contract

The pipeline reads source data only through `config/source_contract.yaml`. Feature and label SQL select from
one CTE per **logical view**, never from physical names, so mapping Thesis's real views is a YAML edit
(no code change). Set another file with `contract_path` in `config/settings.yaml` or the env var
`CHURN_CONTRACT_PATH`.

## Contract file format

```yaml
schema: THESIS_SYNTH            # expands "{schema}" in every object name
views:
  <logical_view>:
    object: "{schema}.<table_or_view>"   # physical object (may be database.schema.name)
    pii: false                         # true: validate metadata, never render
    dedup_key: <source column>           # event / entity id; one row per value survives
    loaded_at: <source column>           # load timestamp; latest wins, treated as UTC
    columns:
      <logical_name>: {source: <source column>, type: <type>, tz: <optional>}
```

Types: `varchar`, `number`, `timestamp` (naive), `timestamp_tz` (offset-aware), `boolean`.
For a naive `timestamp` column, `tz: <IANA zone>` names the zone it was written in.

## What the contract views do

1. **Renames**: `source` column becomes the logical name.
2. **UTC normalisation**: `timestamp_tz` values are converted to UTC; naive timestamps with `tz` are
   converted from that zone; naive timestamps without `tz` are taken to be UTC already. All outputs are
   `TIMESTAMP_NTZ` holding UTC wall time.
3. **Dedup**: `QUALIFY ROW_NUMBER() OVER (PARTITION BY dedup_key ORDER BY loaded_at DESC) = 1`, so the row
   with the latest `loaded_at` wins.
4. **Pseudonymous key (R7.1)**: `subscriber_key = SHA2(pepper || customer_id, 256)`. The pepper comes from env
   `CHURN_KEY_PEPPER` (never the repo). The raw `customer_id` is not exposed by the views.

## Validation

`contract.validate` runs at the start of every cycle. Each mapped object must exist and carry every mapped
source column plus `dedup_key` and `loaded_at`, with a type compatible with the declared type. Any mismatch
fails the run as `ContractError` (listing every problem) before anything but the `RUNS` row is written.

## Key map

`CHURN_PRIVATE.KEY_MAP(subscriber_key, customer_id)` (migration `V003`) is filled by every cycle
(insert-only MERGE). It is the only table holding the raw id; no `CHURN_OPS` table has a `customer_id` column.
In Snowflake, grant `CHURN_PRIVATE` to the sender and app roles only.

## Logical views and columns

### `subscriptions` (dedup key `subscription_id`, loaded_at `loaded_at`)

| Logical column | Type | Meaning |
|---|---|---|
| `customer_id` | varchar | Raw customer id (hashed into `subscriber_key`; not exposed) |
| `subscription_id` | varchar | Subscription id |
| `status` | varchar | ACTIVE, PAUSED, PAST_DUE, CANCELLED, ... |
| `started_at` | timestamp | Subscription start |
| `cancelled_at` | timestamp | Cancellation time, NULL if not cancelled |
| `cancel_type` | varchar | Voluntary / involuntary |
| `cancel_reason_code` | varchar | Cancellation reason code |
| `price_usd` | number | Price per interval |
| `interval_days` | number | Billing interval in days |

### `orders` (dedup key `order_id`, loaded_at `loaded_at`)

| Logical column | Type | Meaning |
|---|---|---|
| `order_id` | varchar | Order id |
| `customer_id` | varchar | Raw customer id (hashed into `subscriber_key`; not exposed) |
| `ordered_at` | timestamp | Order time |
| `blend_sku` | varchar | Product SKU |
| `amount_usd` | number | Order amount |
| `discount_usd` | number | Discount applied |
| `refund_usd` | number | Refunded amount |
| `refunded_at` | timestamp | Refund time |

Every non-PII view yields `loaded_at`; only views carrying `customer_id` yield `subscriber_key`.
`subscription_events` joins through `subscription_id`, and `products` through `blend_sku`.
All eight views are now mapped by the default synthetic contract (T29).

## Optional views read by the feature SQL

The feature SQL (`sql/features/`) also reads the logical views below. Each is optional: when the contract
does not map one, `churn.features.builder` substitutes an empty typed CTE with these columns, declared by
the `-- stub <view>: ...` line of the domain file. Rows are still scored. The domain's counts are 0, its rates,
deltas and recencies are NULL, and its `has_<domain>` indicator is 0 (R2.4). To light a domain up, map the
view in the YAML with at least these logical columns (plus `dedup_key` and `loaded_at`).
The default synthetic contract maps all of them: no stub is used in that pipeline.

| Logical view | Domain file | Columns the feature SQL reads |
|---|---|---|
| `subscription_events` | `subscription_events.sql` | `subscription_id`, `event_id`, `event_type` (`skip`, `pause`, `blend_switch`, ...), `event_ts`. Joined to the subscriber's active subscription on `subscription_id`. |
| `email_events` | `email.sql` | `customer_id` (→ `subscriber_key`), `event_id`, `event_type` (`send`, `open`, `click`, `unsubscribe`), `event_ts` |
| `support_tickets` | `support.sql` | `customer_id` (→ `subscriber_key`), `ticket_id`, `created_at`, `category`, `escalated` (boolean), `sentiment_score` (number) |
| `quiz_responses` | `quiz.sql` | `customer_id` (→ `subscriber_key`), `quiz_session_id`, `question_key`, `answer_value`, `answered_at`, `recommended_sku` (one row per answer, grouped into sessions by `quiz_session_id`) |

### Complete additional mappings (T29)

All these objects live under `{schema}`; each has a UTC `loaded_at` timestamp.

| View | Dedup key | Complete columns and types (in addition to loaded_at) |
|---|---|---|
| subscription_events | event_id | subscription_id, event_id, event_type (varchar); event_ts (timestamp) |
| products | blend_sku | blend_sku, blend_name, goal_category (varchar); price_usd (number) |
| email_events | event_id | customer_id, event_id, event_type, campaign_id, flow_id (varchar); event_ts (timestamp_tz) |
| support_tickets | ticket_id | customer_id, ticket_id, category (varchar); created_at (timestamp); escalated (boolean); resolution_hours, sentiment_score (number) |
| quiz_responses | answer_id | answer_id, customer_id, quiz_session_id, question_key, answer_value, recommended_sku (varchar); answered_at (timestamp) |
| customers_pii | customer_id | customer_id, first_name, email, phone, address (varchar) |

`customers_pii` is explicitly `pii: true`. Validation checks information_schema metadata only.
The default CTE renderer and key-map builder skip it; explicitly rendering it raises `ContractError`.
Fake identifying fields exist only in the synthetic source, never in feature CTEs or profile output.
`latent_truth` is **not** mapped: customer_id, archetype, price_sensitivity, fatigue, hazard_per_day,
and churn_cause are evaluation-only truth in THESIS_SYNTH.

To switch sources, copy the YAML, change schema/object names and column `source` mappings for all
views above, keep `customers_pii.pii: true`, set `CHURN_CONTRACT_PATH`, and run validation and a cycle.
No Python/SQL feature changes are necessary. Supply source load times and timestamp zones accurately.

### Synthetic source profile

`churn synth --scale tiny --profile` replaces all source tables and latent truth, writes
`docs/data-profile.md`, and appends one profile snapshot to `CHURN_OPS.DATA_PROFILE`.
The latter stores `profiled_at`, `section`, `item`, `value`, `scale`, `seed`; sections are rows,
edge_case and churn_timeline. Edge shares count affected subscribers (categories overlap), while
row counts include duplicates. Offset share counts customers with offset-bearing email events;
warehouses may normalise the actual stored offset. Event injection rates are 0.5% duplicates,
1% late loads (+7 days), and 1% optional null fields. Missing domains are independently assigned:
15% no quiz, 5% no email, 65% explicitly no tickets (more naturally have no tickets).
The checked-in report is tiny-scale content evidence, not a full-scale throughput claim (T27).

Migrations are additive `synth/V003` and `V015`; V002 is intentionally not reused because historical
MR !5 shipped it. Recreate local developer databases that applied the lost MR !5 migrations.

`subscriptions` and `orders` are required. Every feature counts a row only if its event time is before
`as_of` and its `loaded_at` is on or before `as_of` (R2.3). The generated catalogue is in `docs/features.md`.
