# Project Book — Narrative Overview

The "blog post" version of the project. Useful for a recruiter or
non-engineer reading the repo top-to-bottom. Engineers should start at
`docs/repo_layout.md` and `docs/05_architecture.md` instead.

## What this is

A real-time decision engine for a card-issuing bank. Every customer
event — a card swipe, a balance check, an app open — goes through a
small pipeline that decides, in under 50 milliseconds, whether to:

- Offer the customer a credit-limit increase
- Trigger a fraud check
- Do nothing

The decision is made by neural uplift models specialized per customer
segment, composed with a contextual bandit that handles trade-offs
between expected lift, cost, and regulatory constraint. The system
detects when its inputs or outputs drift and retrains itself
automatically. Every decision is logged with full lineage so it can be
audited, replayed, or used to evaluate counterfactual policies before
they ever go live.

## Why this exists

To demonstrate a complete production ML system end-to-end — not a
notebook in a Docker container — in a domain where production maturity
matters (regulated financial services with sub-50ms latency SLOs).

The patterns implemented here mirror what a card-issuing bank would
deploy: streaming feature computation, low-latency synchronous serving,
champion-challenger model rollout, drift-triggered retraining,
off-policy evaluation, regulatory audit log. The tooling choices skew
modern OSS (Quixstreams, RisingWave, MLflow, Rust + axum, Metaflow on
Kubernetes) so the whole thing can be built and demonstrated
end-to-end without an enterprise budget.

## How it works (the short story)

The system has three planes that talk to each other only via Kafka
events and the MLflow model registry:

- **The streaming plane** continuously computes per-customer behavioral
  features and writes them to RisingWave (a streaming SQL database that
  doubles as our feature store).
- **The decision plane** is a single Rust process that, for every event,
  looks up the latest features, routes the customer to a segment-specific
  neural uplift model, runs the bandit to pick an action, and returns a
  decision in under 50 milliseconds.
- **The batch plane** is a Metaflow flow that runs when drift is
  detected or on a schedule, fanning out per-segment training in
  parallel, and producing a challenger model that goes through a
  five-stage promotion process before ever serving real traffic.

A drift monitor watches both the input distributions and the prediction
distributions; when something shifts beyond tolerance, it kicks off the
retraining flow automatically. This is what makes the system
self-correcting in production.

## What's hard about this

The architectural challenge isn't any single component — it's the
boundary between them. The streaming, decision, and batch planes have
different latency tolerances (seconds, milliseconds, minutes) and
different failure modes. The system is built so a batch flow failure
at 3am cannot affect the latency on the decision plane. That's enforced
by the Kafka topic boundary — there's no service-to-service call
crossing planes.

The latency budget on the decision plane is also tight. The synchronous
path does five things per request — feature lookup, segment route,
ONNX inference, bandit select, audit log enqueue — in under 50 ms. ADR
004 has the budget table showing why decomposing this into microservices
would force the p99 past 70 ms. Collapsing it into one Rust process
hits the budget with ~15 ms of headroom for tail latency.

The regulatory layer is the third hard part. Credit decisions in the
US fall under ECOA / Regulation B and SR 11-7. The system logs SHAP
attribution per decision against a no-action baseline, enforces a
feature allowlist excluding protected attributes, generates reason codes
for adverse-action notification, and gates model promotion behind a
4-eyes approval pattern that mirrors the spirit of MRM committee review.

## What's deliberately not here

- **Generative AI** — this is a complete data-scientist-and-ML-engineer
  project, not an AI-engineering portfolio. The one LLM touch is the
  `news-sentiment` service that scores news, which feeds the macro
  feature view. Everything else is classical ML in a production wrapper.
- **Underwriting logic** — the system decides whether to act on an event,
  not whether to issue a card or set the initial limit
- **Fraud detection in itself** — the system can flag an event for
  downstream fraud-system inspection, but it does not approve or decline
  transactions
- **Marketing personalization** — the action space is operational, not
  promotional

## What was hard about building it

Three things stand out from Day 0 alone:

- **The MLflow artifact path**. The default Bitnami Helm chart routes
  artifacts directly from clients to the underlying S3 / MinIO. That
  failed for clients outside the cluster because they couldn't resolve
  the cluster-internal storage DNS, and distributing storage credentials
  to every client widens the attack surface. The fix was to run MLflow
  with `--serve-artifacts` (artifact proxy mode) so clients send
  artifacts to the MLflow server which proxies to MinIO server-side.
  See ADR 005. The rotated credentials and the rotation script live in
  `scripts/create-mlflow-secret.sh`.

- **The architectural microservices question**. The reference pipeline
  (the crypto system the streaming substrate came from) decomposes
  everything into per-topic microservices. Mechanical application of
  that pattern to the credit-decisioning request path would blow the
  latency budget. The collapse to a single Rust decisioner is ADR 004 —
  the senior-architect call.

- **Cloud-agnosticism without losing the AWS story**. The architecture
  needs to run on AWS to defend AWS-specific resume claims, but should
  not couple to AWS at the application-code level. Kustomize base +
  overlays handles this. The `base/` is K8s-native and cloud-agnostic;
  per-environment overlays patch image registries, storage classes,
  ingress controllers, and (importantly) the MLflow Deployment's
  credential source — moving from a K8s Secret in local-kind to IRSA in
  AWS. The Terraform layer in `infra/terraform/` provisions everything
  outside the cluster; Kustomize deploys workloads into it.

## What I'd build next

- **Full SR 11-7 mapping document** (Day 8 deliverable) — the
  `docs/REGULATORY_COMPLIANCE.md` chapter walks through how each MRM
  workflow stage maps to an artifact this system already produces.
- **Online bandit-arm posterior updates** (Day 6 stretch) — arms keep
  learning between retraining cycles via Thompson sampling.
- **Multi-cloud overlays** — the on-prem and GCP overlays are
  placeholders; populating them would round out the cloud-agnostic
  claim.

## Where to dig in

- **Engineers wanting the architecture**: `docs/05_architecture.md`
- **Engineers wanting the layout**: `docs/repo_layout.md`
- **Engineers wanting the decisions**: `docs/decisions/`
- **Interviewers wanting the demo**: `docs/tour.md`
- **Recruiters / non-engineers**: this document

## Status

Stable through Day 0. Updated as Days 1–8 land their changes.
