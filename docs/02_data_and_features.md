# 02 — Data and Features

Documents the synthetic transaction schema, the behavioral features
computed in stream, and the RisingWave materialized-view definitions
that make this the system's feature store (per ADR 002).

Detailed dataset documentation lives in `docs/data_card.md` (Google
Data Cards Playbook format). Detailed model-input documentation lives
in `docs/model_card.md`. This chapter is the conceptual overview.

## Transaction event schema (synthetic; produced by `services/transactions`)

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | UUID | Synthetic; unique per event |
| `customer_id` | string | Synthetic; ~10k cohort by default |
| `timestamp_ms` | int64 | Epoch ms; monotonic per customer |
| `amount` | float | Dollars |
| `mcc` | int | Merchant category code (4-digit ISO) |
| `merchant_id` | string | Synthetic |
| `geo_lat`, `geo_lon` | float | Synthetic; clustered around customer home |
| `is_card_present` | bool | |
| `current_balance` | float | Account balance after this event |
| `credit_limit` | float | Current limit |
| `payload_version` | int | Schema-evolution marker |

## Behavioral feature view (computed in stream by `services/behavioral_features`)

These features are computed continuously and materialized into a
RisingWave view consumed by `decisioner` at decision time.

| Feature | Window | Description |
|---------|--------|-------------|
| `velocity_5m` | 5 min tumbling | Transactions per 5 min |
| `velocity_1h` | 1 hour tumbling | Transactions per hour |
| `velocity_24h` | 24 hour tumbling | Transactions per day |
| `utilization` | event | `current_balance / credit_limit` |
| `mcc_entropy_24h` | 24 hour tumbling | Shannon entropy over MCC mix |
| `time_since_last_s` | event | Seconds since last txn for this customer |
| `geo_anomaly_24h` | 24 hour | Distance from rolling-mean home cluster |
| `avg_amount_30d` | 30 day tumbling | Mean txn amount |
| `amount_zscore_30d` | event | Z-score of this event's amount vs rolling baseline |

## Macro/sentiment feature view (reused from crypto pipeline)

The existing `news` + `news-sentiment` services emit a sentiment stream
that RisingWave joins with the behavioral features. We don't rebuild
this — just re-use the existing topic.

| Feature | Source | Description |
|---------|--------|-------------|
| `macro_sentiment_1h` | `news-sentiment` topic | Rolling-window sentiment score |
| `news_topic_attribution` | same | Top-N topics that drove the score |

## Decision audit schema (written by `services/decisioner`)

For every `/decide` call, one row is queued to Kafka topic `decisions`
and downstream-materialized into RisingWave for the off-policy eval flow.

| Field | Type | Used for |
|-------|------|----------|
| `decision_id` | UUID | Primary key |
| `customer_id` | string | Join key for outcomes |
| `decision_ts_ms` | int64 | Latency analysis + drift windowing |
| `segment` | enum | Segment routing audit |
| `champion_model_uri` | string | Model lineage |
| `challenger_model_uri` | string | What the challenger would have done |
| `champion_action` | enum | `OFFER_CLI` / `FRAUD_CHECK` / `NOTHING` |
| `champion_propensity` | float | Probability champion picked this action |
| `challenger_action` | enum | Counterfactual |
| `feature_vector_hash` | bytes | Reproducibility + replay |
| `shap_delta_baseline` | json | Per-feature attribution vs no-action baseline |
| `regulatory_flags` | json | ECOA-relevant flags (e.g. adverse-action eligibility) |

## Outcome event schema (consumed by `services/outcome_collector`)

| Field | Type | Notes |
|-------|------|-------|
| `outcome_id` | UUID | |
| `decision_id` | UUID | Foreign key to decisions |
| `outcome_ts_ms` | int64 | |
| `outcome_kind` | enum | `CLI_ACCEPTED`, `CLI_REJECTED`, `FRAUD_CONFIRMED`, `SPEND_DELTA_30D`, `CHURNED` |
| `outcome_value` | float | For continuous outcomes (e.g. spend delta) |

## Why RisingWave is the feature store (and not Feast)

Documented in ADR 002. Short version: MV-over-CDC semantics give us
point-in-time-correct training joins and sub-10 ms online lookups
without an additional service. SQL is the schema language.

## Status

Schema stable through Day 0. Day 1 populates the synthetic generator
and verifies the schema renders correctly into a RisingWave view. Any
schema drift gets a new `payload_version` rather than mutating the
existing one — schema is immutable in the same way ADRs are.
