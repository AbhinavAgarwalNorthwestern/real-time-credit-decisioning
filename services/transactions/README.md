# transactions

Synthetic credit-transaction stream producer for the finance domain.

## What it does

Emits realistic credit-card transaction events into the `transactions`
Kafka topic. Downstream `behavioral_features` consumes these and computes
stateful per-customer aggregates that materialize into RisingWave for
online serving (per ADR 002: RisingWave-as-feature-store).

It's a Quixstreams producer-only service that bridges an "external"
event source (the synthetic generator) to a Kafka topic. Same pattern
as any Kafka producer — see the JVM equivalent
(Kafka Producer API in Java) for an enterprise-stack mapping when
discussing it in interviews.

## Status

**Skeleton (Day 0).** Implementation lands on Day 1.

## Day 1 acceptance criteria

- Configurable customer cohort + segment mix (low/med/high risk × new/tenured)
- Reproducible via `--seed`
- Realistic transaction velocity, MCC distribution, geo patterns
- Synthetic drift injection (`--inject-drift {velocity,utilization,geo}`)
  for demo-time concept-drift scenarios on Day 5
