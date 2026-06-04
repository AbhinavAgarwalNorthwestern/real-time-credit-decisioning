# 05 — Architecture

The system splits into three planes with different decomposition
philosophies, plus a model-registry plane that crosses all three. Each
plane is independently deployable and communicates with the others
only through Kafka topics and MLflow artifacts.

The detailed rationale is in **ADR 004**
(`docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md`).
This chapter is the consolidated view.

## The four planes

```
┌──────────────────────────────────────────────────────────────────────┐
│  STREAMING PLANE (always-on; per-topic microservices)                 │
│                                                                       │
│  transactions ──► Kafka ──► behavioral_features ──► RisingWave (MV)  │
│                                                                       │
│  news ──► Kafka ──► news-sentiment ──► Kafka ──► RisingWave (joined) │
│                                                                       │
│  outcomes ──► Kafka ──► outcome_collector ──► RisingWave             │
│                                                                       │
│  decisions ──► Kafka ──► drift_monitor ──► drift_events Kafka topic  │
└──────────────────────────────────────────────────────────────────────┘
                                  ▲                       │
                                  │                       │ drift event
                                  │ feature lookup        ▼
┌─────────────────────────────────┴────┐    ┌────────────────────────┐
│  SYNCHRONOUS DECISION PLANE          │    │  BATCH PLANE (Metaflow)│
│  (one Rust process; ADR 004)         │    │                        │
│                                      │    │  Argo Events / cron    │
│  decisioner                          │    │       ▼                │
│   1. RisingWave Postgres lookup      │    │  retraining_flow       │
│   2. Segment route                   │    │   - foreach segment    │
│   3. ONNX uplift inference           │    │   - train T-learner    │
│   4. Bandit select action            │    │   - export ONNX        │
│   5. Audit log enqueue ──► Kafka ────┘    │   - register MLflow    │
│   6. HTTP response                          │   - promotion gate    │
└─────────────────────────────────────┘    └──────────┬─────────────┘
                                                       │
                                            ┌──────────▼─────────────┐
                                            │  MODEL REGISTRY PLANE  │
                                            │                        │
                                            │  MLflow tracking +     │
                                            │   model registry       │
                                            │  MinIO / S3 artifacts  │
                                            │  (proxied per ADR 005) │
                                            └────────────────────────┘
```

## What flows where

| From → To | Protocol | What | When |
|-----------|----------|------|------|
| transactions → behavioral_features | Kafka topic `transactions` | raw event | continuous |
| behavioral_features → RisingWave | RisingWave CDC | feature row | continuous |
| decisioner → RisingWave | Postgres protocol | feature lookup query | per request |
| decisioner → MLflow | HTTPS, model registry | initial model load + periodic refresh | once + on alias change |
| decisioner → Kafka topic `decisions` | Kafka producer | audit log row | per request (async) |
| outcome_collector → RisingWave | RisingWave CDC | joined decision+outcome | continuous |
| drift_monitor → Kafka topic `drift_events` | Kafka producer | drift event | when PSI/KS/ADWIN fires |
| Argo Events → retraining_flow | K8s CRD | trigger | on drift event or schedule |
| retraining_flow → MLflow | HTTPS | new model artifact + registry entry | once per run |
| retraining_flow → MinIO/S3 | S3 API (proxied) | ONNX file | once per segment per run |

## What does NOT cross plane boundaries

- The streaming plane never reaches into the decisioner's address space
- The batch plane never serves a live request
- The decisioner never invokes the batch plane synchronously (the batch
  plane runs on its own schedule + on drift events)
- Models cross from batch → decision plane **only through the MLflow
  registry alias**, never via direct file write

This is the property that makes the three planes independently
deployable and operationally bounded — if a batch flow blows up at 3am,
the decisioner keeps serving the champion.

## Call graph for a single `/decide` request

```
POST /decide {customer_id, event}
   │
   ├─► sqlx::query(feature_view) ──► RisingWave Postgres protocol
   │       └─► returns features in ~5 ms
   │
   ├─► segment_router::route(features)                ~< 1 ms
   │       └─► returns segment enum (low/med/high × new/tenured)
   │
   ├─► onnx_session.run(features) for the segment's model   ~5–10 ms
   │       └─► returns uplift estimates per arm
   │
   ├─► bandit::select(uplift_estimates, costs, regulatory_constraints)  ~< 1 ms
   │       └─► returns chosen action + propensity
   │
   ├─► audit_log_queue.send_nowait(decision)          ~< 1 ms
   │       └─► async Kafka producer; doesn't block response
   │
   └─► HTTP 200 with action + decision_id              ~2 ms
                                              ────────────
                                              Total p99 < 50 ms (target)
```

## How this maps to ADRs

| Plane | Key ADR(s) |
|-------|------------|
| Streaming | ADR 001 (Quixstreams), ADR 002 (RisingWave-as-FS) |
| Decision | ADR 004 (monolithic decisioner) |
| Batch | ADR 003 (Metaflow on K8s) |
| Model registry | ADR 005 (MLflow artifact proxy) |
| Manifest deployment | ADR 006 (Kustomize base+overlays) |

## Status

The four-plane structure is stable through Day 0. The ASCII diagram
above lives in `docs/architecture_diagrams.md` as the canonical version;
this chapter cross-references it for narrative reading.
