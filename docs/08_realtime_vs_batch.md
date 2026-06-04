# 08 — Real-Time vs Batch: why the planes are split

The single most common architectural mistake in ML systems is treating
"real-time" and "batch" as a stylistic choice. They are not. They are
different SLO regimes with different failure modes, and conflating them
forces a system to optimize for the worst of both.

This chapter codifies why we split them.

## The three SLO regimes

| Plane | SLO | What can be late by minutes? | What can be late by hours? |
|-------|-----|------------------------------|----------------------------|
| **Decision (synchronous)** | p99 < 50 ms | nothing | nothing |
| **Streaming (asynchronous)** | seconds for features; minutes for drift | feature freshness | drift detection |
| **Batch (orchestrated)** | minutes for a flow; hours for cohort training | retraining cadence | challenger validation, OPE backtest |

A failure on one plane should not cascade to a different plane's SLO.
The Kafka topic boundary between planes makes this property mechanical.

## Why not "just" do everything real-time

A common temptation: stream every computation; never run batch jobs.
The problems:

1. **Stateful aggregations over large windows** (90-day spend baseline,
   30-day MCC entropy) are cheap as batch, expensive as stream. A
   Quixstreams reduce over 90 days of state needs to keep 90 days of
   per-key state hot — that's a lot of RAM. The batch equivalent runs
   in a few minutes on demand.
2. **Hyperparameter search** (Optuna with many trials) is fundamentally
   not a streaming workload. Trials are independent; the orchestrator
   wants `foreach`, not a continuous flow.
3. **Off-policy evaluation** requires looking at a slice of logged
   decisions in retrospect. That's an analytical query, not a stream
   transform.

## Why not "just" do everything batch

The opposite temptation: a nightly batch that scores everyone, with a
cache for online lookup. The problems:

1. **Latency on the request path** would be unmeasurable on the data
   path itself. The cache hides the latency-budget problem but doesn't
   solve it (every cache hit is a stale prediction).
2. **Drift response** has a minimum lag of one batch cycle (typically
   ~24 hours). For card fraud or a flash flash-crash propagation,
   that's much too slow.
3. **Cold-start customers** never have a recent prediction in the cache.
   Synchronous inference handles them naturally; batch-with-cache
   does not.

## The right split — what goes where

| Concern | Plane | Why |
|---------|-------|-----|
| Customer event ingestion | Streaming | Inherently real-time |
| Behavioral features (short windows) | Streaming | Used within seconds of the event |
| Behavioral features (long windows, 30+ days) | Hybrid: streaming with batch snapshot for the long tail | RAM cost trade-off |
| Per-event decision | Synchronous | Customer is waiting |
| Audit log write | Streaming (async) | Don't block the response |
| Outcome ingestion + join | Streaming | Needs to handle late events naturally |
| Drift computation | Streaming | Want to react in minutes |
| Drift event → retrain trigger | Streaming → batch handoff | Drift detection is continuous; retraining is discrete |
| Retraining | Batch | Foreach over segments; not a stream |
| Off-policy evaluation | Batch | Analytical query over logged decisions |
| Champion-challenger promotion | Batch (with manual gate) | Discrete decision; needs human approval |
| Model registry alias swap | Batch | One-time event per promotion |
| Monitoring dashboards | Streaming (aggregations) | Want to see metrics within seconds |
| Cost accounting | Batch | Daily aggregate |

## How the planes communicate

The only allowed cross-plane communications are:

1. **Kafka topics** — append-only event streams. Producer doesn't know
   the consumer; consumer is decoupled in time.
2. **MLflow model registry** — versioned model artifacts with aliases.
   Batch plane writes; decision plane reads.
3. **RisingWave materialized views** — streaming plane writes the
   underlying CDC, all planes can read the MV.

Direct service-to-service calls **never** cross plane boundaries. This
is what makes the planes independently deployable.

## What this means for resume framing

A frequent ML-eng question: *"do you do real-time ML or batch ML?"*
The senior answer is "yes" — every production ML system that processes
customer events runs both planes, and the architectural maturity comes
from knowing what goes where, not from being purist about either.

This system has all three planes deployed, each with documented SLOs,
each with its own runbook, each with its own monitoring. That's the
demonstrable artifact.

## Status

Chapter is stable through Day 0. Cross-plane communication patterns are
exercised once each plane has implementation behind it (Days 1–6).
