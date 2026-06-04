# Runbook — Model Rollback

## When to use this

Auto-rollback should handle the common case automatically (see "How
auto-rollback works" below). This runbook is for the cases where:

- Auto-rollback failed to trigger or failed to complete
- A challenger is hurting production but hasn't crossed the
  pre-declared regression threshold yet (you're acting on judgment)
- A champion model needs to be reverted to a known-good prior version
  (e.g., post-incident root cause traces back to a model promotion)

## How auto-rollback works (the system's default)

Watchdog loop in the canary controller monitors:

- **Latency** — `/decide` p99 vs baseline (alert at +20%)
- **Throughput** — decisions/sec vs baseline (alert at –10%)
- **Decision distribution** — KS distance vs baseline distribution
  (alert at p < 0.01)
- **Business metric proxy** (when available) — offer-accept rate, fraud
  flag rate

If any threshold trips during the canary window, the controller:

1. Halts the canary ramp at the current traffic level
2. Reverts the MLflow alias swap (challenger → archive; champion stays
   as it was before promotion)
3. Posts a `rollback_event` to Kafka with attribution
4. Notifies (configured: PagerDuty webhook in AWS overlay; logger in
   local-kind)

If the watchdog itself fails, the next steps apply.

## Manual rollback procedure

### Step 1 — Identify the bad version

```bash
# What's the current champion alias pointing at?
just mlflow-ui   # browse in UI
# OR via MLflow CLI:
uv run mlflow models get-latest-versions --name cli_uplift_<segment>
```

Confirm the model version you want to revert AWAY from.

### Step 2 — Identify the desired prior version

```bash
# All versions of this model
uv run mlflow models search --filter "name = 'cli_uplift_<segment>'" \
  --order-by "version DESC"
```

Pick the version to revert TO. Confirm by looking at its run params and
metrics in the MLflow UI.

### Step 3 — Perform the alias swap (4-eyes required)

The alias-swap operation requires two distinct authorizing service
accounts. In local-kind this is a soft convention; in the AWS overlay
this is enforced by IRSA role separation.

```bash
# Service account A: stage the swap
MLFLOW_SA=account_a uv run mlflow models set-tag \
  --name cli_uplift_<segment> --version <prior_version> \
  --tag stage_for_promotion=true

# Service account B: confirm and execute
MLFLOW_SA=account_b uv run mlflow models update-model-version-aliases \
  --name cli_uplift_<segment> --version <prior_version> \
  --aliases champion
```

### Step 4 — Verify

```bash
# Champion alias now points to the prior version
uv run mlflow models get-model-version --name cli_uplift_<segment> --alias champion
# Decisioner picks up the new alias on its next model-refresh cycle (~60s).
# Force immediate refresh:
kubectl rollout restart deployment/decisioner -n real-time-ml
```

Watch the decision dashboard in Grafana — the action distribution should
return to the pre-promotion baseline within ~2 minutes.

### Step 5 — Document the rollback in `docs/incidents.md`

Per the incidents template — severity, detection, root cause,
resolution, prevention.

## When NOT to roll back

- **The challenger is performing slightly worse than the champion**.
  This is normal during canary and is what the gradual ramp is for —
  let the canary controller decide. Rolling back on instinct loses
  information.
- **The decision distribution shifted but business metrics haven't**.
  The distribution shift may be intentional (the new model is better
  at recognizing a segment shift). Wait for the off-policy eval signal
  before acting.
- **You don't have the prior version's MLflow run ID**. Don't guess.
  Find the right run first, then act. A "rollback" to the wrong version
  is just another bad promotion.

## What if the MLflow registry is itself down

- Decisioner caches the champion model in memory; it keeps serving the
  cached version until the cache refresh cycle (default 60s) fails to
  reach MLflow
- If MLflow is unreachable for >5 minutes, decisioner enters degraded
  mode (continues serving cached model; logs at WARN every 30s)
- Restore MLflow first per ADR 005's setup; alias state is in the
  Postgres backend, not in the artifact store

## Status

Stable through Day 0. Updated as Day 4's auto-rollback implementation
lands.
