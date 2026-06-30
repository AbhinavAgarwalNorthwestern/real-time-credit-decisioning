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
| [004](004-monolithic-decisioner-microservices-where-they-help.md) | Monolithic decisioner on the request path; microservices on the streaming/batch planes | **Superseded by [008](008-python-fastapi-decisioner-supersedes-rust.md)** |
| [005](005-mlflow-artifact-proxy-not-direct-s3.md) | MLflow tracking server with `--serve-artifacts` proxy (clients don't talk to MinIO directly) | Accepted |
| [006](006-base-overlays-kustomize-not-helm.md) | Kustomize base+overlays for our manifests; Helm only for third-party charts | Accepted |
| [007](007-crypto-split-archive-retain-sentiment.md) | Crypto-domain code split — retain sentiment-scoring pattern; archive the rest | **Superseded by [011](011-drop-news-sentiment-retention.md)** |
| [008](008-python-fastapi-decisioner-supersedes-rust.md) | Python FastAPI decisioner — supersedes ADR 004 (Rust) | Accepted |
| [009](009-pure-risingwave-sql-for-feature-computation.md) | Pure RisingWave SQL for feature computation (no Python `behavioral_features` service) | Accepted |
| [010](010-synthetic-rct-treatment-assignment.md) | Synthetic RCT (50/50 random) for Day-2 training-data treatment assignment; IPW deferred to Day-6 OPE | Accepted |
| [011](011-drop-news-sentiment-retention.md) | Drop news + news-sentiment from active project surface (supersedes ADR 007); directories remain as Pau's-course archive | Accepted |
| [012](012-hot-challenger-retraining-strategy.md) | Hot-challenger retraining + comprehensive drift detection (7 detectors: PSI, KS, ADWIN, JS divergence, performance gap, schema, per-segment) | Accepted |
| [013](013-dev-vs-aws-validation-split.md) | Dev-vs-AWS validation split: laptop kind for sessions 1-5 + inner loop; AWS dev EKS for sessions 6+ integration, retraining, tags, and load tests | Accepted |

ADR 014+ are reserved for upcoming decisions (production retention
policy, multi-region DR strategy, etc.). They will be written as those
calls are made during the build, not preemptively.
