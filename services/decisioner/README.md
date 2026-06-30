# decisioner

The single Python+FastAPI service on the synchronous decision plane.
Implements the collapsed request-path decomposition per
[ADR 008](../../docs/decisions/008-python-fastapi-decisioner-supersedes-rust.md)
(which supersedes [ADR 004](../../docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md) —
the architectural decision survives, only the language pivoted).

Per request, all in one process:

1. RisingWave feature lookup via `asyncpg` connection pool
2. Rule-based segment routing
3. Per-segment uplift inference via `onnxruntime` (GIL released during the C++ call)
4. Contextual bandit action selection
5. Audit log enqueued to Kafka via `aiokafka` (async, non-blocking)
6. HTTP response

## SLO target

- **p99 < 50 ms** end-to-end on `POST /decide`
- Sustained throughput per replica achieved via uvicorn worker count
  (default 4) plus onnxruntime intra-op parallelism within each request

## Why Python (not Rust)?

Per ADR 008. Short version: same SLO, less margin, ships in a fraction of
the time given the 7-day production-grade build constraint, stays in the
Python stack used end-to-end across the rest of the platform. Rust is
documented as the future production-hardening optimization path —
`docs/jvm_equivalents.md` will include the Rust equivalent alongside the
Java/Spring Boot enterprise mapping.

## Status

**Skeleton (Day 0 pivot).** Implementation plan:

- **Day 1**: `asyncpg` connection pool + RisingWave feature-vector query
- **Day 2**: `ModelRegistry` loading per-segment ONNX from MLflow, with
  hot-reload on alias change; `GET /metrics` Prometheus exporter
- **Day 3**: `POST /decide` handler + segment router + contextual bandit
  + per-request SHAP delta against the no-action baseline
- **Day 4**: champion-challenger shadow scoring + canary traffic split
  + audit log producer (aiokafka)

## Day 0 acceptance criteria

- Imports clean (`uv run python -c "from decisioner.main import app"`)
- `/health` returns 200 OK
- `pyproject.toml` lists every dependency the request-path implementation
  will need (FastAPI, uvicorn, asyncpg, aiokafka, onnxruntime, numpy,
  prometheus-client, mlflow, loguru, pydantic, pydantic-settings)

## Running locally

```bash
# Inside the devcontainer
uv sync
uv run python -m decisioner.main      # binds to $DECISIONER_HOST:$DECISIONER_PORT (default 0.0.0.0:8080)
# Verify
curl -s http://localhost:8080/health  # {"status":"ok"}
```

Or via uvicorn directly for hot-reload during development:

```bash
uv run uvicorn decisioner.main:app --reload --host 0.0.0.0 --port 8080
```

## Configuration

All settings env-driven via `DECISIONER_*` prefix. See `src/decisioner/config.py`
for the full list. Common overrides:

| Env var | Default | Notes |
|---------|---------|-------|
| `DECISIONER_PORT` | `8080` | HTTP listen port |
| `DECISIONER_WORKERS` | `4` | uvicorn worker count per pod |
| `DECISIONER_RW_HOST` | `risingwave-frontend.risingwave.svc.cluster.local` | RisingWave Postgres-protocol endpoint |
| `DECISIONER_MLFLOW_TRACKING_URI` | `http://mlflow-tracking.mlflow.svc.cluster.local` | Model registry |
| `DECISIONER_KAFKA_BROKER_ADDRESS` | `kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092` | Audit log producer |
| `DECISIONER_DECISION_TIMEOUT_MS` | `100` | Hard per-request deadline |
