# ADR 002: RisingWave materialized views as feature store (no Feast layer)

**Status:** Accepted
**Date:** 2026-06-03
**Decision makers:** Platform owner

## Context

The system needs a feature store that satisfies the standard four
requirements:

1. **Online lookup** — sub-10ms per-customer feature vector retrieval at
   serving time
2. **Offline read** — point-in-time-correct training joins (no future leakage)
3. **Single source of truth** — same feature definition produces the same
   value online and offline (no training/serving skew)
4. **Freshness** — features reflect recent events with bounded staleness

Two architectural shapes available:

- **Two-layer**: a streaming/batch processor computes feature values; a
  separate feature-store service (Feast, Tecton, in-house) stores and serves
  them with offline/online splits.
- **One-layer**: a streaming SQL engine maintains materialized views over
  the event stream; views are queryable both as the online source (point
  lookup via Postgres protocol) and as the offline source (snapshot reads
  for training).

We are already running RisingWave for streaming SQL and CDC subscriptions
(it is the substrate the crypto predictor reads from). Asking "should we
also run Feast?" is a real question.

## Decision

**No Feast layer.** RisingWave's materialized views directly serve as the
feature store. The MV-over-CDC pattern *is* the feature-store pattern; Feast
is one implementation of it. We choose the version that has fewer moving
parts.

Specifically:

- Behavioral features are defined as RisingWave SQL materialized views over
  the `behavioral_features` Kafka topic
- Online lookup: `decisioner` and `decision-api` query RisingWave directly
  via Postgres protocol (sub-10ms by design)
- Offline read for training: same view is snapshot-read with a time-bounded
  WHERE clause — point-in-time correctness is a property of the MV semantics
- Feature versioning: views are immutable per name; new feature versions
  create new views (`behavioral_features_v2`)

## Consequences

### Positive

- **No training/serving skew by construction.** Same SQL, same engine,
  same data — offline and online return the same value.
- **One fewer service to operate.** No Feast registry, no Feast server, no
  offline/online sync job, no schema drift between layers.
- **Point-in-time-correctness is enforced by the engine**, not by a feature
  store convention we have to remember to use correctly.
- **Sub-10ms online lookup** via Postgres protocol; no extra hop through a
  Feast online store (Redis or DynamoDB).
- **Lineage is automatic** — every feature value traces back to the source
  CDC stream slice through the MV graph.

### Negative

- **Lose the "Feast literacy" signal in interviews.** Mitigated by clear
  articulation of *why* we chose this and what Feast adds when the MV
  approach is insufficient (see Alternatives below).
- **RisingWave coupling.** A future migration to Materialize, Flink SQL, or
  ksqlDB would require rewriting the MV definitions. Acceptable: SQL is
  largely portable; the MV semantics are similar across all of these engines.
- **No native model-card / feature-card tooling** like Feast's. Mitigated by
  hand-rolled `docs/data_card.md` and `docs/model_card.md`.
- **Less off-the-shelf for non-streaming features.** If we later want
  static, infrequently-updated features (customer demographic enrichment
  from a batch source), we'll need to either CDC them into RisingWave or
  add a thin lookup service. Not a problem at current scope.

## Alternatives considered

- **Feast OSS**: would add a registry service, an online store (Redis or
  DynamoDB), an offline materialization job, and a CLI. Real value when the
  feature graph spans many sources and many teams; lower value when
  everything is already in a streaming SQL engine.
- **Tecton**: managed; commercial; overkill for portfolio scale.
- **Hand-rolled Redis + Postgres**: would re-implement what RisingWave gives
  for free. No win.
- **SageMaker Feature Store**: AWS-only. Defeats the cloud-agnostic goal
  (see ADR 006).
- **Tecton-style "feature view as code" with Feast on top of RisingWave**:
  considered. The Feast layer adds a typed API surface but doesn't materially
  change the data path. Rejected on simplicity grounds.

## Related

- ADR 001: Quixstreams writes to the Kafka topics RisingWave consumes via CDC
- ADR 006: Kustomize layout — RisingWave lives in `base/` as the canonical
  feature-store implementation; the AWS overlay does not swap it
- `docs/02_data_and_features.md`: the actual feature view definitions
- `docs/06_production_patterns.md`: the Feast-equivalence story for
  interview questions
