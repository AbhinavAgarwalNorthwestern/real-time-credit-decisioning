# ADR 003: Metaflow with `@kubernetes` (not `@batch`) for batch orchestration

**Status:** Accepted
**Date:** 2026-06-03
**Decision makers:** Platform owner

## Context

The retraining loop, off-policy evaluation, periodic backtests, and ad-hoc
batch jobs need an orchestrator that:

- Supports DAG-based step execution within a flow
- Fans out work across customer segments (one training pod per segment) —
  this is the "factory" pattern called out in the resume
- Tracks reproducible artifact lineage (which model produced from which
  data window from which code commit)
- Triggers from external events (drift detection) and from cron
- Runs the same flow code locally on a laptop and in production
- Does **not** lock us to a single cloud (ADR 006)

The streaming pipeline (Quixstreams, RisingWave) is always-on and lives
outside the orchestrator. Orchestration is for discrete, time-bounded
batch jobs only.

Options:

|                                  | Argo Workflows | Airflow      | Prefect 2    | Step Functions   | Metaflow      |
|----------------------------------|----------------|--------------|--------------|------------------|---------------|
| DAG-based                        | Yes            | Yes          | Yes          | Yes              | Yes           |
| K8s-native execution             | First-class    | KPO operator | KPO operator | N/A              | `@kubernetes` |
| Foreach / fan-out                | Native         | Workaround   | Yes          | Map-state        | `foreach`     |
| Artifact lineage built-in        | No             | No           | Yes          | No               | Yes           |
| Local-to-production parity       | Painful        | Painful      | Fine         | N/A              | Excellent     |
| Cloud-agnostic                   | Yes            | Yes          | Yes          | **No (AWS)**     | Yes           |
| AWS Batch path (optional)        | Custom         | Operator     | Operator     | Yes              | `@batch`      |
| Event triggers                   | Argo Events    | Sensors      | Webhooks     | EventBridge      | `@trigger` (Argo Events / EventBridge) |
| Production users                 | Intuit, BlackRock | LinkedIn, Airbnb | Klarna, UNICEF | AWS-native shops | Netflix, Outerbounds |

## Decision

We use **Metaflow** as the orchestration framework, with the **`@kubernetes`**
decorator as the default execution backend. The AWS-specific `@batch`
decorator is provided as an optional path in the AWS overlay (`flow_aws_batch.py`)
to defend AWS-Batch resume claims without forcing AWS as the only target.

## Consequences

### Positive

- **Foreach over segments is one line.** The "fan-out CLI propensity
  factory" pattern is a `foreach` step.
- **Artifact lineage is automatic** — every run has typed input/output
  artifacts tracked in the Metaflow datastore (S3-backed in our case).
- **Local-to-cloud parity is excellent.** `python -m flow run` works on a
  laptop and `--with kubernetes` switches it to the cluster with no code
  change.
- **`@kubernetes` runs on any K8s** (kind, EKS, GKE, AKS, on-prem) — preserves
  cloud-agnosticism (see ADR 006).
- **Compiles to Argo Workflows under the hood** when deployed to a K8s
  cluster with the Metaflow Argo plugin, so we get Argo's K8s execution
  guarantees and Metaflow's authoring ergonomics.
- **`@batch` path stays available.** Resume bullets about "fan-out training
  on AWS Batch" remain defensible — we provide a `flow_aws_batch.py` that
  swaps the decorator, with no other code change.

### Negative

- **`@trigger` requires Argo Events** (on K8s) or EventBridge (on AWS) — one
  extra component to install in the cluster overlay. Lightweight; standard.
- **Smaller community than Airflow** at large banks. Mitigated by
  Outerbounds' commercial support and Netflix's open documentation.
- **Linux-only CLI** (`fcntl` import). Mitigated via the devcontainer for
  Windows hosts.
- **Conditional `self.next()` is restricted** — each step transitions to
  exactly one next step. Forces flag-based skip patterns in some flows.

## Alternatives considered

- **Argo Workflows alone**: solid K8s-native orchestrator. Loses Metaflow's
  artifact lineage, typed parameters, and resume-from-step ergonomics. We
  effectively get Argo's runtime *anyway* (Metaflow compiles to it on K8s)
  so this choice is "Argo + Metaflow vs Argo alone."
- **Airflow**: most common at large banks. Forces YAML/Python DAG config;
  XCom is awkward for ML state; AWS Batch integration needs custom operators.
- **Prefect 2**: good local DX; K8s integration less mature than Metaflow's
  on the fan-out pattern.
- **Step Functions**: AWS-only. Defeats cloud-agnosticism (see ADR 006).
- **Metaflow with `@batch` as default**: rejected — would couple the default
  path to AWS. We provide `@batch` only as the AWS overlay path.

## Related

- ADR 006: cloud-agnostic base + AWS overlay strategy
- `services/retraining_flow/`: where the Metaflow flows live
- `docs/06_production_patterns.md`: how the streaming plane and the
  Metaflow batch plane communicate (only via Kafka events and MLflow artifacts)
