# 07 — Interview Q&A drill

Anticipated questions a senior ML / ML-eng interviewer asks about a
real-time decisioning system, with the canonical answers grounded in
this repo. Skeleton at Day 0; answers fill in as each day's work
produces measurements.

For each question: link to the source-of-truth doc + 1-paragraph
talk-track answer.

## Architecture / system design

### "Walk me through the system at a whiteboard."

→ `docs/05_architecture.md` (three planes + call graph)

Talk-track: "Three planes. Streaming plane is microservices-per-topic
on Quixstreams; decision plane is one Rust process for the request
path; batch plane is Metaflow flows triggered by drift events or cron.
The three planes only communicate via Kafka topics and MLflow model
registry — no direct calls across planes. The decisioner does five
things in-process per request: feature lookup from RisingWave, segment
route, ONNX uplift inference, bandit action select, audit log enqueue.
Latency budget: p99 < 50 ms."

### "Why monolithic decisioner instead of microservices everywhere?"

→ ADR 004

Talk-track: "Latency budget. Each Kafka hop is 5–15 ms; the request
path needs five steps; that's the whole SLO gone before any inference
work. Collapsed in-process the same logic runs in ~25 ms p99. We still
do microservices on the streaming plane where seconds-latency is fine."

### "Why RisingWave instead of Feast?"

→ ADR 002

Talk-track: "RisingWave's materialized views over the CDC stream IS the
feature-store pattern. Feast is one implementation of it. By using the
view directly we eliminate training-serving skew by construction — same
SQL serves training-time snapshot reads and serving-time point lookups."

## Modeling

### "Why uplift instead of prediction?"

→ `docs/01_problem_and_domain.md`

Talk-track: "We care about the causal lift of an intervention, not the
probability of an outcome. A customer who would have spent more anyway
doesn't need a CLI offer. Predicting their spend gets the wrong answer;
estimating uplift gets the right one."

### "Why T-learner over X-learner / DragonNet / causal forest?"

→ `docs/03_models_and_choices.md`

### "How do you handle calibration?"

_(Day 2 deliverable — populated then.)_

## MLOps / lifecycle

### "How do you roll out a new model safely?"

→ `docs/06_production_patterns.md` Pattern 4

Talk-track: "Five stages. Offline validation gate. Shadow scoring for
~1 hour. Off-policy evaluation with IPS / SNIPS / DR. Canary 5%→25%→100%
with auto-rollback. MLflow alias swap requires two-account approval —
that's our 4-eyes gate matching the SR 11-7 spirit."

### "What's your champion-challenger workflow?"

→ ADR 007 (planned Day 4) + `docs/runbooks/rollback.md`

### "How does the system detect that the model is going stale?"

→ `services/drift_monitor`, ADR 003

Talk-track: "PSI on inputs, KS or ADWIN on predictions. When any
detector fires, a drift_event lands on Kafka. Argo Events triggers the
Metaflow retraining flow. The fan-out trains per segment in parallel;
each segment goes through the validation gate before becoming a
challenger."

### "How does off-policy evaluation work here?"

→ `docs/06_production_patterns.md` Pattern 6

## Latency / performance

### "What's your p99 latency target and how do you hit it?"

→ ADR 004, `docs/04_results_and_metrics.md`

Talk-track: "p99 < 50 ms. We hit it by collapsing the request path into
one Rust process and using ONNX Runtime for inference in-process.
Feature lookup is one Postgres-protocol query to RisingWave at ~5 ms.
Inference per segment is 5–10 ms. Everything else is < 1 ms. Total
headroom is ~15 ms for tail latency."

### "How do you load-test?"

→ Day 7 — k6 with realistic traffic shape, measured against the SLO.
See `docs/04_results_and_metrics.md` once populated.

## Cloud / portability

### "Could this run on GCP / Azure / on-prem?"

→ ADR 006, `docs/06_production_patterns.md` Pattern 7

Talk-track: "Yes. K8s + Kustomize base+overlays. The base is
cloud-agnostic. Per-environment patches swap image registries, storage
classes, ingress controllers, object-store endpoints. The application
code uses `infra_lib.*` to abstract S3 endpoint and secret-provider
backend, so the same code works against MinIO, S3, or GCS S3-interop."

### "Walk me through your Terraform."

→ `infra/terraform/README.md`

Talk-track: "Terraform provisions everything *outside* the K8s cluster:
VPC, EKS, ECR, S3, IAM with IRSA. K8s workloads live in Kustomize
overlays, not Terraform. The split matches ADR 006 — IaC for
infrastructure, manifests for workloads."

## Regulatory / compliance

### "How does this work in a SR 11-7 environment?"

→ `docs/REGULATORY_COMPLIANCE.md` (Day 8)

### "Walk me through adverse-action notification."

→ `docs/REGULATORY_COMPLIANCE.md` + audit log schema in
`docs/02_data_and_features.md`

## Tools / honest framing

### "Is RisingWave / Quixstreams actually used in banks in production?"

Talk-track: "The patterns are. The specific tools are newer fintech /
modern-data-stack choices. JP Morgan, Goldman, Citi tend toward Kafka
Streams or Flink on the JVM, KSQL or Flink SQL for streaming SQL, and
in-house feature stores. The architectural pattern — Kafka as the
integration spine, MLflow as the model contract, K8s for compute — is
identical. The OSS tool choices let me build and demo end-to-end alone."

### "Did you build this in production at $employer?"

→ See the honesty framing established at project start. Talk-track:
"I built this as a portfolio project to demonstrate a production-grade
real-time ML architecture end-to-end — Kafka, streaming SQL, MLflow
champion-challenger, Rust serving at p99 < 50 ms. It mirrors the
architecture a bank would deploy, with modern OSS tooling so I could
ship it solo."

## What I expect them to skip

Some interviewers won't ask any of these and will test you on basic
fundamentals (system design board, ML fundamentals, SQL). The patterns
above are for the senior-bar interviews where they probe production
maturity.

## Status

Skeleton at Day 0. Each Day-N's measurements get folded into the
relevant talk-tracks so the answer always has a number behind it.
