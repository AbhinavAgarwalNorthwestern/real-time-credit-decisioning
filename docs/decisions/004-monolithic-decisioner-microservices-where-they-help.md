# ADR 004: Monolithic decisioner on the request path; microservices on the streaming and batch planes

**Status:** Accepted
**Date:** 2026-06-03
**Decision makers:** Platform owner

## Context

The codebase we inherited (the crypto pipeline) follows a strict
microservice-per-topic pattern: `trades → candles → technical_indicators →
predictor → prediction-api` — each transformation is a small standalone
Quixstreams app or thin Python service, communicating via Kafka topics.

That pattern is the right shape for the crypto domain because the SLO is
"predictions land in RisingWave within seconds of new candles."

The credit-decisioning domain has a fundamentally different SLO on the
request path:

- **p99 < 50ms** end-to-end on `POST /decide`
- 5000 decisions/sec sustained

We need to decide whether the request-path decomposition (e.g. one service
each for segment routing, per-segment uplift inference, contextual bandit
selection, audit logging, HTTP serving) should follow the same
microservice pattern, or collapse into a single process.

### Latency budget

A back-of-envelope decomposition of the request path:

| Step | Microservice path (Kafka hops) | Single-process path (function calls) |
|------|--------------------------------|--------------------------------------|
| Feature lookup from RisingWave | ~5 ms | ~5 ms |
| Segment routing | 5–15 ms (one hop) | <1 ms |
| Uplift inference per segment | 5–15 ms (one hop) | 5–10 ms (ONNX in-process) |
| Bandit selection | 5–15 ms (one hop) | <1 ms |
| Audit log (async) | 5 ms (one hop, async) | <1 ms (queued, async) |
| HTTP response | 2 ms | 2 ms |
| **Total p50** | **~30–50 ms** | **~13–18 ms** |
| **Total p99** | **70+ ms (SLO miss)** | **~25–35 ms (SLO hit with room)** |

The microservice decomposition burns the entire SLO on serialization and
broker round-trips before any inference happens. The single-process
decomposition leaves ~15ms of headroom for tail latency on the inference
step (cold-cache RisingWave lookups, GC pauses, etc.).

## Decision

We split the system into **three planes with different decomposition
philosophies**, not one uniform philosophy:

1. **Streaming / async plane** — keep the microservice-per-topic pattern.
   Each service is a Quixstreams transformer or thin producer/consumer.
   Services on this plane: `transactions` (synthetic stream producer),
   `behavioral_features` (stream → RisingWave MV), `outcome_collector`
   (joins outcomes back to decisions), `drift_monitor` (consumes prediction
   stream, emits drift events).

2. **Synchronous decision plane (request path)** — **collapse into a single
   Rust process** named `decisioner`. It is the only service in the request
   path. It does in-process:
   - One Postgres-protocol query to RisingWave for the feature vector
   - Rule-based segment routing
   - Per-segment neural uplift inference via the `ort` (ONNX Runtime) crate,
     using PyTorch models exported to ONNX during training
   - Contextual bandit selection over the uplift estimates
   - Audit log entry queued for async write
   - HTTP response

3. **Batch plane** — orchestrated by Metaflow (see ADR 003). Lives outside
   the request path. Flows: `retraining_flow`, `eval_flow` (off-policy
   evaluation), `backtest_flow`.

The three planes communicate only via Kafka topics (events) and MLflow
artifacts (model versions). They never reach into each other's address
space.

## Consequences

### Positive

- **Hits the p99 < 50ms SLO with room to spare** (estimated headroom 15ms
  for tail latency)
- **One pod, one log stream, one trace** for the request path — debugging
  a decision means looking at one process
- **No serialization tax** on the hot path
- **Lower resource cost** — one process instead of four
- **Senior-architect signal**: we are explicit about *where* microservices
  help and *where* they don't, and we have the numbers to justify it
- **In-process model loading** is fine because tabular NN uplift models
  are small (typically MB-scale per segment); a single pod can hold all
  segment models in memory comfortably

### Negative

- **Cannot scale segment-routing independently from uplift inference.**
  Mitigated: these are coupled by request flow anyway, so scaling them
  separately wouldn't help. They scale together via decisioner replicas.
- **PyTorch → ONNX export step couples training and serving languages.**
  Mitigated by a CI step that validates the ONNX export produces the
  same outputs as the PyTorch model within a numerical tolerance, on
  every training run.
- **The decisioner pod holds the entire segment-model set.** RAM cost is
  small for our model sizes; would need revisiting if model sizes
  grew to 10+ GB.
- **Less "many microservices" wow factor for less-experienced reviewers.**
  Mitigated by this ADR, which makes the reasoning explicit, and by the
  fact that we *do* run the streaming plane as microservices — so the
  pattern is in the codebase, just applied where it earns its operational
  cost.
- **A bug in the decisioner takes down the whole request path.** Mitigated
  by Kubernetes liveness/readiness probes, multiple decisioner replicas
  behind the load balancer, and a champion-challenger pattern where the
  champion is always loaded from MLflow (so a single bad challenger model
  cannot break serving).

## Alternatives considered

- **Full microservice decomposition (Pau's pattern applied uniformly)**:
  rejected on the latency budget above. Would force a 70+ms p99, missing
  the SLO.
- **Single all-in-one service handling streaming + serving + drift**:
  rejected. Couples deployment cadences (you'd redeploy the request path
  every time you tweak a feature definition). Violates the
  separation-of-concerns that Kafka topics enforce naturally.
- **Python sidecar for NN inference + Rust HTTP router**: rejected. The
  IPC hop (Unix socket or gRPC) adds 2–5ms and operational complexity
  without buying meaningful flexibility. ONNX Runtime in Rust is the
  cleaner answer.
- **Rust + Triton Inference Server as a model-serving sidecar**: Triton
  is a great fit at very high model-count scale, but its operational
  weight (pod, GPU allocation, model repository) is wrong for our
  segment count (~5–10 small tabular models). Re-evaluate at 50+ models.
- **Python FastAPI service for everything**: rejected. FastAPI is fine,
  but the GIL + per-request overhead would burn 5–10ms of our budget on
  things Rust doesn't pay for. The Rust choice is itself a senior-signal
  and earns its keep here.

## Related

- ADR 001: Quixstreams is the right choice on the streaming plane,
  consistent with this decomposition
- ADR 002: RisingWave-as-feature-store is the one synchronous external
  call the decisioner makes
- ADR 003: Metaflow is the right shape for the batch plane, also
  consistent with this decomposition
- `docs/05_architecture.md`: the three-plane diagram and the request-path
  call graph
- `docs/06_production_patterns.md`: the latency budget walkthrough as an
  interview-ready story
