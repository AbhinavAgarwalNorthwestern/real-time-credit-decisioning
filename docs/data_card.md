# Data Card

Follows the structure of [Google's Data Cards Playbook](https://research.google/pubs/data-cards-purposeful-and-transparent-dataset-documentation-for-responsible-ai/).

## Dataset overview

- **Name**: synthetic credit-card transaction stream
- **Owner**: this project
- **Created**: 2026-06-03 (synthetic generator); ongoing stream
- **License**: portfolio use only — code under repository license; data is generated, not collected
- **Source**: `services/transactions` synthetic generator (Day 1)
- **Why synthetic and not real data**: there is no public credit-card
  transaction stream that can be used legitimately. Synthetic data, with
  a documented generator, is the defensible alternative. A reviewer can
  rerun the generator and reproduce the dataset.

## Intended use

- Training and evaluation of per-segment uplift models for the
  credit-limit-increase / fraud-check decisioning system
- Off-policy evaluation of bandit decision policies
- Drift-injection scenarios for the drift-monitor demo (Day 5)

## Out-of-scope use

- Any inference about real customer behavior (data is synthetic)
- Training a model that will see production traffic at a real bank
  without retraining on real data
- Fairness benchmarking (the synthetic generator does not include
  protected attributes; therefore cannot measure their effect)

## Sensitive features

The schema deliberately **excludes** ECOA-protected attributes (race,
color, religion, national origin, sex, marital status, age, public
assistance receipt) and behavioral proxies for them. See
`docs/01_problem_and_domain.md` for the regulatory motivation.

## Schema

| Column | Type | Description | Source field in event |
|--------|------|-------------|----------------------|
| `event_id` | UUID | Unique per event | generator |
| `customer_id` | string | Stable per synthetic customer | generator |
| `timestamp_ms` | int64 | Epoch ms; monotonic per customer | generator |
| `amount` | float | Dollars | generator |
| `mcc` | int | Merchant category code (4-digit ISO) | generator |
| `merchant_id` | string | Synthetic | generator |
| `geo_lat`, `geo_lon` | float | Clustered around customer home | generator |
| `is_card_present` | bool | | generator |
| `current_balance` | float | After this event | generator |
| `credit_limit` | float | Current limit | generator |
| `payload_version` | int | Schema-evolution marker | generator (always 1 at Day 1) |

(Behavioral feature columns are documented in
`docs/02_data_and_features.md` under "Behavioral feature view".)

## Generation process

_(Day 1 deliverable.)_

Synthetic generator parameters:
- **Cohort size**: ~10 000 customers by default; configurable
- **Segment mix**: low/med/high risk × new/tenured; configurable
- **Reproducibility**: every run seeded; same seed yields identical stream
- **Realism guardrails**: velocity, MCC distribution, geo concentration
  match published industry distributions (citations in `services/transactions/README.md` once Day 1 lands)
- **Drift injection**: optional `--inject-drift {velocity,utilization,geo}`
  for Day 5 demo scenarios

## Known limitations

- Synthetic — does not capture real-world customer behavioral
  heterogeneity, especially in the long tail (one-time-large-purchase
  customers, dormant-then-active reactivation patterns)
- Geographic clustering is parametric, not learned from a real
  distribution
- MCC distribution is industry-average; does not capture
  cardholder-segment-specific patterns

## Maintenance

- Schema version bumps on `payload_version` rather than mutating
- Distribution-change requests are logged in `docs/incidents.md` and,
  if architecturally relevant, ADR'd

## Status

Schema is stable through Day 0. The generator implementation lands
Day 1.
