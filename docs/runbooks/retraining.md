# Runbook — Retraining

## When to run this

A retraining run is triggered automatically by:

- **A `drift_event` from `services/drift_monitor`** landing on the
  `drift_events` Kafka topic. Argo Events (K8s overlay) or EventBridge
  (AWS overlay) picks up the event and invokes the flow.
- **A scheduled cron** — daily nightly run by default (`@schedule`
  decorator in `services/retraining_flow/`).
- **Manual invocation** — see "How to run manually" below.

You should also run this manually after:

- A new feature is added to the `behavioral_features` service
- A new segment is added to the segment router
- An ADR change that affects the modeling layer (ADR 008+)

## How to run manually

```bash
# Local Metaflow run — Kubernetes execution (recommended)
just flow-retrain-k8s

# Local Metaflow run — no Kubernetes (laptop only, low-fidelity)
just flow-retrain-local

# AWS Batch execution (AWS overlay only)
just flow-retrain-aws-batch
```

Watch the run:

```bash
# Metaflow card server shows per-step HTML cards
uv run python -m retraining_flow.main card server
```

## What the flow does

1. Loads the training-data window from RisingWave (point-in-time-correct
   join via the MV semantics)
2. `foreach` over segments — one Kubernetes pod per segment
3. Each pod:
   - Trains a per-arm T-learner with Optuna HPO
   - Logs all params + metrics to MLflow
   - Exports the model to ONNX
   - Validates the ONNX prediction matches the PyTorch prediction
     within a numerical tolerance (validation step)
   - Registers the model in MLflow with a challenger alias
4. Promotion gate (separate Metaflow flow `shadow_promotion_flow`):
   - Challenger must beat champion on offline metric
   - Challenger must not regress on any segment
   - Challenger prediction distribution must be calibrated
5. If all gates pass → challenger moves to shadow mode (1 hour)
6. Off-policy evaluation flow (`eval_flow`) runs IPS/SNIPS/DR
7. If OPE shows positive expected lift → canary 5%→25%→100%
8. 4-eyes alias swap in MLflow (two service-account approvals required)

## How long it takes

| Stage | Wall-clock target |
|-------|-------------------|
| Fan-out training (all segments parallel) | < 30 min |
| Validation gate | < 2 min |
| Shadow scoring window | 1 hour |
| Off-policy evaluation | < 5 min |
| Canary ramp | ~30 min total (5% for 10, 25% for 10, 100% for 10) |
| Total drift-to-100%-promotion | ~2.5 hours |

If the wall-clock is much higher than this, check:

- Pod resource limits (each segment training pod expects 4 CPU / 8 GiB)
- RisingWave query latency (training-data window read is the largest
  early-step cost)
- MLflow artifact upload speed (large ONNX models can saturate the
  artifact proxy)

## When promotion fails

- **Offline gate fails**: challenger is archived; no further action.
  Investigate the per-segment metrics in the MLflow run; common cause
  is a feature whose distribution shifted enough to make the existing
  hyperparameters poor — re-run with a wider HPO search.
- **Shadow scoring fails**: same — challenger archived; look at the
  shadow-prediction distribution vs champion in the Metaflow card.
- **OPE fails**: challenger archived. IPS variance is high when the
  bandit's exploration was thin; consider raising the bandit exploration
  rate temporarily and rerunning OPE.
- **Canary fails**: see `docs/runbooks/rollback.md`. Auto-rollback
  should kick in within seconds; verify the alert fired and the alias
  swap reverted.

## Where to look when something is wrong

| Symptom | Look here |
|---------|-----------|
| Pod stuck in Pending | `kubectl describe pod <name>` — usually a resource-limit or PVC issue |
| Training step OOMing | Adjust `@resources(memory=...)` in the FlowSpec |
| ONNX export differs from PyTorch | The validation step catches this; usually a non-deterministic op |
| MLflow registry rejects new version | `kubectl logs deployment/mlflow-tracking -n mlflow` — check for backend DB errors |
| Drift event fired but no flow started | Argo Events Sensor logs: `kubectl logs -n argo-events sensor/drift-sensor` |

## Status

Stable through Day 0. Updated as Day 5's implementation lands.
