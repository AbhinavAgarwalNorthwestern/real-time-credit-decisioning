# Infrastructure — Reference Documentation

This is the **operational reference** for the platform's infrastructure:
what each cluster component is, what every file in `deployments/dev/kind/`
does, how to bootstrap from scratch, and what to do when each piece dies.

For **design rationale** (why Kustomize, why RisingWave-as-feature-store,
why FastAPI not Rust) see the ADRs under `docs/decisions/`. This document
deliberately doesn't repeat those.

---

## 1. Overview

The platform runs entirely inside a local **kind** (Kubernetes-in-Docker)
cluster during development, with a published **AWS EKS overlay** for
production (Day 7+). The same workloads + manifests + Kustomize overlays
deploy to either target.

Six infrastructure components are stood up before any application code:

| Component | Why we need it |
|---|---|
| **ingress-nginx** | HTTP routing into the cluster from the host |
| **Strimzi Kafka** | The streaming event bus for transactions + decisions + outcomes |
| **Kafka UI** | Browser-based introspection of topics, messages, consumer lag |
| **RisingWave** | Streaming SQL + materialized views — our **feature store** (ADR 002) |
| **PostgreSQL** (bundled with RisingWave) | Backend store for MLflow metadata |
| **MinIO** (bundled with RisingWave) | S3-compatible object store for MLflow artifacts + ML model files |
| **MLflow** | Experiment tracking + model registry (ADR 005) |
| Grafana | Observability dashboards — **deferred to Day 7** (chart deprecated) |

Application services (`transactions`, `behavioral_features`, `decisioner`,
`drift_monitor`, `retraining_flow`, `outcome_collector`) deploy *on top of*
this infrastructure starting Day 1 Phase D.

---

## 2. Architecture diagram

ASCII overview of how the pods talk to each other in the kind cluster.

```
host (devcontainer terminal / browser)
    │
    │ host port mappings via kind-with-portmapping.yaml
    │   80    → ingress-nginx     :80
    │   443   → ingress-nginx     :443
    │   31092 → kafka external bootstrap (NodePort)
    │   31234 → kafka broker 0    (NodePort)
    │   31235 → kafka broker 1    (NodePort, future)
    │   31236 → kafka broker 2    (NodePort, future)
    │   4567  → risingwave        :4567 (Postgres protocol)
    │   9000  → minio API         (when port-forwarded)
    │   9001  → minio console     (when port-forwarded)
    │   8889  → mlflow tracking   (when port-forwarded)
    │
    ▼
┌────────────────────────────────────────────────────────────────────┐
│  kind cluster (control-plane node: rwml-34fa-control-plane)        │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ns: ingress-nginx                                           │   │
│  │   ingress-nginx-controller (Pod)                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ns: kafka                                                   │   │
│  │   strimzi-cluster-operator (Pod)                            │   │
│  │   kafka-e11b-dual-role-0   (Pod — controller + broker, KRaft│   │
│  │       Service: kafka-e11b-kafka-bootstrap:9092 (in-cluster) │   │
│  │       Service: kafka-e11b-kafka-external-bootstrap:9094     │   │
│  │   kafka-e11b-entity-operator (Pod — topic + user operators) │   │
│  │   kafka-ui (Pod)                                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ns: risingwave                                              │   │
│  │   risingwave-meta-0        (Pod — metadata service)         │   │
│  │   risingwave-compute-0     (Pod — query execution)          │   │
│  │   risingwave-frontend-*    (Pod — SQL endpoint, Postgres proto)│   │
│  │   risingwave-compactor-*   (Pod — LSM compaction)           │   │
│  │   risingwave-postgresql-0  (Pod — backend Postgres)         │   │
│  │       used by:                                              │   │
│  │         • RisingWave's own metadata storage                 │   │
│  │         • MLflow's --backend-store-uri                      │   │
│  │   risingwave-minio-*       (Pod — S3-compatible storage)    │   │
│  │       buckets:                                              │   │
│  │         • risingwave    (RW's own object storage)           │   │
│  │         • mlflow-d971   (MLflow artifacts)                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ns: mlflow                                                  │   │
│  │   mlflow-tracking         (Pod — server with --serve-artifacts│
│  │     Reads from:                                             │   │
│  │       • Postgres (metadata)  → risingwave-postgresql        │   │
│  │       • MinIO    (artifacts) → risingwave-minio (proxied)   │   │
│  │     ADR 005 — artifact-proxy pattern                        │   │
│  │   Secret: mlflow-minio-secret (AccessKeyID + SecretKey)     │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ns: monitoring (deferred — Grafana chart deprecated)        │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

Application-service plane (added Day 1 onward):

```
synthetic txn stream → kafka(transactions topic) → behavioral_features pod
                                                      │
                                                      ▼
                                 RisingWave Source → MV "behavioral_features"
                                                      │
                                                      ▼ (Postgres protocol)
                                              decisioner (FastAPI) /decide
                                                      │
                                                      ▼
                                              kafka(decisions topic)
                                                      │
                                                      ▼
                                              outcome_collector
                                                      │
                                                      ▼
                                 RisingWave MV "decision_outcomes" → OPE
```

---

## 3. Component inventory

| Component | Namespace | Pod(s) | In-cluster service | Host-exposed port |
|---|---|---|---|---|
| ingress-nginx | `ingress-nginx` | `ingress-nginx-controller-*` | `ingress-nginx-controller` | 80, 443 |
| Strimzi operator | `kafka` | `strimzi-cluster-operator-*` | (none — operator) | — |
| Kafka broker | `kafka` | `kafka-e11b-dual-role-0` | `kafka-e11b-kafka-bootstrap:9092` | 31092 (ext bootstrap), 31234 (broker 0) |
| Kafka entity operator | `kafka` | `kafka-e11b-entity-operator-*` | (none) | — |
| Kafka UI | `kafka` | `kafka-ui-*` | `kafka-ui:8080` | (port-forward as needed) |
| RisingWave meta | `risingwave` | `risingwave-meta-0` | `risingwave-meta:5690` | — |
| RisingWave compute | `risingwave` | `risingwave-compute-0` | (gRPC) | — |
| RisingWave frontend | `risingwave` | `risingwave-frontend-*` | `risingwave:4567` | 4567 |
| RisingWave compactor | `risingwave` | `risingwave-compactor-*` | (internal) | — |
| Postgres backend | `risingwave` | `risingwave-postgresql-0` | `risingwave-postgresql:5432` | — |
| MinIO | `risingwave` | `risingwave-minio-*` | `risingwave-minio:9000` (API), `:9001` (console) | (port-forward) |
| MLflow tracking | `mlflow` | `mlflow-tracking-*` | `mlflow-tracking:80` | (port-forward 8889) |

---

## 4. File-by-file walkthrough

### 4.1 Bootstrap orchestrator

#### `deployments/dev/kind/create_cluster.sh`

**Purpose**: One-command bootstrap of the full cluster from scratch.

**What it does, in order**:

1. Delete any existing cluster named `rwml-34fa` (idempotent)
2. Delete and recreate the Docker network `rwml-34fa-network` on `172.200.0.0/16`
3. `kind create cluster --config kind-with-portmapping.yaml`
4. `kubectl wait --for=condition=Ready node`
5. Apply `manifests/ingress-nginx-all-in-one.yaml`
6. Run `install_risingwave.sh` (RW first because MLflow depends on its bundled Postgres + MinIO)
7. Run `install_kafka.sh`
8. Run `install_kafka_ui.sh`
9. Run `install_grafana.sh` (tolerated failure — deprecated chart)
10. Print manual MLflow next-steps

**Key choices**:
- `set -uo pipefail` (not `-e`) — script continues past Grafana failure but still aborts on undefined vars or pipe failures
- `$SCRIPT_DIR` resolved via `BASH_SOURCE` so the script works from any cwd
- Cluster name + network name extracted to top-of-file variables

**Depends on**: `kind`, `kubectl`, `docker`, `helm` — all installed via `mise.toml`.

**Depended on by**: nothing — top of the dependency tree.

---

#### `deployments/dev/kind/kind-with-portmapping.yaml`

**Purpose**: kind cluster definition.

**Key contents**:
- 1 control-plane node (no separate worker nodes — sufficient for dev)
- `extraPortMappings` mapping host ports 80, 443, 4567, 8080, 8181, 8182, 9001, 31092, 31234-31236 to the same ports in the node
- That mapping is what lets you `curl http://localhost:4567` from the host and reach the RisingWave frontend

**Edit if**: you need to expose a new port to the host. For most workloads, prefer `kubectl port-forward` instead of editing this file (no cluster recreate required).

---

### 4.2 Per-component installers

Each script is **idempotent, path-independent, and tolerates partial failure of its sub-steps**.

#### `deployments/dev/kind/install_risingwave.sh`

**Purpose**: Install RisingWave + bundled Postgres + bundled MinIO into the `risingwave` namespace.

**What it does**:
1. `helm repo add risingwavelabs https://risingwavelabs.github.io/helm-charts/ --force-update`
2. `helm repo update`
3. `helm upgrade --install --create-namespace --wait risingwave risingwavelabs/risingwave -f manifests/risingwave-values.yaml`

**Bundled pieces** (configured by the helm chart's values):
- Postgres pod for RW's own metadata + the `mlflow` database we add later
- MinIO pod with two buckets pre-created: `risingwave` (RW's storage) and `mlflow-d971` (used by MLflow)
- Auto-generated credentials in Secret `risingwave-minio`

**Why install RisingWave first**: MLflow needs the bundled Postgres + MinIO to be Ready before it can come up. Install order in `create_cluster.sh` reflects this.

---

#### `deployments/dev/kind/install_kafka.sh`

**Purpose**: Install the Strimzi Kafka operator + the `kafka-e11b` Kafka cluster CR.

**What it does**:
1. `kubectl create namespace kafka` (idempotent)
2. `kubectl apply --server-side --force-conflicts -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka`
   - `--server-side` is **required** — the Strimzi CRDs are too large for client-side apply's 256 KB `last-applied-configuration` annotation; client-side apply silently drops them
3. Wait for Strimzi operator Deployment Ready (180 s)
4. Wait for CRDs `Established` — closes a race where the operator deployment is Ready before the API server is serving the new resource kinds
5. Apply `manifests/kafka-e11b.yaml` — the Kafka cluster CR + KafkaNodePool

**Critical version notes**:
- Manifest uses `apiVersion: kafka.strimzi.io/v1` (Strimzi 0.46+ removed `v1beta2`)
- Manifest pins Kafka version to `4.1.2` (Strimzi 0.50+ no longer supports Kafka 3.9)

**Depends on**: kubectl, network access to strimzi.io and `quay.io/strimzi`.

---

#### `deployments/dev/kind/install_kafka_ui.sh`

**Purpose**: Install Provectus's Kafka UI for browser introspection of the broker.

**What it does**:
- `kubectl apply -f manifests/kafka-ui-all-in-one.yaml`

The manifest creates a Deployment + Service in the `kafka` namespace; the UI auto-discovers the bootstrap server by environment variable.

**Access**: `kubectl -n kafka port-forward svc/kafka-ui 8182:8080` then open `http://localhost:8182`.

---

#### `deployments/dev/kind/install_grafana.sh`

**Purpose**: Install Grafana via the `grafana/grafana` Helm chart.

**Status**: ⚠ **Deprecated chart — runs but typically fails to come Ready in kind.** `helm` is called with `--timeout=60s` so failure doesn't pause the bootstrap. `create_cluster.sh` tolerates the failure and continues.

**Day 7 plan**: swap to `bitnami/grafana` or `grafana-operator`.

---

### 4.3 Manifests applied during bootstrap

#### `manifests/ingress-nginx-all-in-one.yaml`

**Purpose**: HTTP routing for any Service that needs Ingress (currently none of ours — every service is reachable via port-forward in dev, and via the AWS Load Balancer Controller in EKS).

**Why install it anyway**: future Day 1+ services may want to add `Ingress` resources without re-bootstrapping. Having the controller pre-installed makes that a one-manifest change.

**Source**: official `kubernetes/ingress-nginx` "kind" provider manifest, copied here so the cluster bootstrap is offline-installable.

---

#### `manifests/kafka-e11b.yaml`

**Purpose**: The Kafka cluster custom resource. Two YAML documents:

1. **KafkaNodePool `dual-role`** — defines a node pool of size 1 with both `controller` and `broker` roles (KRaft mode, no ZooKeeper). 10 GiB persistent claim per replica.
2. **Kafka `kafka-e11b`** — references the node pool via the annotation `strimzi.io/node-pools: enabled`. KRaft mode enabled via `strimzi.io/kraft: enabled`. Two listeners:
   - `plain` on port 9092 — internal cluster traffic
   - `external` on port 9094 — `nodeport` type, advertised on `127.0.0.1`, with bootstrap nodePort 31092 and per-broker nodePorts 31234-31236

**Replication factors all set to 1** — minimum for single-broker dev. **Not** suitable for production; ADR for prod replication factors lives on Day 7.

**Schema version notes**: pinned to Kafka 4.1.2 + metadataVersion 4.1-IV0. Pau's original cohort manifest used Kafka 3.9 + apiVersion v1beta2; both got bumped during Day 1 because the Strimzi operator deployed by `strimzi.io/install/latest` no longer supports those.

---

#### `manifests/kafka-ui-all-in-one.yaml`

**Purpose**: Kafka UI Deployment + Service, configured to discover the bootstrap server in-cluster.

Edit if you change the Kafka cluster name (the env var `KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS` references `kafka-e11b-kafka-bootstrap:9092`).

---

#### `manifests/risingwave-values.yaml`

**Purpose**: Helm values for the RisingWave chart — pins versions, enables the bundled Postgres + MinIO, and sets resource limits sane for kind.

**Important**: when the chart bumps a major version, the schema of this file can change. Read the upstream `helm-charts/charts/risingwave/values.yaml` before bumping.

---

#### `manifests/grafana-values.yaml`

**Purpose**: Helm values for the (deprecated) Grafana chart. **Not currently in effective use** because `install_grafana.sh` tolerates failure.

---

#### `manifests/mlflow-final.yaml`

**Purpose**: Our custom MLflow Deployment + Service per ADR 005.

**Key configuration**:

```yaml
command:
  - mlflow
  - server
  - --host
  - 0.0.0.0
  - --port
  - "5000"
  - --backend-store-uri
  - postgresql://postgres:postgres@risingwave-postgresql.risingwave.svc.cluster.local:5432/mlflow
  - --artifacts-destination
  - s3://mlflow-d971/
  - --serve-artifacts
  - --default-artifact-root
  - mlflow-artifacts:/
```

What each flag does:

| Flag | Purpose |
|---|---|
| `--backend-store-uri postgresql://...` | Metadata storage (experiments, runs, params, metrics, registered models) goes to the RisingWave-bundled Postgres |
| `--artifacts-destination s3://mlflow-d971/` | Where the server writes artifacts (models, plots, EDA reports) — the bucket already exists, RisingWave pre-creates it |
| `--serve-artifacts` | Clients send artifacts to MLflow over HTTP; MLflow proxies to MinIO server-side. Clients never need MinIO network reachability or credentials. (ADR 005) |
| `--default-artifact-root mlflow-artifacts:/` | Routes the client artifact URL through MLflow's artifact-proxy path instead of giving the client the raw S3 URL |

**Env vars consumed**:
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — from `mlflow-minio-secret` (server uses these to write to MinIO on the client's behalf)
- `MLFLOW_S3_ENDPOINT_URL=http://risingwave-minio.risingwave.svc.cluster.local:9000` — redirects boto3 from AWS S3 to our MinIO

---

#### `manifests/mlflow-minio-secret.yaml.example`

**Purpose**: Committed template for the `mlflow-minio-secret` Kubernetes Secret. The real Secret is generated at apply time by `scripts/create-mlflow-secret.sh` from `.env.local`, then `kubectl apply`-ed to the cluster.

The example file exists so future operators (or future-you) can see the expected schema without seeing the credentials.

---

### 4.4 Helper scripts

#### `scripts/create-mlflow-secret.sh`

**Purpose**: Convert credentials in `.env.local` into the `mlflow-minio-secret` Kubernetes Secret in namespace `mlflow`.

**Idempotent**: uses `kubectl apply` on a `dry-run=client -o yaml` rendering, so re-running updates the secret in place.

**Required env vars in `.env.local`**:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

---

#### `scripts/smoke_test_finance.sh`

**Purpose**: Phased validation that infrastructure + application code is functioning.

**Phase 1** (Day 1 today): infra health — Kafka broker Ready, RisingWave frontend Ready, MinIO Ready, MLflow tracking pod Ready.

**Phase 2** (Day 1 close): end-to-end data path — synthetic transaction → behavioral feature MV → row visible in RisingWave.

**Phase 3** (Day 3): `POST /decide` round-trip + audit log row.

Run with `PHASE=N bash scripts/smoke_test_finance.sh`.

---

## 5. End-to-end setup procedure

Fresh laptop, no cluster running. This is the sequence we just walked through.

```bash
# 1. Clone + open in VS Code devcontainer
git clone <repo>
cd realtime-credit-decisioning
# VS Code: "Reopen in Container"

# 2. Devcontainer build runs postCreateCommand which:
#    - installs mise tools (kubectl, helm, kustomize, k9s, awscli,
#      terraform, k6, rust, python)
#    - symlinks k9s configs
#    - installs pre-commit hooks
#    - adds mise activation to .bashrc

# 3. Activate mise in this shell (fresh shells auto-load via .bashrc)
eval "$(mise activate bash)"

# 4. Bootstrap the cluster
bash deployments/dev/kind/create_cluster.sh
#    Runs ~10-15 minutes. Bootstraps cluster + ingress + RW + Kafka + UI.
#    Grafana fails fast (deprecated chart, tolerated).

# 5. Manual MLflow setup
#    Extract MinIO root creds from the bundled secret
ROOT_USER=$(kubectl -n risingwave get secret risingwave-minio \
    -o jsonpath='{.data.root-user}' | base64 -d)
ROOT_PASS=$(kubectl -n risingwave get secret risingwave-minio \
    -o jsonpath='{.data.root-password}' | base64 -d)

#    Put them into .env.local
sed -i "s|^export AWS_ACCESS_KEY_ID=.*|export AWS_ACCESS_KEY_ID=${ROOT_USER}|" .env.local
sed -i "s|^export AWS_SECRET_ACCESS_KEY=.*|export AWS_SECRET_ACCESS_KEY=${ROOT_PASS}|" .env.local

#    Apply the secret + MLflow Deployment
bash scripts/create-mlflow-secret.sh
kubectl apply -f deployments/dev/kind/manifests/mlflow-final.yaml
kubectl -n mlflow rollout status deployment/mlflow-tracking --timeout=300s

# 6. Verify everything
PHASE=1 bash scripts/smoke_test_finance.sh
#    Should print "Phase 1 passed"
```

Once Phase 1 passes, infrastructure is done. Application services deploy
into this cluster on Day 1+.

---

## 6. The MLflow MinIO-key dance — why it's manual

MLflow needs MinIO credentials to write artifacts. Three approaches, in
ascending order of correctness:

| Approach | Setup cost | Security | Used today? |
|---|---|---|---|
| Use MinIO root user directly | 30 sec | Bad (full bucket admin) — fine for ephemeral dev | ✅ today |
| Console-create a scoped access key | 5 min (need port-forward to MinIO console + browser) | Good (limited to mlflow-d971 bucket) | ✗ |
| `mc admin user svcacct add` (mc CLI service account) | 2 min (need `mc` installed) | Best (programmatic; can be scoped + rotated automatically) | Day 7 hardening target |

**Why it can't be fully automated today**: the RisingWave Helm chart auto-generates the MinIO root password and stores it in `risingwave-minio` Secret. Programmatic creation of *additional* credentials requires either the MinIO API (which we'd need to script with `mc` or `curl` against the admin API) or the console (which needs a browser).

For a dev cluster that's ephemeral and never leaves the laptop, the
root-credential shortcut is acceptable. The credentials don't exist
outside the cluster. Day 7 introduces the `mc` automation as part of the
production-hardening pass.

---

## 7. Known issues + fixes log

Chronological list of what broke during initial setup and how we fixed it,
so the same time isn't burned re-debugging.

| Date | Symptom | Root cause | Fix |
|---|---|---|---|
| Day 0 Session 1 | MLflow artifact upload failed when client outside cluster | Bitnami chart's direct-S3 path needs cluster-internal DNS | ADR 005: switched to custom Deployment with `--serve-artifacts` |
| Day 0 Session 2 | MinIO credentials hardcoded in 3 places | Original course config | Rotated keys, moved to `.env.local`, gitignored manifests, added detect-secrets hook |
| Day 0 Session 3 | Repo was double-nested clones | Accidental clone-in-clone | Flattened + renamed to `realtime-credit-decisioning`; backed up orphan `.git` |
| Day 0 Session 5 | Rust decisioner skeleton built | Initial ADR 004 choice | (Later) ADR 008 superseded — pivoted to Python FastAPI on Day 1 to save toolchain debugging time |
| Day 1 Phase A | `mise install` failed on helm + rust transient network errors | Network blip during downloads | Added retry loop + tolerant `set -uo pipefail` (no `-e`) in postCreateCommand.sh |
| Day 1 Phase A | `kubectl` not on PATH | mise activation missing from `.bashrc` | postCreateCommand.sh now adds `eval "$(mise activate bash)"` |
| Day 1 Phase A | Container connection-refused to cluster API | Old kind cluster gone after devcontainer rebuild | `bash deployments/dev/kind/create_cluster.sh` rebuilds it |
| Day 1 Phase B | `create_cluster.sh` errored on missing files | Bare `./` paths assumed cwd | Rewrote with `$SCRIPT_DIR` resolution |
| Day 1 Phase B | Grafana install timed out | `grafana/grafana` chart deprecated | Reduced timeout to 60s, tolerate failure, mark as Day 7 swap |
| Day 1 Phase B | "No resources found in kafka namespace" after `kubectl apply -f kafka-e11b.yaml` | CRDs hadn't established when CR was applied (race) | install_kafka.sh now `--server-side --force-conflicts` + `kubectl wait --for=condition=Established` |
| Day 1 Phase B | "no matches for kind 'KafkaNodePool' in version 'v1beta2'" | Strimzi 0.46+ removed v1beta2 | Bumped manifest to `kafka.strimzi.io/v1` |
| Day 1 Phase B | Strimzi operator crash-loop, no broker created | Kafka version 3.9.0 unsupported (Strimzi 0.50+ requires 4.1+) | Bumped manifest to Kafka 4.1.2 + metadataVersion 4.1-IV0 |
| Day 1 Phase B | MLflow needs access key but MinIO console not reachable | Devcontainer ↔ host port-forward semantics | Skipped console; used MinIO root creds directly (good enough for dev) |
| Day 1 Phase B | Smoke test `FAIL RisingWave frontend pod not found` | Selector `app.kubernetes.io/component=frontend` doesn't match RW chart labels | Updated smoke test to `risingwave/component=frontend` (RW's actual label convention) |
| Day 1 Phase B | Smoke test `FAIL MinIO pod not found` | RW chart's MinIO sub-chart uses Bitnami-style labels, not RW's | Updated smoke test to `app.kubernetes.io/name=minio` |
| Day 1 Phase B | MLflow `FATAL: database "mlflow" does not exist` (CrashLoopBackOff) | Pau's setup required manual `CREATE DATABASE mlflow` after Postgres was Ready | Created `install_mlflow.sh` that auto-creates the DB + secret + applies Deployment; `create_cluster.sh` now invokes it |
| Day 1 Phase B | MLflow `connection refused` on port 5000 right after restart | Port-forward race — kubectl forwarded to port 5000 before MLflow finished pip install + DB wait + gunicorn bind | Retry the port-forward after ~10 s, or use `kubectl rollout status` to wait for the Deployment Ready signal first |
| Day 1 Phase D-5 | transactions pod stuck `CreateContainerConfigError`; `kubectl describe` showed missing `cluster-config` CM | Overlay's `configMapGenerator` does NOT inherit the base's `namespace:` declaration — generated CMs landed in `default`, Deployment in `real-time-ml` couldn't envFrom them | Added `namespace: real-time-ml` to `deployments/overlays/local-kind/kustomization.yaml`. Standard pattern: declare namespace at every overlay that contains generators, not only the base |
| Day 1 Phase D-5 | transactions container CrashLoop with `ModuleNotFoundError: No module named 'transactions'` | Dockerfile ran `uv pip install .` BEFORE `COPY src/ src/`; hatchling's `packages = ["src/transactions"]` produced an empty wheel because the source tree wasn't present at install time | Swap the order in `services/transactions/Dockerfile`: copy `src/` first, then install. Applies to every src-layout Python service using hatchling |
| Day 1 Phase D-5 | Smoke test phase 2 reported `No messages in 'transactions' topic` despite producer logs showing 79k+ events emitted | `scripts/smoke_test_finance.sh` used Kafka 3.x CLI (`kafka-run-class.sh kafka.tools.GetOffsetShell --broker-list`), removed in Kafka 4.x. Errors were silenced by `2>/dev/null`, so failure surfaced as zero messages | Switched to `kafka-get-offsets.sh --bootstrap-server`; dropped stderr suppression so future incompatibilities are visible |
| Day 1 Phase D-5 | Smoke test phase 2 reported `behavioral_features has 0 rows`; psql showed `table or source not found` | Two issues: (1) RisingWave DDL had never been applied to the current cluster (likely lost in a devcontainer rebuild) — `apply_ddl.sh` idempotently restores it. (2) DDL creates MVs named `behavioral_features_5m` and `behavioral_features_latest`, but the smoke test queried the unsuffixed name `behavioral_features` | Ran `bash deployments/dev/risingwave/apply_ddl.sh`. Updated smoke test to query `behavioral_features_5m` (the windowed aggregate; `_latest` is derived from it) |
| Day 2 D2-1a | New MV file `02_mv_events_enriched.sql` failed with `Invalid column: segment_id` despite the column being declared in `00_source_transactions.sql` | `CREATE SOURCE IF NOT EXISTS` silently skips schema updates when the source already exists. The live source had been created from an older revision of the DDL file that pre-dated `segment_id`. RW's `DESCRIBE transactions` confirmed the missing column | `DROP SOURCE transactions CASCADE` (cascades through all dependent MVs) + re-run `apply_ddl.sh`. Pattern: for any source DDL change, drop + recreate; `IF NOT EXISTS` won't migrate schemas. Future hardening: change `00_source_transactions.sql` to `DROP SOURCE IF EXISTS transactions CASCADE` followed by `CREATE SOURCE`, making it auto-migrating — but this costs the historical MVs' state on every apply, so document the trade-off before changing |
| Day 2 D2-1a | MinIO returned HTTP 429 TooManyRequests during `CREATE MATERIALIZED VIEW behavioral_features_7d` — RW hummock storage rate-limited by bundled dev MinIO. MinIO pod itself crashed (1 restart logged) under the load. Initial diagnosis incorrectly attributed parallel 04 failures to a SQL bug — they were the same root cause; 04 succeeded on re-run after MinIO recovered. Symptom recurred when even **reads** (a 7-MV UNION query) hit MinIO's rate cap — root cause turned out to be the bundled chart's MinIO request cap of `114` per the `x-ratelimit-limit` header. | Combined load: backfill of 79k+ Kafka events through 5 new windowed MVs + a live producer + RW's own compaction reads, against MinIO configured with a 114-req cap suitable for "kick the tires" demos | **Resolved** via two-part fix: (a) for the apply itself, use the serial-with-sleep recipe documented below; (b) **structurally raise MinIO's request cap with `kubectl -n risingwave set env deployment/risingwave-minio MINIO_API_REQUESTS_MAX=10000`** — this should be the standing config for any dev cluster doing real workloads, not just an incident response. Apply recipe (for future MV additions): (1) scale producer to 0, (2) apply files serially with `sleep 90` between, (3) watch RW pods for MinIO restarts and pause 30s if any, (4) rescale producer to 1 after settling. Day-7 hardening: size MinIO appropriately in `manifests/risingwave-values.yaml` (not just patched live) or move to S3 directly. `apply_ddl.sh` should grow a `--serial --sleep N` mode |
| Day 2 Pipeline | `torch.onnx.export()` failed with `ModuleNotFoundError: No module named 'onnxscript'` | PyTorch 2.3+ defaults to dynamo-based ONNX exporter which requires `onnxscript` (not in our deps) | Added `dynamo=False` to `torch.onnx.export()` in `export.py` — uses legacy TorchScript exporter, no extra deps needed |
| Day 2 Pipeline | ONNX equivalence check failed: max diff ~1.83e-04 exceeding 1e-5 threshold | Normal float32 precision loss between PyTorch and ONNX Runtime (different op fusion, different reduction order) | Relaxed `EQUIVALENCE_TOL` from `1e-5` to `1e-3` in `export.py`. 1e-3 is standard for float32 ONNX validation |
| Day 2 Pipeline | MLflow logging failed with DNS resolution error for `mlflow-tracking.mlflow.svc.cluster.local` | Code called `mlflow.set_tracking_uri()` with hardcoded in-cluster URI, overriding `MLFLOW_TRACKING_URI` env var. Hardcoded in TWO locations: `mlflow_log.py` default arg + `__main__.py` argparse default | Changed defaults in both files to `os.environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5001')`. Added `import os` to both. Port 5001 because 5000 had stale port-forwards |
| Day 3 Deploy | Decisioner pod `ImagePullBackOff` — image not in kind node cache | Docker image built but not loaded into kind cluster | `docker build -t localhost:5000/decisioner:dev services/decisioner/` + `kind load docker-image localhost:5000/decisioner:dev --name rwml-34fa` |
| Day 3 Deploy | RisingWave connection error: `risingwave-frontend.risingwave.svc.cluster.local` not found | Overlay kustomization.yaml had wrong service name; actual RW service is `risingwave`, not `risingwave-frontend` | Changed `RW_HOST=risingwave.risingwave.svc.cluster.local` in `deployments/overlays/local-kind/kustomization.yaml` |
| Day 3 Deploy | asyncpg sent `UNLISTEN *` on connection release → RisingWave returned error (unsupported statement) | asyncpg's connection reset sends `CLOSE ALL; UNLISTEN *; RESET ALL;` — none supported by RW. `statement_cache_size=0` only prevents `DEALLOCATE`; the reset still fires on every `pool.release()` | Two-part fix: (1) `statement_cache_size=0` prevents DEALLOCATE, (2) monkey-patch `asyncpg.connection.Connection.reset` to a no-op (`_noop_reset`). This is the standard pattern for asyncpg + non-Postgres wire-compatible DBs (CockroachDB, RisingWave). Applied in `feature_lookup.py` at module import time |
| Day 3 Deploy | ONNX model expected 8 features but decisioner sent 21 | Training pipeline applied ≥50% coverage filter → only 8 features survived. `feature_lookup.py` hardcodes all 21 MV columns. Serving code (scaffolded Day 3 before pipeline ran) was never updated to match training output | `ModelRegistry.warm_up()` now downloads `manifest/feature_schema.json` from MLflow (written by training pipeline) and exposes `registry.feature_cols`. Route slices the 21-col feature vector to the model's expected columns dynamically. No hardcoded feature count — future retraining with different coverage will propagate automatically |
| Day 3 Deploy | First RW query from decisioner pod times out (2s `command_timeout`) | RisingWave cold-cache after MinIO restart — first MV read has to fetch from object storage (~1.7s). Subsequent queries are 30-70ms (within SLO) | Bumped `rw_pool_acquire_timeout_s` from 2.0 to 10.0 in `config.py`. Cold-start latency is acceptable (happens once per pod start); warm p50 measured at 41ms. Day 7 k6 load test measures proper warm-state p99 |
| Day 4 Deploy | Decisioner pod crash-looped (CrashLoopBackOff) after Day 4 image rebuild | Liveness probe `initialDelaySeconds: 30` too short — MLflow model download from MinIO takes 30-40s on cold cluster. Probe fires before uvicorn binds port 8080, gets `connection refused`, kills the container | Replaced readiness/liveness `initialDelaySeconds` with a K8s `startupProbe` (10s initial + 24 failures × 5s period = 130s budget). Liveness/readiness probes now have no `initialDelaySeconds` — they only start checking after startupProbe passes. Pod self-recovered after back-off; fix prevents recurrence on future rollouts |
| Customer-attribute rollout 2026-06-29 | After DROP SOURCE CASCADE + re-apply DDL, file `02_mv_events_enriched.sql` repeatedly failed with `Hummock error: unexpected decreasing now, old=N, new=N-1`. Cascade-failed every downstream MV. Restarting RW pods didn't help — the same error fired on every re-apply, even with serial-with-sleep recipe | Snapshot backfill of accumulated Kafka history (~30 days of prior testing events) through the LAG()-based `events_enriched` MV trips a RisingWave Hummock storage bug where the internal clock can go backwards by 1 ms during the backfill replay. Customer_attributes succeeded because its CREATE prints "snapshot backfill disabled due to using shared source" — no historical replay needed | **Two-part fix:** (a) Changed `00_source_transactions.sql` `scan.startup.mode` from `'earliest'` to `'latest'` — RW starts consuming at the current Kafka offset instead of replaying history. New events flow forward-only; the Hummock bug never fires because there's no historical replay. (b) Moved the explanatory comment OUTSIDE the `WITH (...)` clause — RisingWave's parser doesn't handle multi-line `--` comments inside `WITH` options. Tradeoff: historical training data must now be re-generated via `backfill_trigger.py` (synthetic 2-7 day backfill written to Kafka *after* RW's consumer offset), instead of replaying real accumulated events. This is actually production-parity behavior (real banks have explicit backfill workflows, not "replay Kafka from offset 0"). |
| Customer-attribute rollout 2026-06-29 | `--backfill-days 7` (the prior session default) generates ~30-35M events for cohort_size=1000 and OOMKills the producer pod, blocking the training pipeline indefinitely | Per-customer event rates from `segments.py` mean ~5000 events/customer/day; 1000 customers × 7 days ≈ 35M events. Prior session log (D2-1a) shows 29M events caused pod eviction after 10+ hours | **Use `--backfill-days 2`** for dev cluster runs (`v1.1.0` shipped with 2-day backfill; ~10M events; ~15-20 min wall-clock). Long-window MVs (7d, 30d) are sparse with 2-day backfill, but the pipeline's documented ≥50% coverage fallback (per the D2 bug fixes) handles this. Scale up to `--backfill-days 7` only on AWS overlay where pod memory is configurable per the Terraform module |

---

## 8. "When X dies" — operational cheat sheet

Recovery procedures for each component, ordered from least to most destructive.

### 8.1 A single pod crashes

```bash
# Identify the offender
kubectl get pods -A | grep -v Running

# Logs from the crashed container (before the latest restart)
kubectl logs -n <ns> <pod> --previous --tail=80

# Current container logs
kubectl logs -n <ns> <pod> --tail=80
```

Most pod crashes self-recover via Kubernetes' restart policy. If
restart count keeps climbing, find the root cause in `--previous` logs.

### 8.2 Kafka broker dies

Symptom: producer/consumer clients see "connection refused" or timeouts.

```bash
# 1. Confirm: is the broker pod Running?
kubectl get pods -n kafka -l strimzi.io/component-type=kafka

# 2. If CrashLoopBackOff, check logs
kubectl logs -n kafka kafka-e11b-dual-role-0 -c kafka --previous --tail=100

# 3. Check the Kafka CR status
kubectl get kafka -n kafka
kubectl describe kafka kafka-e11b -n kafka | tail -40

# 4. Check the operator (the operator reconciles the broker)
kubectl logs -n kafka deployment/strimzi-cluster-operator --tail=60

# 5. If the broker pod is stuck Pending, check PVC
kubectl get pvc -n kafka

# Recovery options (in order):
# - delete the broker pod; StrimziPodSet recreates it
kubectl delete pod kafka-e11b-dual-role-0 -n kafka

# - or restart the entire Kafka resource
kubectl annotate kafka kafka-e11b -n kafka strimzi.io/restart-broker=true --overwrite
```

### 8.3 RisingWave frontend / compute / meta dies

Symptom: SQL queries hang or fail; behavioral_features writes error;
decisioner can't read features.

```bash
# Check status
kubectl get pods -n risingwave

# Most common — meta restarts before others; meta needs Postgres
kubectl get pod risingwave-postgresql-0 -n risingwave

# If meta is alive but compute keeps restarting, look for gRPC failures
kubectl logs -n risingwave risingwave-compute-0 --tail=80

# If frontend is the one failing, it's likely Postgres unreachable
kubectl logs -n risingwave -l app.kubernetes.io/component=frontend --tail=60

# Recovery
kubectl rollout restart statefulset risingwave-meta -n risingwave
kubectl rollout restart statefulset risingwave-compute -n risingwave
kubectl rollout restart deployment -n risingwave -l app.kubernetes.io/component=frontend
```

Materialized views and tables survive restarts (state lives in MinIO + Postgres).

### 8.4 MinIO dies

Symptom: MLflow artifact upload fails; RisingWave can't checkpoint;
no new model artifacts visible in the registry.

```bash
# Status
kubectl get pod -n risingwave -l app=minio

# Logs
kubectl logs -n risingwave -l app=minio --tail=80

# Buckets reachable?
ROOT_USER=$(kubectl -n risingwave get secret risingwave-minio -o jsonpath='{.data.root-user}' | base64 -d)
ROOT_PASS=$(kubectl -n risingwave get secret risingwave-minio -o jsonpath='{.data.root-password}' | base64 -d)
kubectl -n risingwave port-forward svc/risingwave-minio 9000:9000 &
AWS_ACCESS_KEY_ID="$ROOT_USER" AWS_SECRET_ACCESS_KEY="$ROOT_PASS" \
    aws --endpoint-url http://localhost:9000 s3 ls

# Recovery — restart MinIO
kubectl rollout restart deployment -n risingwave -l app=minio
```

If MinIO data is corrupted, the `mlflow-d971` bucket needs to be
recreated. Past model artifacts are lost; metadata in Postgres is fine
but artifact URIs will 404. This is a dev cluster — accept the loss and
restart the workflow.

### 8.5 MLflow tracking dies

Symptom: `mlflow.start_run()` connection refused; model registry
unreachable.

```bash
# Status
kubectl get pods -n mlflow
kubectl logs -n mlflow deployment/mlflow-tracking --tail=80

# Most failures are Postgres-side (backend store)
kubectl logs -n risingwave risingwave-postgresql-0 --tail=40

# Or MinIO-side (artifact store)
kubectl logs -n risingwave -l app=minio --tail=40

# Recovery — restart MLflow
kubectl rollout restart deployment/mlflow-tracking -n mlflow
kubectl -n mlflow rollout status deployment/mlflow-tracking --timeout=120s

# If the Secret is wrong (e.g. credentials drifted)
bash scripts/create-mlflow-secret.sh
kubectl rollout restart deployment/mlflow-tracking -n mlflow
```

### 8.6 Postgres backend dies

Symptom: MLflow + RisingWave both go unhealthy simultaneously.

```bash
kubectl get pod risingwave-postgresql-0 -n risingwave
kubectl logs risingwave-postgresql-0 -n risingwave --tail=80

# Recovery
kubectl delete pod risingwave-postgresql-0 -n risingwave
# StatefulSet recreates it; PV is reused, so data persists
```

If the PV is corrupted, you lose RisingWave's metadata AND MLflow's run
history. Recovery is to recreate the cluster (Section 8.8). Treat
Postgres backups as out of scope for dev.

### 8.7 Ingress-nginx dies

Symptom: Ingress-routed traffic times out. None of our services
currently use Ingress, so this rarely blocks Day-1 work.

```bash
kubectl get pods -n ingress-nginx
kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx
```

### 8.8 Whole cluster gone or unrecoverable

```bash
# Nuclear option — full rebuild
bash deployments/dev/kind/create_cluster.sh

# Then the MLflow manual steps from Section 5 step 5
```

Everything is rebuildable from code. No state worth preserving lives
only in the kind cluster — RisingWave MVs are recomputed from Kafka,
MLflow runs are dev experiments, the buckets are recreated by the
helm chart.

### 8.9 Devcontainer itself dies

```bash
# In VS Code: Cmd/Ctrl+Shift+P → "Dev Containers: Rebuild Container"
# Then re-bootstrap the cluster as in 8.8
```

The kind cluster runs inside the devcontainer's docker-in-docker. Rebuilding the devcontainer wipes the cluster. Always re-run `create_cluster.sh` after a devcontainer rebuild.

### 8.10 mise tools missing after rebuild

Symptom: `kubectl: command not found` (or any other mise-managed tool).

```bash
# Activate mise in current shell
eval "$(mise activate bash)"

# If still missing, reinstall tools
mise install

# Verify activation line is in .bashrc for future shells
grep mise ~/.bashrc || echo 'eval "$(mise activate bash)"' >> ~/.bashrc
```

---

## 9. References

### ADRs
- **001** — Quixstreams over Kafka Streams / Flink
- **002** — RisingWave as feature store (no Feast)
- **003** — Metaflow on Kubernetes (not AWS Batch)
- **004** — *Superseded by 008* — Monolithic decisioner (architectural decision retained; language pivoted)
- **005** — MLflow with `--serve-artifacts` proxy (motivates `mlflow-final.yaml`)
- **006** — Kustomize base + overlays (motivates the `deployments/base/` + `overlays/` split)
- **007** — Crypto-domain code split (retained `news` + `news-sentiment` for macro signal)
- **008** — Python FastAPI decisioner (supersedes 004)

### Runbooks
- `docs/runbooks/retraining.md` — when + how to retrain a model
- `docs/runbooks/rollback.md` — champion-challenger alias rollback
- `docs/runbooks/drift_response.md` — what to do when drift fires
- `docs/runbooks/oncall.md` — first-response checklist

### Operational logs
- `docs/day0_log.md` — chronological action log of Day 0
- `docs/incidents.md` — post-mortems

### Architecture
- `docs/05_architecture.md` — the three-plane decomposition
- `docs/06_production_patterns.md` — interview-ready production patterns
- `docs/repo_layout.md` — directory conventions
- `docs/architecture_diagrams.md` — five canonical diagrams (D1 four-plane, D2 single decision call graph, D3 retraining loop, D4 substrate + AWS overlay, D5 cross-plane invariants)
