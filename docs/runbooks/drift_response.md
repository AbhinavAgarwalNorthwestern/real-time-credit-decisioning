# Runbook — Drift Response

## What "drift" means in this system

Three signals, three detectors. Any one firing emits a `drift_event`:

| Signal | Detector | Threshold default |
|--------|----------|-------------------|
| Input feature distribution | **PSI** (Population Stability Index) on each feature | PSI > 0.25 sustained over 30 min |
| Prediction distribution | **KS** (Kolmogorov-Smirnov) on the action-mix output | p < 0.01 in a rolling 1-hour window |
| Online detection on continuous signals | **ADWIN** (`river` library) | Significance level α = 0.001 |

All three thresholds are configurable; see `services/drift_monitor/`.

## What happens automatically

1. A detector fires → emits `drift_event` to the `drift_events` Kafka
   topic
2. Argo Events Sensor (K8s overlay) or EventBridge rule (AWS overlay)
   listens for the event
3. The matching Sensor / rule triggers `retraining_flow`
   (see `docs/runbooks/retraining.md`)
4. The drift event is also recorded in the `drift_history` table in
   RisingWave for audit

You do not need to do anything for the automated path. This runbook is
for the cases where:

- A drift event fires that you didn't expect
- A drift event fires and the automated retraining doesn't
- A drift event fires for a feature that has known seasonal swings
  (false positive)

## Diagnosing an unexpected drift event

### Step 1 — Identify which detector fired and on which feature

```sql
-- in RisingWave
SELECT detector, feature, severity, evidence, fired_at
FROM drift_history
ORDER BY fired_at DESC
LIMIT 20;
```

The `evidence` column contains the detector's diagnostic payload — for
PSI, it's the per-bin contribution map; for KS, it's the p-value and
test-statistic distribution; for ADWIN, it's the change-point location.

### Step 2 — Validate the signal

Use the Grafana drift dashboard panel for the affected feature. Look
for:

- **A sudden step change** → likely an upstream system event (deploy,
  config change, data-source switch). Cross-reference with the deploy
  log
- **A gradual ramp** → likely a real distribution shift; let retraining
  proceed
- **A spike returning to baseline within minutes** → likely a transient
  upstream blip; consider raising the detector's sustain threshold

### Step 3 — Decide whether to retrain

| Cause | Action |
|-------|--------|
| Upstream config change you control | Roll back the config; the drift signal should resolve in minutes |
| Real distribution shift | Let `retraining_flow` proceed. Monitor the run; see `docs/runbooks/retraining.md` |
| Known seasonal swing | Adjust the detector's reference window for this feature; document in an ADR if it's a permanent change |
| False positive from a noisy feature | Raise the sustain threshold; document the change in `docs/incidents.md` |
| Sensor mis-fired (Argo Events / EventBridge bug) | Restart the Sensor pod; file an incident |

## When automated retraining doesn't fire

Symptom: drift event in Kafka but no retraining flow starts.

Check, in order:

```bash
# 1. Argo Events Sensor running?
kubectl get sensor -n argo-events

# 2. Sensor logs
kubectl logs -n argo-events sensor/drift-sensor

# 3. EventSource subscribed to the right topic?
kubectl describe eventsource kafka-drift -n argo-events

# 4. Metaflow can be reached?
uv run python -m retraining_flow.main run --help
```

Common causes:
- Sensor restarted with a stale subscription offset; bumping the
  subscription start point fixes it
- The Sensor's trigger spec references a service account that no
  longer has the permission to start jobs; check RBAC
- EventSource is mis-configured for the cluster's actual Kafka
  endpoint (e.g., after a kind cluster rebuild)

## When to suppress an active drift detector

There are valid cases for temporarily silencing a detector:

- **A planned upstream change** that will cause a known one-shot drift
  (e.g., a generator parameter change for demo purposes)
- **A known false-positive feature** during an investigation

```bash
# Set the suppression flag via env on the drift_monitor service
kubectl set env deployment/drift_monitor \
  -n real-time-ml \
  DRIFT_SUPPRESS_FEATURES="velocity_5m,velocity_1h"
```

Then **always re-enable** within the documented suppression window
(default: 24 hours; auto-re-enable). Log the suppression event in
`docs/incidents.md`.

## Status

Stable through Day 0. Updated as Day 5's drift_monitor implementation
lands. Detector thresholds revisit after the first month of operation
with real drift signal.
