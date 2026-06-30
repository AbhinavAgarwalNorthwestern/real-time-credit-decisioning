# 06 — Production Patterns

Walkthrough of the patterns this system implements that distinguish a
production ML system from a notebook. Written as interview-ready
explanations — each section answers a question a senior interviewer
will ask, in the form of an architectural justification.

## Pattern 1 — Separation of concerns across three planes

**Question form**: "Why don't you serve, train, and feature-build in one
service?"

The streaming, decision, and batch planes have different SLOs
(seconds, milliseconds, minutes) and different failure modes. A retraining
job failing at 3am should not affect the request path. A streaming
feature computation falling behind should not block training. The
Kafka topic boundary makes the failure isolation operationally real.

See `docs/05_architecture.md` for the diagram.

## Pattern 2 — Monolithic decisioner on the synchronous path

**Question form**: "Why is the decisioner one process instead of
N microservices?"

Microservices on the request path force serialization round-trips
between each step. At p99 < 50 ms, that's not affordable. ADR 004 has
the full latency-budget table — short version: collapsed in-process
beats decomposed-over-Kafka by ~40 ms p99.

The collapse is for the request path only. The streaming plane stays
microservice-per-topic.

**Implementation note**: ADR 008 supersedes ADR 004's *language* choice
(Python FastAPI + asyncpg + onnxruntime + aiokafka) without changing the
*architectural* choice (single process). Rust remains documented as the
production-hardening optimization path in `docs/jvm_equivalents.md`. The
latency-budget envelope still holds: measured p99 ~30-50 ms achievable
with uvicorn + 4 workers + onnxruntime GIL-release during inference.

## Pattern 3 — RisingWave materialized views as the feature store

**Question form**: "Where does training-serving feature parity come
from?"

It comes from the engine, not from convention. RisingWave's MV
semantics guarantee that the same query returns the same value at
training time (snapshot read with WHERE on event time) and at serving
time (point lookup via the Postgres protocol). There's nothing for a
developer to remember.

Detailed reasoning: ADR 002.

## Pattern 4 — Champion-Challenger with shadow + off-policy eval + canary + auto-rollback

**Question form**: "How do you safely roll out a new model?"

Five-step pipeline:

1. **Offline validation gate**: new model must beat current champion on
   the same held-out test set AND not regress on any segment AND produce
   a calibrated prediction distribution.
2. **Shadow scoring**: champion still serves all traffic; challenger's
   would-be decisions are logged but not acted on for ~1 hour. Pure read
   path; zero customer impact.
3. **Off-policy evaluation** (IPS / SNIPS / Doubly-Robust): estimate
   what the challenger's reward would have been on the last N hours of
   real decisions, using the logged propensities. Quantifies expected
   lift before any live traffic.
4. **Canary**: 5% → 25% → 100% traffic to challenger with auto-rollback
   if any business or operational metric regresses by more than a
   pre-declared threshold.
5. **Audit**: MLflow alias swap requires two distinct service accounts
   to approve (4-eyes principle; mirrors SR 11-7 MRM committee process).

## Pattern 5 — Drift-triggered retraining (closes the production loop)

**Question form**: "How does the system stay accurate as the world
changes?"

`drift_monitor` continuously computes PSI on input features and KS /
ADWIN on prediction distributions. When any detector fires, it emits a
`drift_event` to Kafka. Argo Events (K8s) or EventBridge (AWS) listens
for that event and triggers `retraining_flow` (Metaflow). The flow
fans out per segment, trains, validates against the champion-promotion
gate (Pattern 4), and either promotes a challenger or archives a
rejected one.

This is what turns "an ML system" into "a production ML system that
improves itself." See `docs/runbooks/drift_response.md` for what
operators do when a drift event fires unexpectedly.

## Pattern 6 — Off-policy evaluation as the promotion gate

**Question form**: "How do you know the challenger is better before
running an A/B test?"

The bandit logs the **propensity** with which it chose each action.
That lets us run inverse-propensity-score (IPS), self-normalized IPS
(SNIPS), and doubly-robust (DR) estimators over the decision log to
estimate what the challenger's expected reward **would have been** if
it had served the same traffic.

If IPS shows the challenger would have outperformed by a confidence-
bounded margin, the canary starts. Otherwise the challenger is archived
without ever serving real traffic. Off-policy eval is the cheap version
of an A/B that runs continuously, not just on promotion candidates.

This is the FAANG-grade DS-rigor signal that's hardest to fake in
interviews — having an OPE harness in the repo is the demoable artifact.

## Pattern 7 — Cloud-agnostic substrate with optional AWS overlay

**Question form**: "Could this run on GCP / on-prem instead of AWS?"

Yes, by design. The application code runs in K8s; storage is S3-API
(MinIO locally, S3 in AWS, GCS-S3-interop on GCP); orchestration is
Metaflow `@kubernetes` (any K8s cluster); event triggers are Argo
Events (cloud-agnostic). The AWS overlay (`deployments/overlays/aws-eks/`)
swaps endpoints to AWS-native services; the application code does not
change.

ADRs 003, 006 are the relevant decisions.

## Pattern 8 — Regulatory compliance as a first-class output of the system

**Question form**: "How does this work in a real bank with SR 11-7 and
ECOA?"

Every decision logs:
- Model URIs (champion + challenger) for lineage
- Feature vector hash for reproducibility
- SHAP delta vs no-action baseline for explainability
- Regulatory flags (ECOA-protected-feature presence checks)

That makes:
- **Adverse-action notification**: reason codes can be generated from
  the SHAP delta + a code lookup table
- **Model audit**: any past decision can be replayed against the
  champion to verify the system did what it logged
- **MRM committee review**: model cards (Mitchell et al.) live in
  `docs/model_card.md`; promotion artifacts are exportable from MLflow

Day 8 documents the full SR 11-7 / Reg B / ECOA mapping in
`docs/REGULATORY_COMPLIANCE.md`.

## Pattern 9 — Static training datasets over a streaming substrate

**Question form**: "Data is arriving continuously — how do you train on a
fixed dataset?"

Three concerns conspire to make this harder than batch ML:

| Concern | Risk | Our mechanism |
|---|---|---|
| **Training-serving feature skew** | Features computed one way at training, differently at serving → silent model failure | Single MV serves both — RisingWave's `behavioral_features_5m` is queried at training (with `WHERE event_time < cutoff`) and at serving (point lookup); zero skew by construction (ADR 002 + 009) |
| **Point-in-time correctness** | Training row uses *future* feature values → label leakage; model overestimates in production | SQL window semantics — `LEFT JOIN behavioral_features f ON f.window_end <= t.event_time` ensures we only see features available at the transaction's moment |
| **Snapshot immutability** | Same training query a week later sees different rows → models not reproducible | Materialize the training join into an immutable table at training start: `CREATE TABLE training_snapshot_v1 AS SELECT … WHERE event_time < cutoff`; MLflow run logs the snapshot URI as a dataset artifact |

The snapshot table gets a content-hash signature, a row count, and a
cutoff timestamp logged to MLflow alongside the model. Reproducing the
model means: re-create the snapshot from (cutoff timestamp + DDL at git
SHA + same seed). That's what makes the whole training loop replayable
months later.

## Pattern 10 — Cold-start tiered fallback

**Question form**: "What happens when a brand-new customer's first
transaction arrives — the bandit has never seen them?"

Two distinct cold-start problems:

**System cold-start** (we just turned the platform on, no history at all):
solved by backfilling synthetic history (Day 2) or genuine historical
events (production scenario) before any live decisions are made. The
system is never trained on truly zero data.

**Customer cold-start** (existing system, new customer): a three-tier
fallback in the decisioner's request path:

```
For each /decide call:

  Tier 1: customer has ≥ 30 events in last 90 days
          → use per-customer feature vector from RisingWave MV
          → T-learner predicts individualized uplift
          → bandit acts on that prediction

  Tier 2: customer has 1-29 events
          → blend customer features with segment prior, weighted
            by sample count (Bayesian shrinkage)
          → T-learner uses the blended vector

  Tier 3: customer has 0 events (just-onboarded)
          → use segment-level prior derived from training data
          → segment comes from application-form data (already known)
          → add exploration noise (ε-greedy on Day 3;
            Thompson sampling on Day 6 stretch)
```

Day 4 specifically tests this: inject 50 customers with zero behavioral
history into the cohort, verify the decisioner returns reasonable actions
for them (segment defaults + exploration noise), and watch them naturally
"graduate" from Tier 3 → Tier 2 → Tier 1 as events accumulate.

The segment in Tier 3 comes from data the bank already has at customer
onboarding (risk score, tenure proxy, demographic) — not from transaction
history. That's how we have a feature vector for someone who's never
transacted.

## Pattern 11 — Training-cutoff matrix per outcome horizon

**Question form**: "How do you choose what data goes into training?"

Three considerations balance:

| Consideration | Pulls cutoff toward... |
|---|---|
| Recency (current patterns) | More recent data |
| Outcome maturity (labels need time to materialize) | Older data |
| Volume (enough samples) | Older / wider window |

The longest outcome horizon dominates. Different model heads have
different outcome horizons:

| Outcome label | Horizon | Training cutoff at training time `T` | Training window |
|---|---|---|---|
| CLI acceptance | ~14 days | `T − 14 days` | `T − 90 days` to `T − 14 days` |
| Fraud confirmation | ~7 days | `T − 7 days` | `T − 90 days` to `T − 7 days` |
| Default | ~12 months | `T − 12 months` | `T − 24 months` to `T − 12 months` |

In production this means default-prediction models are always one year
behind current data. Banks accept this — it's the cost of label maturity.

**Synthetic shortcut for our portfolio**: because our data generator
controls the *true* response parameters per customer, we synthesize
outcome labels at transaction-emit time using those parameters. This
collapses the horizon to zero in synthetic mode, letting Day 2 train +
validate the full pipeline in one pass. **We document this shortcut
explicitly in `docs/data_card.md`** so no interviewer thinks we're
claiming this works in real production. Day 7's
`docs/REGULATORY_COMPLIANCE.md` notes that production deployment would
follow the matrix above.

## Pattern 12 — Backfill in three flavors

**Question form**: "Real-time ML doesn't have a 'load the file' step. How
do you handle backfill?"

Three distinct backfill needs in our system, each with a different mechanism:

### Backfill A — historical transactions for model training

**Need**: Day 2 cannot train on whatever's accumulated since Day 1.
Needs ~30-90 days of historical data with realistic per-customer time
series.

**Mechanism**: `services/transactions` runs in `--mode backfill` with
`--start` and `--end` timestamps. The producer emits events with
*historical* `event_time` values (within the specified range), as fast
as Kafka can accept. RisingWave's watermark advances as historical events
flow through; materialized views compute features for the historical
window.

Day 2 starts with `TXN_MODE=backfill TXN_START="<90 days ago>"` →
~15 minutes of producing → 90 days of synthetic history in RisingWave →
train Day 2 models on it.

### Backfill B — decision history for off-policy evaluation

**Need**: Day 6 evaluates "would the new policy have done better than the
champion?" — needs to score historical decisions with a new policy and
compute IPS/SNIPS/DR estimators.

**Mechanism**: decisions are already persisted in the `decisions` Kafka
topic (30-day retention per `kafka-topics.yaml`) AND mirrored into a
RisingWave table. Day 6's OPE notebook reads from there:

```sql
SELECT decision_id, customer_id, action_taken, propensity,
       feature_vector, outcome_kind, outcome_value
FROM decision_outcomes
WHERE decision_ts BETWEEN ? AND ?;
```

The OPE estimator computes the lift of *any* new policy over the logged
policy without re-running history. No additional infrastructure needed —
the audit log we already keep IS the backfill source.

### Backfill C — outcome events arriving late

**Need**: real outcomes don't arrive immediately. CLI acceptance might
come in days, default in months. Late outcomes need to join back to old
decisions.

**Mechanism**: `outcome_collector` service (Day 6) consumes the `outcomes`
topic and joins to `decisions` in a streaming materialized view:

```sql
CREATE MATERIALIZED VIEW decision_outcomes AS
SELECT
    d.decision_id, d.customer_id, d.action_taken,
    d.propensity, d.feature_vector,
    o.outcome_kind, o.outcome_value, o.observed_at
FROM decisions d
LEFT JOIN outcomes o
    ON d.decision_id = o.decision_id;
```

The `LEFT JOIN` means: every decision has a row immediately; outcomes
append as they arrive. RisingWave's incremental MV maintenance updates
this in real-time as new outcome events land. Day 6 reads from
`decision_outcomes` for OPE; the same view feeds the regulatory audit
log (Day 7's `docs/REGULATORY_COMPLIANCE.md`).

## What this section is NOT

It is NOT the runbook. Runbooks are in `docs/runbooks/`. This section
is "what the patterns are and why" — operational how-to lives elsewhere.

## Status

Patterns stable through Day 0. Each pattern's implementation is
populated across Days 1–8; section anchors stay fixed so cross-references
from interview-prep doc (`docs/07_interview_qa.md`) don't break.
