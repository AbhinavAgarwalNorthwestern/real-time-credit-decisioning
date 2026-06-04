# Runbook — On-Call

## Who's on call

Solo project. The author is on call 24/7 by default. When this is
deployed to a real team, the rotation goes here.

## What pages you

Alerts fire from three sources:

- **Grafana Alertmanager** — SLO burn-rate alerts on `/decide` p99
  latency, throughput, error rate; drift PSI exceedance
- **Metaflow flow failures** — retraining or eval flow failures (logged;
  paged if multiple consecutive)
- **K8s liveness/readiness probe failures** — pod restart loops

In the AWS overlay, alerts route through SNS → PagerDuty webhook. In
local-kind, alerts surface in Grafana and the terminal.

## First-response checklist (every alert)

1. Acknowledge the alert in Grafana / PagerDuty
2. Open the affected service's Grafana panel
3. Check the Kubernetes events for the namespace:
   ```bash
   kubectl get events -n real-time-ml --sort-by='.lastTimestamp' | tail -50
   ```
4. Check the service logs (recent):
   ```bash
   kubectl logs -n real-time-ml deployment/<service> --tail=200
   ```
5. If actionable, file an incident in `docs/incidents.md` once resolved

## Common alerts and first-pass diagnostics

### `/decide` p99 latency burn-rate

- **Symptom**: alert fires when p99 > 50 ms for sustained period
- **First check**: which step is slow? Open the decisioner trace
  dashboard (per-step latency)
  - **Feature lookup slow** → RisingWave pod health (`kubectl get pods -n risingwave`)
  - **Inference slow** → ONNX session not warmed; check decisioner
    cold-start metrics
  - **Audit log enqueue slow** → Kafka producer health; check broker
    metrics
- **Mitigation**: scale up decisioner replicas (`kubectl scale
  deployment/decisioner --replicas=N`)

### Drift event fires unexpectedly

- See `docs/runbooks/drift_response.md`

### Retraining flow failed

- See `docs/runbooks/retraining.md` — "When promotion fails" section

### Canary rollback fired

- See `docs/runbooks/rollback.md` — the auto-rollback should have
  completed; verify by checking the alias state

### `decisioner` is unreachable

- Check the pod's status and recent events:
  ```bash
  kubectl describe pod -n real-time-ml -l app=decisioner | tail -50
  ```
- If `CrashLoopBackOff`: read logs. Likely causes:
  - MLflow tracking server unreachable (decisioner refuses to start
    without an initial model load)
  - RisingWave unreachable (sqlx connection pool fails health check)
  - ONNX model file missing in the artifact store

### MLflow tracking server is unreachable

- Pod status:
  ```bash
  kubectl describe pod -n mlflow -l app=mlflow | tail -50
  ```
- Common causes:
  - Postgres backend (bundled RisingWave Postgres locally; RDS in AWS)
    is unhealthy
  - MinIO unreachable (local-kind only)
  - The MinIO credentials Secret was rotated but the MLflow pod wasn't
    restarted — see `docs/day0_log.md` Action 9

### Kafka consumer lag

- Check the topic's consumer lag in Grafana
- Restart the stuck consumer:
  ```bash
  kubectl rollout restart deployment/<service> -n real-time-ml
  ```
- If lag is persistent, the consumer may be unable to keep up — scale
  it horizontally

## What to NOT do during an alert

- **Don't rollback a model on instinct** — see `docs/runbooks/rollback.md`
- **Don't delete a stuck Metaflow flow** — `kill` the run via the
  Metaflow CLI; deleting via `kubectl delete` leaves orphaned MLflow
  runs
- **Don't bypass the 4-eyes promotion gate** even during an incident —
  if rolling back is urgent, both service-account approvals are still
  required; the gate prevents the worst-case mistakes

## Status

Skeleton through Day 0. Updated as each day's services come online
and the SLO alerts fire for real measurements.
