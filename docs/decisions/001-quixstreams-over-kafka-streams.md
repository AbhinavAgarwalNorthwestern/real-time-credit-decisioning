# ADR 001: Quixstreams over Kafka Streams / Flink for stream processing

**Status:** Accepted
**Date:** 2026-06-03
**Decision makers:** Platform owner

## Context

We need stateful stream processing for:

- Per-customer behavioral features computed in-stream (velocity, utilization,
  MCC entropy, time-since-last)
- News-sentiment joining onto transaction stream
- Drift signals computed continuously over the prediction distribution
- Real-time updates of bandit-arm posteriors (stretch goal)

The team writes Python; the broader system (training, serving model wrapping,
orchestration glue) is Python-first. Choosing a JVM stream processor forces
two-language operations and slows iteration.

Options evaluated:

|                                | Kafka Streams (JVM) | Flink (JVM)       | Spark Structured Streaming | Bytewax (Python) | Quixstreams (Python) | Faust (Python) |
|--------------------------------|---------------------|-------------------|----------------------------|-----------------|----------------------|----------------|
| Native Kafka client            | First-class         | First-class       | Source                     | Yes             | First-class          | Yes            |
| Stateful windowing             | Mature              | Mature            | Micro-batch                | Maturing        | Yes                  | Yes            |
| Python-native API              | No                  | PyFlink (wrapper) | PySpark                    | Yes             | Yes                  | Yes            |
| JVM operational burden         | Yes                 | Yes               | Yes                        | No              | No                   | No             |
| Production adoption            | Wide (banks)        | Wide              | Wide                       | Niche           | Growing (fintech)    | **Deprecated** |
| Local-cluster parity           | Painful             | Painful           | Painful                    | Easy            | Easy                 | Easy           |
| Throughput ceiling (single pod)| Very high           | Very high         | Very high                  | Moderate        | Moderate             | Low            |

## Decision

We use **Quixstreams** for in-Python stateful stream processing on top of Kafka.

## Consequences

### Positive

- Single language (Python) end-to-end — same engineers write features,
  models, decisioner, and stream transforms
- `StreamingDataFrame` API is familiar to anyone who knows Pandas; stateful
  windowing and reduce/initialize patterns are first-class
- No JVM operations layer; deployments are normal K8s pods on the same
  Python image we already build
- Local-cluster parity is excellent — `app.run()` in dev is byte-identical
  to production
- The crypto pipeline already uses it; reusing infra is a real cost win

### Negative

- Throughput ceiling is lower than Kafka Streams / Flink. Not a problem at
  our scale (5k decisions/sec target). Would become a problem at 100k+/sec —
  flagged as a future-replacement trigger
- Banks more commonly run Kafka Streams or Flink in production. Mitigated
  by clear articulation in interviews (see `06_production_patterns.md`):
  *"the architectural pattern is identical; the JVM swap is a local change
  bounded by the Kafka topic contract"*
- Quixstreams ecosystem is younger than Flink's; fewer Stack Overflow answers
- Newer library, faster API changes — pin versions strictly

## Alternatives considered

- **Kafka Streams**: forces a JVM stack alongside Python. Operationally
  heavier for a single-engineer portfolio project. Right choice at JPM scale.
- **Apache Flink**: same JVM cost, plus a separate cluster to operate.
  Overkill for our throughput.
- **PyFlink**: thin wrapper over Java; you still operate a Flink cluster
  and the wrapper has known edge cases. No real win over Quixstreams.
- **Spark Structured Streaming**: micro-batch model has higher per-event
  latency than true streaming. Wrong tool for sub-second behavioral features.
- **Bytewax**: Python-native and improving; chose Quixstreams instead because
  the crypto pipeline already depends on it and the Kafka integration is more
  mature in Quixstreams today.
- **Faust**: deprecated. Hard no.

## Related

- ADR 002: RisingWave is the materialized-view layer that consumes
  Quixstreams output
- `docs/05_architecture.md`: where Quixstreams sits in the three-plane diagram
- `docs/06_production_patterns.md`: the "swap to Kafka Streams" interview
  talking points
