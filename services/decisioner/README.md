# decisioner

The single Rust service on the synchronous decision plane. Implements the
collapsed request-path decomposition per ADR 004.

Per-request work, all in one process:

1. One Postgres-protocol query to RisingWave for the feature vector
2. Rule-based segment routing
3. Per-segment neural uplift inference via ONNX Runtime (`ort` crate)
   on PyTorch models exported by `retraining_flow`
4. Contextual bandit selection over uplift estimates
5. Audit log entry queued for async write to Kafka
6. HTTP response

## SLO target

- **p99 < 50 ms** end-to-end on `POST /decide`
- 5000 decisions/sec sustained throughput per replica

## Why Rust?

Per ADR 004 — latency budget. FastAPI/Python's GIL + per-request overhead
would burn 5–10 ms; Rust + axum + sqlx + ort hits the budget with headroom
for tail latency.

## Status

**Skeleton (Day 0).** Implementation:

- Day 2: segment router + ONNX inference path
- Day 3: contextual bandit + `/decide` handler
- Day 4: champion-challenger shadow + canary + audit log queue

## Day 0 acceptance criteria

- Compiles
- `/health` returns `ok` on port 3000
- Cargo.toml lists every dependency the request-path implementation
  will need (no surprise rebuilds later)

## Why a separate crate (not in the root workspace yet)

Standalone for now so Day 0 doesn't require touching the root Cargo.toml.
Will be wired into the root Cargo workspace alongside `prediction-api`
when Day 2 begins.
