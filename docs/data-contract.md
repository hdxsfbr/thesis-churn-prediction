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

Every view also yields `subscriber_key` and `loaded_at`. Further views (email, support, quiz) are added to
the same YAML by later tickets and must be listed here.
