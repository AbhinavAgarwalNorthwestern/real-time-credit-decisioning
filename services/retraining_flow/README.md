# retraining_flow

Metaflow fan-out retraining flow for the finance domain. Lives on the
**batch plane** per ADR 004 — outside the request path, communicates
with other planes only via Kafka events and MLflow artifacts.

## What it does

Triggered by:
- `drift_detected` event from `drift_monitor` (via Argo Events / EventBridge)
- Scheduled cron (`@schedule` decorator)
- Manual CLI invocation

Fan-out steps:
1. Load training-data window from RisingWave (point-in-time-correct)
2. `foreach` segment → train a per-segment neural T-learner (uplift model)
3. Each segment training step logs to MLflow with consistent run naming
4. Validation gate: new model must beat champion offline metric AND
   not regress on any segment AND produce a calibrated distribution
5. Export winning models to ONNX (consumed by Rust `decisioner`)
6. Promote winning models to challenger alias in MLflow registry

Default execution: Kubernetes (`@kubernetes` decorator) — works on kind
locally and EKS in cloud (per ADR 003). AWS Batch path (`@batch`)
available as the AWS overlay option, defending Synchrony-pattern resume
bullets without forcing AWS lock-in.

## Status

**Skeleton (Day 0).** Implementation lands on Day 5.

## Day 5 acceptance criteria

- `RetrainingFlow(FlowSpec)` with `@kubernetes` step decorators
- `foreach` over segments returns per-segment ONNX model + metrics
- MLflow run naming convention: `retrain_{trigger}_{date}_{segment}`
- ONNX export + validation step (Python prediction vs ONNX-runtime
  prediction within numerical tolerance) per ADR 004 mitigation
- Promotion gate documented as `docs/runbooks/retraining.md`
