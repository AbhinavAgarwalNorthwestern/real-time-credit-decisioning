# ADR 008: Python FastAPI decisioner — supersedes ADR 004 (Rust)

**Status:** Accepted (supersedes [ADR 004](004-monolithic-decisioner-microservices-where-they-help.md))
**Date:** 2026-06-05
**Decision makers:** Platform owner

## Context

[ADR 004](004-monolithic-decisioner-microservices-where-they-help.md) chose Rust
(axum + sqlx + ort) for the request-path decisioner on a latency-budget
argument. That reasoning still holds — collapsing the request path into a
single process beats microservice decomposition on the p99 budget. The
architectural decision (one in-process service for the synchronous decision
plane) is preserved.

What changed is the **implementation language**. Four pressures pushed away
from Rust during early build:

1. **Timeline**: 7 days for the full production-grade build. Rust adds
   learning-curve hours and a class of integration debugging that Python
   sidesteps:
   - `ort` (ONNX Runtime crate) needs `libonnxruntime` available on the
     target image — a real C++ dependency
   - `rdkafka` crate needs `librdkafka` — same problem at the system level
   - `sqlx` PG client needs careful pool tuning and per-query type checking
   - tokio async runtime tuning for fairness under load
2. **Toolchain friction**: mid-Day-1 `mise install` failed twice on the
   `rust@stable` plugin — rustup network blips, exit-code reporting issues.
   Time spent debugging the toolchain is time not spent on the system.
3. **Ecosystem reach**: SHAP, evidently, opik, statsmodels, ecpost — the
   off-the-shelf ML rigor libraries are Python. A Rust decisioner would
   either reimplement these or call out to a Python sidecar (defeating the
   single-process collapse).
4. **Stack consistency**: every other service (`transactions`,
   `behavioral_features`, `drift_monitor`, `retraining_flow`,
   `outcome_collector`) is Python. One stack across the codebase reduces
   review burden, debugging surface, and deployment complexity.

### Revised latency budget

| Step | Rust (ADR 004) | **Python FastAPI (this ADR)** |
|------|----------------|-------------------------------|
| HTTP/event loop overhead | ~1 ms | ~2–5 ms |
| Feature lookup (asyncpg vs sqlx, both PG protocol) | 5 ms | 5–8 ms |
| Segment routing | <1 ms | <1 ms |
| ONNX inference (per segment) | 5–10 ms | 8–15 ms (onnxruntime releases GIL) |
| Bandit selection | <1 ms | <1 ms |
| Audit log enqueue (aiokafka vs rdkafka) | <1 ms | <1 ms |
| HTTP response | 2 ms | 2–3 ms |
| **Total p99** | **25–35 ms** | **30–50 ms** |

Same SLO (p99 < 50 ms). Less margin (~15 ms less headroom). Achievable with
uvicorn worker count > CPU count + onnxruntime releasing the GIL during the
C++ inference call.

## Decision

Use **Python with FastAPI + uvicorn (uvloop)** for the request-path
decisioner. Dependency stack:

- `fastapi` + `uvicorn[standard]` — async HTTP server
- `asyncpg` — async Postgres-protocol client for RisingWave feature lookup
- `aiokafka` — async Kafka producer for the audit log
- `onnxruntime` — model inference (CPU; GPU not required at our model size)
- `pydantic-settings` — env-driven configuration
- `prometheus-client` — metrics exporter for SLO dashboards

Concurrency: 4 uvicorn workers per pod replica; `onnxruntime` configured
with `intra_op_num_threads=2` for inference parallelism within a single
request; async/await throughout the request path to avoid event-loop
blocking.

## Consequences

### Positive

- **1–2 days saved** across Days 2–3 (no Rust learning, no C++ system-dep
  wrangling, no ONNX export validation step — onnxruntime loads `.onnx`
  files directly without a Rust-side compatibility check)
- **Single Python stack across the codebase**: easier review, easier debug,
  one Docker base image, one CI pattern
- **Direct PyTorch → onnxruntime path** in the same process (no language
  boundary between training and serving)
- **Reduced integration risk**: each of `ort`, `rdkafka`, `sqlx`
  represented a half-day-to-day debugging risk in the Rust plan. All
  eliminated.
- **Better ML-rigor library access**: SHAP delta for the regulatory audit
  log (Day 6), evidently for drift backfill, ecpost for OPE — all native
- **Simpler deployment**: one Dockerfile pattern across all services
- **mise.toml shrinks**: `rust = "stable"` removed; the troublesome
  rustup install no longer blocks container bootstrap

### Negative

- **Lose the "Rust low-latency serving" interview talking point.**
  Mitigation: framed honestly as a deliberate timeline-vs-language tradeoff;
  Rust documented as the production-hardening optimization path in
  `docs/06_production_patterns.md`. The architectural decision (collapsed
  request path) is what matters; the language is replaceable.
- **Tighter latency margin** (~15 ms less headroom than Rust). Mitigation:
  uvicorn worker count tuning, onnxruntime GIL-release during inference,
  measured on Day 7 with k6.
- **GIL contention risk** at very high concurrency. Mitigation: workers > CPUs,
  inference releases GIL, async I/O for non-CPU work (DB + Kafka).
- **ADR 004 superseded**, requiring index + reference updates across
  `README.md`, `docs/05_architecture.md`, `docs/repo_layout.md`,
  `docs/06_production_patterns.md`.
- **`Cargo.toml` workspace removed** — no Rust services remain. The
  cargo-fmt pre-commit hook and the rust-check CI job are removed.
- **`docs/jvm_equivalents.md` (Day 1+) replaces the natural "show me Rust
  for the bank-grade variant" demo** — instead it becomes the Java/Spring
  Boot mapping doc (still useful for enterprise-stack interviews).

## Alternatives considered

- **Stay with Rust as planned (ADR 004)**: rejected on timeline +
  toolchain-friction grounds. Re-evaluate if the project extends beyond
  the 7-day build into long-running production with sustained latency
  pressure beyond what Python can deliver.
- **Go + Echo/Gin**: solves the GIL concern and the C++ linking risk.
  Rejected because (a) Python depth is the user's strength and (b) Go
  still needs a model-serving story (gorgonia, ONNX-Go, or Python sidecar
  via gRPC).
- **Java + Spring Boot**: the most common bank-production answer.
  Rejected on developer-velocity grounds for a 7-day timeline; documented
  as the production target in `docs/jvm_equivalents.md`.
- **Python FastAPI + Rust sidecar for inference only**: adds an IPC hop
  (Unix socket or gRPC) that costs ~2–5 ms and operational complexity,
  without buying back enough latency to justify the Rust dependency.
- **Triton Inference Server as the model-serving sidecar**: appropriate at
  very high model-count scale, but its operational weight (pod, GPU
  allocation, model repository) is wrong for our 6 small tabular models.
  Re-evaluate at 50+ models.

## Related

- **[ADR 004](004-monolithic-decisioner-microservices-where-they-help.md)** —
  superseded by this ADR. Body remains immutable per the ADR convention;
  status header is marked Superseded.
- [ADR 002](002-risingwave-as-feature-store-not-feast.md) — RisingWave is
  the synchronous lookup; `asyncpg` speaks the Postgres protocol just as
  `sqlx` would have.
- [ADR 005](005-mlflow-artifact-proxy-not-direct-s3.md) — MLflow stays
  unchanged; the Python decisioner loads ONNX artifacts through the same
  registry.
- [ADR 006](006-base-overlays-kustomize-not-helm.md) — base+overlays
  manifest layout unchanged; the decisioner Deployment switches its image
  from a Rust binary to a Python+uvicorn container.
- `docs/06_production_patterns.md` — interview-ready production-pattern
  walkthrough; updated to note Rust as the future hardening path.
