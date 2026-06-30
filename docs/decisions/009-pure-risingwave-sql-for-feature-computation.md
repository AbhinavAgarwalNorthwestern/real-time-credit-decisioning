# ADR 009: Pure RisingWave SQL for feature computation

**Status:** Accepted
**Date:** 2026-06-06
**Decision makers:** Platform owner

## Context

ADR 002 established RisingWave's materialized views as the feature store
(replacing Feast). What ADR 002 left ambiguous: **where does the actual
feature computation happen?** Two implementations of
"RisingWave-as-feature-store" were possible:

- **Option A** — Quixstreams stateful aggregator computes the feature
  values in Python, writes results back to Kafka, RisingWave consumes
  those results and serves them via SQL.
- **Option B** — RisingWave consumes the raw event stream directly via
  `CREATE SOURCE FROM KAFKA`, and the feature computation IS the
  materialized view definition (windowed aggregations, joins,
  derivations all happen in SQL inside RW).

Both produce a RisingWave MV that the decisioner queries for features.
But Option A makes RW a storage layer with Python doing the work, while
Option B makes RW genuinely the compute+storage feature store. They look
the same from the decisioner's perspective; they're very different from
the operator's perspective.

## Decision

We adopt **Option B**: pure RisingWave SQL for feature computation.

The `services/behavioral_features/` Python service is removed from the
architecture. RisingWave's `CREATE SOURCE FROM KAFKA transactions` reads
the producer's events directly; behavioral features are computed as
materialized views in SQL over that source.

If a future feature requires non-SQL computation (e.g., calling an ML
model for an embedding, hitting an external API), we reintroduce a thin
Python service for that specific feature only — not a general feature
engine.

## Consequences

### Positive

- **Single source of truth** — the MV DDL *is* the feature definition.
  No risk of Python aggregator and RW schema drifting.
- **Single state store** — only RW maintains state. Quixstreams' RocksDB
  state for features doesn't exist, eliminating the dual-state-store
  debugging problem.
- **Less code** — `services/behavioral_features/` (~600 LOC across
  config, aggregator, main, tests) becomes ~80 lines of SQL. Day 1 buys
  ~2 hours back.
- **Cleaner audit / lineage** — "why does this feature have this value?"
  is answered by reading one SQL file plus the upstream source. No
  Python state to inspect.
- **Matches ADR 002's intent more cleanly** — RisingWave is genuinely
  the feature store (compute + storage), not just storage after Python
  compute.
- **One fewer service** to deploy, monitor, scale, restart.
- **Stronger interview signal** — "I used RisingWave's materialized
  views as the feature store" is more distinctive than "I built a
  Python streaming feature engine."

### Negative

- **SQL dialect coupling** — feature definitions are now tied to
  RisingWave's specific streaming SQL extensions (`TUMBLE`, `HOP`,
  `EMIT ON UPDATE`, etc.). Migrating to Flink SQL would require porting
  the DDL.
  *Mitigation*: the JVM-equivalents doc (`docs/jvm_equivalents.md` —
  Day 7) maps these to Flink SQL 1:1.
- **Some features are awkward in SQL** — complex Python logic (custom
  state machines, ML-derived embeddings) doesn't fit naturally.
  *Mitigation*: introduce a Python helper service for those specific
  features only.
- **Schema evolution requires DDL migrations** — adding a feature means
  `CREATE MATERIALIZED VIEW v2 AS ...`. Quixstreams Python lets you add
  a field with one PR.
  *Mitigation*: RisingWave supports online backfill on MV creation,
  so v1→v2 cutover is non-blocking.
- **Unit-testing individual feature computations is harder** — you
  can't isolate a feature's logic in a pytest.
  *Mitigation*: write SQL tests that insert synthetic rows into a test
  table and assert MV output. Pattern established Day 1 in
  `deployments/dev/risingwave/tests/` (Day 2 deepening).
- **Larger SQL files** — every feature in one place can become a giant
  DDL.
  *Mitigation*: split per concern (`01_mv_velocity.sql`,
  `02_mv_utilization.sql`, …) once we have more than 3-4 features.

## Why this isn't a general "SQL > Python for streaming features"

It's specific to our project:

- **All planned features are SQL-expressible** (velocity, utilization,
  MCC entropy, time-since-last, geo-anomaly z-score, macro sentiment).
  Each pattern is a standard window or aggregation.
- **The team has SQL skills** (Oracle Certified SQL Expert) — readability
  is on par with Python.
- **No ML-derived features today** that require Python-side computation.
- **Reviewers can audit a single SQL file** for the feature catalog.

If our project required ML embeddings, external API enrichments, or
custom Python stateful logic per feature, Option A or a hybrid would
be the right answer. They don't, so it isn't.

## Alternatives considered

- **Option A — Quixstreams stateful aggregator + RW storage**. Rejected:
  makes RW a storage layer, defeats ADR 002's framing, requires
  maintaining two state stores, doubles operational surface.
- **Hybrid (B + thin Python service for normalization)**. Considered
  for the case where raw events need PII stripping or schema
  normalization before RW consumes them. **Reserved as a future option**
  — not needed today because the producer (Day 1 D2) emits clean
  schema-conformant events.
- **Feast on top of RisingWave**. Rejected at ADR 002.

## Related

- ADR 002 — RisingWave as feature store (this ADR clarifies the
  implementation choice ADR 002 left open)
- ADR 004 — Monolithic decisioner (decisioner reads features from the
  MV via async Postgres — unchanged by this decision)
- ADR 008 — Python FastAPI decisioner (same — decisioner reads from
  MV; this ADR doesn't affect ADR 008)
- `services/behavioral_features/` — **deleted as part of this ADR**
- `docs/02_data_and_features.md` — to be updated with SQL implementations
  of each planned feature (Day 2)
- `deployments/dev/risingwave/00_source_transactions.sql` — the source DDL
- `deployments/dev/risingwave/01_mv_behavioral_features.sql` — the MV DDL
