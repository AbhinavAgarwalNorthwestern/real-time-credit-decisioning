# Real-Time Credit-Decisioning Platform

A real-time decision engine that, for every customer event in a card-issuer's
stream, decides within 50 ms whether to **offer a credit-limit increase**,
**trigger a fraud check**, or **do nothing** — using per-segment neural
uplift models composed with a contextual bandit, with a self-correcting
retraining loop that detects drift, retrains, shadow-scores, and promotes
safely.

[![python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org)
[![rust](https://img.shields.io/badge/rust-1.78-orange.svg)](https://www.rust-lang.org)
[![license](https://img.shields.io/badge/license-portfolio-lightgrey.svg)](#license)

---

## Architecture at a glance

Three planes, each with its own decomposition philosophy:

```
STREAMING (per-topic microservices)        SYNCHRONOUS (one Rust process)
  transactions  ─►  Kafka                    decisioner (axum + sqlx + ort)
  behavioral_features ─► RisingWave (MV)       1. RisingWave feature lookup
  news + news-sentiment ─► sentiment MV        2. Segment route
  drift_monitor → drift_events topic           3. Per-segment ONNX inference
  outcome_collector → decision_outcomes        4. Contextual bandit select
                                               5. Audit log (async)
                                               6. HTTP response
                                                                ⏱ p99 < 50 ms

BATCH (Metaflow on Kubernetes)
  retraining_flow.foreach(segment) → train T-learner → MLflow registry
  champion-challenger shadow → off-policy eval → canary 5%→100% → auto-rollback
```

The reasoning behind the split lives in
[ADR 004](docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md).

## Target headline numbers

| Metric | Target | When measured |
|--------|--------|---------------|
| `/decide` p99 latency | < 50 ms | Day 7 (k6) |
| Decisions / sec / replica | ≥ 5 000 | Day 7 |
| Off-policy eval challenger lift | > 0 % vs champion | Day 6 |
| Drift detection true-positive rate | ≥ 90 % | Day 5 |
| Retraining wall-clock (full fan-out) | < 30 min | Day 5 |

Measured numbers land in [`docs/04_results_and_metrics.md`](docs/04_results_and_metrics.md).

## Stack

| Layer | Tool | Why |
|-------|------|-----|
| Stream processing | Quixstreams (Kafka) | ADR 001 |
| Streaming SQL / feature store | RisingWave (MV over CDC) | ADR 002 |
| Batch orchestration | Metaflow `@kubernetes` | ADR 003 |
| Decision serving (request path) | Rust + axum + sqlx + ort (ONNX Runtime) | ADR 004 |
| Model registry | MLflow with `--serve-artifacts` | ADR 005 |
| K8s manifests | Kustomize base + overlays | ADR 006 |
| Cloud infra | Terraform → EKS (AWS); base works on any K8s | ADR 006 |

For typical big-bank production equivalents (Kafka Streams, Flink SQL, Spring Boot,
AWS Batch, Step Functions), see [docs/06_production_patterns.md](docs/06_production_patterns.md).

## Documentation

| Doc | Audience |
|-----|----------|
| [`docs/repo_layout.md`](docs/repo_layout.md) | First read for engineers |
| [`docs/01_problem_and_domain.md`](docs/01_problem_and_domain.md) → [`08_realtime_vs_batch.md`](docs/08_realtime_vs_batch.md) | Sequential conceptual chapters |
| [`docs/05_architecture.md`](docs/05_architecture.md) + [`docs/architecture_diagrams.md`](docs/architecture_diagrams.md) | System design |
| [`docs/decisions/`](docs/decisions/) | Architecture Decision Records |
| [`docs/runbooks/`](docs/runbooks/) | Operational how-to |
| [`docs/tour.md`](docs/tour.md) | 10-minute demo script |
| [`docs/project_book.md`](docs/project_book.md) | Narrative overview for non-engineers |
| [`docs/data_card.md`](docs/data_card.md), [`docs/model_card.md`](docs/model_card.md) | Per Google Data Cards + Mitchell et al. 2019 |
| [`docs/day0_log.md`](docs/day0_log.md) | Day 0 change log |
| [`docs/incidents.md`](docs/incidents.md) | Operational incidents |

## Quick start

### Local development on a kind cluster

```bash
uv sync
just kind-up
just mlflow-secret          # apply MLflow MinIO Secret from .env.local
just k8s-apply-local        # deploy via local-kind overlay
just test-finance-smoke
```

### AWS deployment

See [`infra/terraform/README.md`](infra/terraform/README.md) and the
`aws-eks` overlay in [`deployments/overlays/aws-eks/`](deployments/overlays/aws-eks/).

```bash
just tf-init
just tf-plan
just tf-apply               # creates billable AWS resources
just k8s-apply-aws
```

## Provenance

The streaming substrate (Kafka + RisingWave + MLflow + kind setup) was
scaffolded from a real-time-ML cohort course. The finance domain — synthetic
transaction stream, behavioral features, segment routing, neural uplift
models, contextual bandit, Rust decisioner, drift-triggered retraining,
off-policy evaluation, regulatory audit layer — is original work built
end-to-end for this repository. See
[ADR 007](docs/decisions/007-crypto-split-archive-retain-sentiment.md) for
the inheritance / authorship split.

The cohort-4 baseline lives in a sibling directory
(`C:\Users\abhin\realtime-ml-cohort-4-archive\`) on a `cohort-4` branch
at the pre-Day-0 commit; it is not pulled into this repo.

## License

Portfolio project. Code under repository license; included third-party
infrastructure manifests retain their upstream licenses.
