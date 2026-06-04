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

**Question form**: "Why is the decisioner one big Rust binary instead of
N microservices?"

Microservices on the request path force serialization round-trips
between each step. At p99 < 50 ms, that's not affordable. ADR 004 has
the full latency-budget table — short version: collapsed in-process
beats decomposed-over-Kafka by ~40 ms p99.

The collapse is for the request path only. The streaming plane stays
microservice-per-topic.

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

## What this section is NOT

It is NOT the runbook. Runbooks are in `docs/runbooks/`. This section
is "what the patterns are and why" — operational how-to lives elsewhere.

## Status

Patterns stable through Day 0. Each pattern's implementation is
populated across Days 1–8; section anchors stay fixed so cross-references
from interview-prep doc (`docs/07_interview_qa.md`) don't break.
