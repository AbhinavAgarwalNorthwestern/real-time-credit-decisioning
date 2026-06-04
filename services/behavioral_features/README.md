# behavioral_features

Streaming behavioral-feature transformer for the finance domain.

## What it does

Consumes the `transactions` Kafka topic and computes stateful per-customer
features in real time: velocity (events/min over rolling windows),
utilization (balance/limit ratio), MCC-category entropy, time-since-last-
transaction, geo-anomaly score. Writes the resulting feature rows into
RisingWave as a materialized view consumed by `decisioner` at decision time.

It's the canonical Quixstreams stateful-transform pattern: consume an
input topic, maintain stateful per-key aggregations across rolling
windows, emit derived rows downstream. The JVM equivalent (Kafka
Streams `KStream` + `KTable` with windowed aggregations, or Flink SQL
with `TUMBLE()` windows) maps 1:1 — see
[`docs/jvm_equivalents.md`](../../docs/jvm_equivalents.md) (Day 1+) for
the side-by-side mapping.

## Why RisingWave directly (no Feast)

Per ADR 002 (`docs/decisions/002-risingwave-as-feature-store-not-feast.md`).
RisingWave's MV-over-CDC semantics give us point-in-time-correct training
joins and sub-10ms online lookups without an additional service.

## Status

**Skeleton (Day 0).** Implementation lands on Day 1.

## Day 1 acceptance criteria

- Stateful windowed aggregations via Quixstreams `reduce` / `tumbling_window`
- Schema versioned via `feature_view_version` metadata column
- Sink into RisingWave via CDC topic (RW subscribes; no direct write needed)
- Late-event tolerance configurable
