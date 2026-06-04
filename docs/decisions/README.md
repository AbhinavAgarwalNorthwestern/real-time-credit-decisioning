# Architecture Decision Records

Short documents that capture each important architectural decision in this
project — the context that led to it, what we decided, and the consequences
(good and bad) of that choice.

We use the [Michael Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

- **Context** — what's the situation that demands a decision?
- **Decision** — what did we decide to do?
- **Consequences** — what happens because of this decision (positive and negative)?

ADRs are **immutable**. If a decision is reversed, write a new ADR that
supersedes the old one. Don't edit the original.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [001](001-quixstreams-over-kafka-streams.md) | Quixstreams over Kafka Streams / Flink for stream processing | Accepted |
| [002](002-risingwave-as-feature-store-not-feast.md) | RisingWave materialized views as feature store (no Feast layer) | Accepted |
| [003](003-metaflow-kubernetes-not-batch.md) | Metaflow with `@kubernetes` (not `@batch`) — cloud-agnostic batch orchestration | Accepted |
| [004](004-monolithic-decisioner-microservices-where-they-help.md) | Monolithic Rust decisioner on the request path; microservices on the streaming/batch planes | Accepted |
| [005](005-mlflow-artifact-proxy-not-direct-s3.md) | MLflow tracking server with `--serve-artifacts` proxy (clients don't talk to MinIO directly) | Accepted |
| [006](006-base-overlays-kustomize-not-helm.md) | Kustomize base+overlays for our manifests; Helm only for third-party charts | Accepted |

ADRs 007–010 are placeholders for upcoming decisions (champion-challenger
shape, bandit-on-uplift composition, off-policy evaluation gate, Terraform-not-CDK).
They will be written as those calls are made during the build, not preemptively.
