# outcome_collector

Joins downstream outcomes back to the originating decisions for off-policy
evaluation (IPS / SNIPS / Doubly-Robust estimators on Day 6).

## What it does

Consumes:
- `decisions` Kafka topic (every decision logged with propensity + chosen action)
- `outcomes` Kafka topic (CLI accepted? fraud confirmed? customer churned?
  spend delta?)

Performs a time-bounded join (customer_id + decision_id + outcome_horizon),
writes the joined record to a RisingWave `decision_outcomes` table.

Day 6 off-policy-evaluation flow reads `decision_outcomes` to estimate
counterfactual policy performance via IPS, self-normalized IPS, and
Doubly-Robust estimators. The off-policy gate is what lets a challenger
be promoted to canary without waiting for live A/B (per the production
patterns doc).

## Why a separate service?

The join is *streaming, time-bounded, and stateful* — exactly the shape
Quixstreams is built for. Keeping it as its own service avoids coupling
the `decisioner`'s tight latency budget (ADR 004) to outcome ingestion.

## Status

**Skeleton (Day 0).** Implementation lands on Day 6.

## Day 6 acceptance criteria

- Late-outcome tolerance configurable (default: 7 days)
- Output rows tagged with outcome-arrival lag for IPS variance estimation
- RisingWave `decision_outcomes` table with the schema documented in
  `docs/data_card.md`
