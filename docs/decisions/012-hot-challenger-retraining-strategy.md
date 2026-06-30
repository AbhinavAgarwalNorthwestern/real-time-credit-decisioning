# ADR 012: Hot-challenger retraining + comprehensive drift detection

**Status:** Accepted
**Date:** 2026-06-07
**Decision makers:** Platform owner

## Context

Day-5 originally planned a **reactive** retraining strategy: drift fires
→ `RetrainingFlow` runs → new model becomes challenger → shadow scoring
→ canary → promotion to champion. Time-to-challenger = end-to-end
retraining latency (~10–30 minutes on this synthetic-data scale, more in
production with 30-day backfills + Optuna HPO).

During that window the champion keeps making decisions on a feature
distribution it was never trained for. For credit decisioning under
ECOA + SR 11-7 the regulatory ask is fast model response to drift; the
"30-minute hole" is the production failure mode.

Two design questions surfaced together:

1. **Are we detecting all the kinds of drift that matter?** PSI/KS/ADWIN
   on input features catches *some* drift but misses prediction-side
   drift (model output distribution shifts before realized performance
   regresses), performance drift (OPE-vs-realized gap), schema/data-
   quality drift (missingness spikes), and per-segment drift (one
   subgroup changes while the aggregate looks stable).

2. **Can the time-to-challenger gap be eliminated?** Industry pattern:
   pre-emptively maintain a "hot" challenger that's continuously
   retrained on a short cadence, so when drift fires the alias swap is
   instant.

## Decision

### Decision 1 — Comprehensive drift detection

The drift_monitor runs the following detectors continuously (per
`services/drift_monitor/src/drift_monitor/detectors.py`):

| Detector | Catches | Configured threshold |
|---|---|---|
| `PSIDetector` | Per-feature covariate drift | PSI > 0.2 |
| `KSDetector` | Continuous-feature distribution shape shift | KS p-value < 0.05 |
| `ADWINDetector` | Concept drift in the decision stream over time | Hoeffding bound δ = 0.002 |
| `JSDivergenceDetector` | **Prediction drift** — model output distribution shifts (earlier signal than performance drift) | JS divergence > 0.1 |
| `PerformanceDriftDetector` | **Performance drift** — OPE-estimated profit vs realized outcomes | Relative gap > 15% |
| `SchemaDriftDetector` | **Data-quality drift** — missingness rate spikes (preempts model failures) | Δmissingness > 5pp |
| `PerSegmentDriftMonitor` | **Per-segment drift** — subgroup changes that aggregate away at cohort level | Stratified PSI/KS per `segment_id` |

Any detector firing emits a `drift-events` Kafka message; the
orchestrator (Argo Events) consumes it and triggers the retraining
response below.

### Decision 2 — Hot-challenger pattern for retraining

Implemented in `services/retraining_flow/src/retraining_flow/hot_challenger.py`.

`RetrainingFlow` runs on a **short recurring cadence** (default 2h,
configurable). Each run produces a new MLflow model version registered
with alias `latest_candidate`. No live traffic touches it.

Lifecycle:

```
created → latest_candidate → challenger → champion → archived
              ↑                  ↑           ↑
          (every 2h)         (drift fired   (4-eyes
                              or scheduled    + canary
                              promotion)     success)
```

When the drift_monitor fires:

1. Orchestrator calls `hot_challenger.on_drift_event()`.
2. If `latest_candidate` exists and is fresher than
   `max_candidate_age_minutes` (default 6h), promote it to
   `challenger` (instant alias swap).
3. Day-4's canary fraction starts at 5% via a tag on the model
   version; the rollback watchdog (Day-4) monitors performance and
   either ramps fraction up or rolls back.
4. If no fresh candidate exists (e.g., devcontainer restart), fall
   back to synchronous retraining and accept the latency hit. This
   path is the exception, not the rule.

Cost: ~12 retraining runs/day at ~$0.30 each (Metaflow @kubernetes,
small Optuna budget). <$5/day to eliminate the drift-response gap.
Production sizing tunes cadence against the model's expected drift
rate and SLA.

## Consequences

### Positive

- **Drift-to-challenger latency goes from minutes to seconds.** Alias
  swap is an MLflow metadata operation, not a training run.
- **Continuous validation of the training pipeline.** Every 2h the
  pipeline runs end-to-end; if something breaks (DGP gate fails, ONNX
  export diverges, MLflow becomes unreachable), it's caught hours
  before drift fires, in the absence of a real incident.
- **More-complete drift coverage.** Prediction drift, performance
  drift, schema drift, and per-segment drift now have dedicated
  detectors. Earlier detection of subtle drift forms.
- **Cleaner audit trail.** Each candidate's MLflow version captures the
  training-time snapshot (data SHA256, seed, hyperparams). Reviewers
  can answer "what would we have deployed at time T?" from history.
- **Cost-effective.** The cadence + Optuna trial count are configurable
  and explicit; production teams tune the trade-off rather than living
  with whatever a one-shot training defines.

### Negative

- **More MLflow registry versions per day.** Daily cadence × N segments
  × 12 runs ≈ 70+ versions/day. The registry needs a retention policy
  (archive after 30 days). Day-7 production-hardening adds this.
- **Compute baseline goes up.** Even when no drift fires, we're using
  cluster resources every 2h. In dev this is invisible; in production
  we right-size against the SLA.
- **Cohort drift between training runs.** The candidate trained at T-2h
  uses backfilled data from [T-2h-window, T-2h]. If drift hits between
  T-2h and T, the "fresh" challenger may not actually reflect the
  drift cause. **Mitigation**: the drift_monitor includes the drift
  payload in the trigger event; downstream evaluation (Day-6 OPE)
  weights the OPE estimate by whether the challenger's training data
  covered the drift window.
- **Promotion gate must be tight.** With more candidates moving more
  often, the 4-eyes + canary gate (Day-4) becomes the real safety net
  against bad challengers. This ADR does not change the gate; it
  raises the gate's importance.

### Open trade-off (not decided here)

The 2h cadence is a default, not a derived value. Production
deployment should tune it from the observed drift-fire rate over the
first 90 days. If drift fires once a quarter, 2h is overkill. If
drift fires weekly, 2h is right. Day-7 records the chosen cadence in
`docs/REGULATORY_COMPLIANCE.md` so SR 11-7 reviewers see the
trade-off explicit.

## Alternatives considered

- **Reactive retraining only** (original Day-5 plan). Rejected: the
  drift-to-deploy gap is the production failure mode.
- **Continuous online learning** (incremental updates per decision).
  Rejected: too aggressive for credit decisioning; small adverse
  events can cascade if the model updates from biased recent decisions
  before they're validated. Hot-challenger keeps the offline
  validation gate; online learning would have to invent a different
  one.
- **Schedule-only retraining** (every 2h, no drift signal). Rejected:
  the drift signal is independently valuable (root-cause + audit) even
  if we always have a fresh candidate. The hybrid (scheduled
  candidate + drift trigger) is strictly more capable.

## Related

- ADR 008 — Python FastAPI decisioner (the alias-swap target)
- `services/drift_monitor/src/drift_monitor/detectors.py` — the seven
  detectors
- `services/retraining_flow/src/retraining_flow/hot_challenger.py` —
  the promotion logic
- `services/decisioner/src/decisioner/champion_challenger.py` — the
  4-eyes + canary gate the hot challenger feeds into
- Day-6 OPE harness — evaluates the challenger before / during canary
- `docs/REGULATORY_COMPLIANCE.md` (Day-7) — records the cadence
  decision for SR 11-7
