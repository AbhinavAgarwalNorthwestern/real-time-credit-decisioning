# Tour — 10-Minute Interview Demo Script

Memorize this. It is the demo that wins interviews. The system runs
locally on a kind cluster; the demo flow takes ~10 minutes from
cold-start to closing on the design doc.

Each scene has: target time, what the interviewer sees, what you say,
and a fallback for if something fails live.

## Pre-demo checklist

Before starting screen-share:

- [ ] kind cluster is up: `kubectl get nodes` shows Ready
- [ ] Kafka topics created and idle (no consumer lag dashboards red)
- [ ] RisingWave reachable: `psql ${RW_DSN} -c "SELECT 1"` succeeds
- [ ] MLflow UI reachable: `just mlflow-ui` and browser tab loaded
- [ ] Grafana up with the four panels visible
- [ ] One terminal at the repo root, one at `services/decisioner/`,
      one at `services/transactions/`
- [ ] Browser tabs: MLflow, Grafana, repo on GitHub, design doc

## Scene 1 — Idle state (0:00 → 1:00)

**What's on screen**: Grafana with zero events, latency baseline,
drift PSI at 0.

**You say**: "This is the system at idle. Three planes — streaming,
synchronous decision, batch retraining. Each independently deployable.
Right now nothing's flowing."

**Fallback**: If Grafana fails to load, switch to terminal showing
`kubectl get pods` across all namespaces.

## Scene 2 — Turn on the stream (1:00 → 2:00)

**What's on screen**: Grafana lights up — events/sec rising, decision
distribution shows roughly the prior split across CLI / fraud / no-action.
p99 latency stays under 50 ms.

**You say**: "Now I'm starting the synthetic transaction generator —
this is the `transactions` service, the analog of a card-issuer's event
bus. Watch the decision dashboard. The decisioner is doing five things
per event in-process: feature lookup from RisingWave, segment route,
neural uplift inference via ONNX, contextual bandit selection, audit
log enqueue. All in one Rust process to hit p99 under 50 ms."

**Fallback**: If generator fails, show pre-recorded screenshot of the
populated dashboard.

## Scene 3 — Load test (2:00 → 3:30)

**What's on screen**: Terminal runs `just load-test-local`. k6 ramps
to 5000 RPS; Grafana shows latency staying flat.

**You say**: "I'm now hitting `/decide` with 5000 requests per second
sustained from k6. Watch p99 — stays well under 50 ms even under load.
This is the latency-budget table in ADR 004 made real. Every Kafka hop
the request path doesn't take saves 5–15 ms; that's how we have
headroom for the tail."

**Fallback**: If k6 fails, show the Day 7 measurements from
`docs/04_results_and_metrics.md` and walk through the budget table.

## Scene 4 — Decision audit log (3:30 → 5:00)

**What's on screen**: Open the decisions table in RisingWave.

```sql
SELECT decision_id, customer_id, champion_action, challenger_action,
       champion_propensity, shap_delta_baseline
FROM decisions
ORDER BY decision_ts_ms DESC
LIMIT 10;
```

**You say**: "Each row is one decision. We log the champion's action,
the challenger's would-be action, the propensity, and a SHAP delta
against the no-action baseline. That gives us off-policy evaluation
ability, model lineage, and the regulatory artifacts for adverse-action
notification under ECOA/Reg B. This is the audit and explainability
layer that turns an ML system into a production-ML system."

**Fallback**: Open the schema documentation in
`docs/02_data_and_features.md`.

## Scene 5 — Inject concept drift (5:00 → 6:00)

**What's on screen**: Run `just demo-drift`. The generator flips to a
shifted distribution. Grafana shows PSI on the affected feature climbing.
A drift event appears in Kafka.

**You say**: "Now I'm injecting drift — synthetic generator switches to
a different velocity distribution, modeling a sudden behavioral shift.
The drift monitor catches it within seconds. Watch the PSI chart and the
drift_events topic."

**Fallback**: Show the drift detector source in
`services/drift_monitor/` and explain the PSI / KS / ADWIN logic.

## Scene 6 — Retraining kicks off (6:00 → 7:30)

**What's on screen**: Argo Events catches the drift_event; the Metaflow
flow starts; per-segment fan-out pods spin up.

**You say**: "Drift event triggers retraining via Argo Events. The
Metaflow flow fans out — one pod per segment, training in parallel.
This is the CLI propensity factory pattern made real. The fan-out is
what gives the 4x throughput gain over sequential training."

**Fallback**: Show the previous successful flow run in the Metaflow UI.

## Scene 7 — New models in MLflow (7:30 → 8:30)

**What's on screen**: Open MLflow UI, show new model versions with
champion + challenger aliases.

**You say**: "New per-segment models land in MLflow as challenger
versions. The champion alias still points at the previous version —
so the running decisioner is still serving the champion. Challenger is
shadow-scoring."

**Fallback**: Walk through the alias scheme + the registry naming
convention in `docs/03_models_and_choices.md`.

## Scene 8 — Off-policy evaluation (8:30 → 9:30)

**What's on screen**: Open the OPE notebook (or terminal). Run the IPS
estimator over the last N hours of logged decisions.

**You say**: "Off-policy evaluation: we use IPS, self-normalized IPS,
and doubly-robust estimators on the decision log to estimate what the
challenger would have done if it had served the same traffic. The
propensities we logged in the audit table make this possible. If IPS
shows the challenger would have done better with a confidence-bounded
margin, the canary starts. Otherwise the challenger is archived."

**Fallback**: Show the estimator math in `docs/06_production_patterns.md`
Pattern 6.

## Scene 9 — Canary promotion + auto-rollback (9:30 → 10:00)

**What's on screen**: Trigger the canary; show traffic gradually shifting
5% → 25% → 100% in Grafana. Then deliberately inject a bad challenger
metric; show the auto-rollback kicking in.

**You say**: "Canary: 5% to 25% to 100% over a few minutes with
auto-rollback. I'll inject a regression deliberately — the system
detects it and reverts to the previous champion within seconds. That's
the 4-eyes-promotion gate plus the auto-rollback safety net."

**Fallback**: Walk through `docs/runbooks/rollback.md`.

## Closing — Design doc (10:00)

**What's on screen**: Open `docs/decisions/` in browser.

**You say**: "Every architectural call here has an ADR. Why monolithic
decisioner instead of microservices? ADR 004 has the latency budget.
Why RisingWave instead of Feast? ADR 002 explains the MV-over-CDC
equivalence. Why Kustomize over Helm? ADR 006. The reasoning is in the
repo. Happy to dig into any of them."

## What goes wrong + recovery

- **kind cluster crashes mid-demo**: keep a recorded video on a side
  monitor as backup
- **Decision API can't connect to RisingWave**: cluster restart between
  scenes is fine; we lose <30s
- **k6 results show p99 > 50ms**: don't panic — own it; reference
  ADR 004 budget; note "this is local kind on a laptop, prod numbers
  on EKS in `docs/04_results_and_metrics.md` are…"
- **MLflow UI 500s**: reference `docs/decisions/005-mlflow-artifact-proxy-not-direct-s3.md`
  and the rotation runbook; pivot to terminal `mlflow runs` CLI

## Status

Script is stable through Day 0. Implementation populates each scene as
Days 1–7 complete. Rehearse end-to-end Day 8 before the first
interview.
