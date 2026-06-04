# drift_monitor

Concept-drift monitor for the finance domain. Closes the production-ML
loop by triggering retraining when input distributions or prediction
distributions shift beyond tolerance.

## What it does

Consumes:
- The `decisions` Kafka topic (the audit log written by `decisioner`)
- The behavioral-feature materialized view from RisingWave (rolling window)

Computes:
- **PSI (Population Stability Index)** per feature against a reference window
- **KS (Kolmogorov-Smirnov)** on prediction distributions
- **ADWIN** online drift detector for non-stationary streams (via the `river` lib)

Emits `drift_detected` events to Kafka with topic + feature attribution.
These events trigger the `retraining_flow` via Argo Events (per ADR 003 —
Metaflow on K8s).

## Status

**Skeleton (Day 0).** Implementation lands on Day 5.

## Day 5 acceptance criteria

- PSI calculator with configurable rolling-window length + threshold
- ADWIN detector with significance level configurable from env
- Drift event schema: `{detector, feature, timestamp, severity, evidence}`
- Grafana panel reading drift metrics from a Prometheus exporter
