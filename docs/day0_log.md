# Day 0 — Change Log

Running record of every change made during Day 0 infra hardening.
Append-only; oldest entries at the top.

---

## 2026-06-03 — Session 1 — Three approved actions

Scope: user-approved actions only — delete `telecom` sandbox, rotate MinIO credentials, log everything.
Broader Day 0 work (Kustomize base/overlays restructure, workspace scaffolding for new
finance services, design doc scaffold, devcontainer additions, Terraform skeleton) is
**not** done in this session — awaiting user confirmation before proceeding.

### Action 1 — Deleted `services/telecom/` sandbox

- **What**: Recursive delete of `services/telecom/` directory (Python sandbox containing
  `test_mlflow.py`, `simulator_server.py`, `websocket_api.py`, etc.).
- **Why**: Abandoned experiment with hardcoded credentials (see Action 2). No external
  code referenced it — verified via `grep telecom` (only matches were inside the directory
  itself, plus `pyproject.toml`/`uv.lock` which referenced a phantom `services/telemetry`
  entry that didn't exist on disk).
- **Files touched**: removed entire `services/telecom/` subtree.
- **Side effect**: `pyproject.toml` workspace member entry `"services/telemetry"` removed
  (it pointed to a directory that didn't exist; was already a phantom).
- **Risk**: low. Nothing else imported from this package; the crypto pipeline never
  touched it.

### Action 2 — Rotated MinIO / S3 credentials

- **What**: Generated cryptographically random replacement credentials for the MinIO
  artifact store used by MLflow. Replaced the old credentials in three locations and
  restructured the secret handling so the credential never lives in a committed file again.
- **Why**: The old credentials (`DXNQpQ5ncbHUobaRa4s1` / `WpVvuikA2Dpym7lW15wVSELKcuwiTHEz0VX1EFwd`)
  appeared verbatim in three places: a Python file (`services/telecom/test_mlflow.py`),
  the MLflow K8s Deployment manifest (`mlflow-final.yaml`), and the matching K8s Secret
  manifest (`mlflow-minio-secret.yaml`). If this repo is ever pushed publicly, the keys
  are compromised. Rotation + structural changes prevent recurrence.
- **New credential generation**: PowerShell with `RNGCryptoServiceProvider`
  (cryptographically secure). 20-char access key + 40-char secret key.
- **Files touched**:
  1. `deployments/dev/kind/manifests/mlflow-minio-secret.yaml` — replaced old keys with
     new ones, added explanatory comment, kept the file for local-dev use only.
  2. `deployments/dev/kind/manifests/mlflow-minio-secret.yaml.example` — NEW. Committed
     placeholder template with `REPLACE_WITH_REAL_*` values for repo viewers to see the
     structure without seeing real keys.
  3. `deployments/dev/kind/manifests/mlflow-final.yaml` — replaced the two hardcoded
     `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` `value:` env vars with
     `valueFrom: secretKeyRef:` pointing at the existing `mlflow-minio-secret` Secret.
     The Deployment now reads creds from the Secret rather than embedding them.
  4. `.env.local` — appended `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
     `MLFLOW_S3_ENDPOINT_URL`, `MLFLOW_S3_IGNORE_TLS`. This is the single source of truth
     for the new credentials on disk; the file is already gitignored (line 221 of `.gitignore`).
  5. `scripts/create-mlflow-secret.sh` — NEW. Idempotent shell script that sources
     `.env.local` and applies the Secret to the cluster via `kubectl create secret
     ... --dry-run=client -o yaml | kubectl apply -f -`. This replaces the workflow of
     editing the YAML file by hand.
  6. `.gitignore` — added patterns to ignore `deployments/**/mlflow-minio-secret.yaml`
     and any `*-secret.yaml` file going forward, with a re-include rule for the
     `*.example` templates.

- **What you (the user) need to do in the cluster**:
  ```bash
  # 1. Apply the new secret from .env.local
  bash scripts/create-mlflow-secret.sh

  # 2. Restart the MLflow tracking pod so it picks up the new env vars
  kubectl -n mlflow rollout restart deployment/mlflow-tracking

  # 3. Sanity-check: tracking server should come up healthy and serve artifacts
  kubectl -n mlflow get pods
  ```

- **Old credential handling**: the OLD credentials are now invalid as soon as you apply
  the new Secret AND restart MLflow. If you have any other pods or scripts that
  hardcoded the old keys, they'll start failing — easiest fix is grep for the old access
  key (`DXNQpQ5ncbHUobaRa4s1`) across the repo and replace with env-var reads.

- **Note for AWS deployment**: this rotation pattern applies to MinIO (local-kind only).
  On AWS, this Secret goes away entirely — pods will use IAM Roles for Service Accounts
  (IRSA) to access S3 with no embedded credentials at all. The AWS overlay (Day 8) will
  document that swap.

### Action 3 — Wrote this log

- **What**: Created `docs/day0_log.md` (this file).
- **Why**: User asked for a record of what was changed.

---

---

## 2026-06-03 — Session 2 — ADRs, justfile, MLflow image bump

Scope: high-signal scaffolding using conventions absorbed from
`C:/Users/abhin/projects/project_01` (battery-pdm). Adopts the Nygard-format
ADRs, the numbered-chapter docs layout (will populate progressively), the
`justfile`-as-task-runner convention, and `env.shared` for non-secret env.

### Action 4 — Wrote five Architecture Decision Records

- **What**: Created `docs/decisions/` with an index `README.md` and five
  substantive ADRs covering the architectural calls already made in design
  discussions. Each ADR follows the Michael Nygard format used in
  `project_01`: Status / Date / Decision makers / Context / Decision /
  Consequences (Positive + Negative) / Alternatives considered / Related.
- **Why**: ADRs are the single highest-signal artifact in a senior portfolio
  repo. They demonstrate reasoning, not just shipping. They also pre-empt
  the most common architecture interview questions ("why X over Y?") with
  written, defensible answers.
- **Files created**:
  - `docs/decisions/README.md` — index with status column
  - `docs/decisions/001-quixstreams-over-kafka-streams.md`
  - `docs/decisions/002-risingwave-as-feature-store-not-feast.md`
  - `docs/decisions/003-metaflow-kubernetes-not-batch.md`
  - `docs/decisions/005-mlflow-artifact-proxy-not-direct-s3.md` (documents
    the actual debugging journey from the Bitnami chart → custom Deployment)
  - `docs/decisions/006-base-overlays-kustomize-not-helm.md`
- ADRs 004, 007–010 are reserved as placeholders in the index for upcoming
  decisions (champion-challenger shape, bandit-on-uplift composition,
  off-policy evaluation gate, Terraform-not-CDK). They are written when the
  corresponding work is done, not preemptively.

### Action 5 — Wrote `justfile` task runner

- **What**: Created `justfile` at the repo root with task targets grouped
  into: lint/format/typecheck, tests (including `test-crypto-smoke` as the
  Day 0 regression net), local-service dev, Docker build, kind cluster up/down,
  Kustomize overlay apply/diff/validate, MLflow ops (`mlflow-secret`,
  `mlflow-restart`, `mlflow-ui`), Terraform (`tf-init`/`plan`/`apply`/`destroy`),
  k6 load testing, Metaflow flow targets (local / `@kubernetes` / `@batch`),
  docs serve.
- **Why**: Standardize task invocation across environments and contributors.
  Matches `project_01`'s pattern. Single place for "how do I do X" lookup.
- **Files created**: `justfile`
- **Note**: the existing `Makefile` is kept (used by the crypto pipeline);
  `justfile` is the new authoritative task runner for finance-domain and
  cross-cutting tasks. They will coexist during the transition.

### Action 6 — Created `env.shared` for non-secret shared env

- **What**: Created `env.shared` at the repo root with project name,
  namespace constants, service hostnames/ports (RisingWave, Kafka, MLflow),
  AWS region and EKS cluster name defaults. Designed to be auto-sourced
  via `direnv` inside the devcontainer or manually via `source env.shared`.
- **Why**: Keeps non-secret env in one place; complements `.env.local`
  (which holds secrets and is gitignored).
- **Files created**: `env.shared`

### Action 7 — Bumped MLflow tracking server image v2.11.3 → v2.22.0

- **What**: Changed the image tag in
  `deployments/dev/kind/manifests/mlflow-final.yaml` from
  `ghcr.io/mlflow/mlflow:v2.11.3` to `:v2.22.0`.
- **Why**: The Python client pinned in `pyproject.toml` is `mlflow==2.22.0`.
  Major-version skew between client (2.22) and server (2.11) is a known
  source of subtle failures — model registry alias semantics, `log_input`,
  signature handling, and `log_table` all changed across the 2.11→2.22 range.
  Aligning client and server eliminates a class of confusing bugs and
  removes the most likely root cause of the prior MinIO-auth debugging.
- **Files touched**: `deployments/dev/kind/manifests/mlflow-final.yaml`
- **What you need to do**: after applying the new manifest with
  `kubectl apply -f deployments/dev/kind/manifests/mlflow-final.yaml`,
  the new image will be pulled and rolled out. Confirm with
  `kubectl -n mlflow rollout status deployment/mlflow-tracking`.
- **Backward compatibility**: experiments and registered models from 2.11
  are forward-compatible with 2.22 (MLflow has a strong backward-compat
  story across 2.x). Verified against the MLflow 2.22 release notes.

### Action 8 — Updated this log

- **What**: Appended Session 2 entries.

---

## 2026-06-03 — Session 3 — Repo rename + monolithic-decisioner ADR

### Action 9 — Renamed repo, cleaned up nested clones

- **What**: Flattened the nested directory structure and renamed the repo
  to remove the cohort-suggestive name.
- **Why**: The repo was a double-nested clone — outer dir
  `C:\Users\abhin\real-time-ml-system-cohort-4\` contained both its own
  `.git/` and an inner working tree `real-time-ml-system-cohort-4/` which
  was itself a separate clone with its own `.git/`. Both clones at the
  same HEAD (`5d09345`). The top-level name leaked the course origin.
- **What was done**:
  1. Verified both `.git/` repos held identical history
     (same HEAD, same `git log --oneline -5`)
  2. Moved every non-`.git` item from the inner working tree up to the
     outer git root (preserves the outer `.git/` as the authoritative
     repo; effectively deletes the duplicate)
  3. Backed up the orphan inner `.git/` as
     `C:\Users\abhin\orphan_inner_git_backup.zip` (2.7 MB) — keep for ~1
     week as a safety net, delete when confirmed nothing was missed
  4. Removed the orphan inner `.git/` and the now-empty inner dir
  5. Renamed `C:\Users\abhin\real-time-ml-system-cohort-4\` →
     `C:\Users\abhin\realtime-credit-decisioning\`
- **Files touched**: every file in the working tree (path change only,
  contents unchanged); the outer `.git/` index now reflects pre-move
  state and shows the file moves as deletions + untracked additions
  until `git add -A` is run
- **What you (the user) need to do**:
  1. **Verify your IDE / VS Code workspace** is reopened from
     `C:\Users\abhin\realtime-credit-decisioning\` — old bookmarks will
     fail
  2. **`uv sync`** to reconcile the lockfile against the (already-cleaned)
     workspace member list
  3. **`git status`** then `git add -A && git commit -m "Restructure:
     flatten nested working tree"` to commit the move (git's rename
     detection should pick up most files as renames)
  4. **`kubectl config view`** to check the kubeconfig path env var —
     if you set `KUBECONFIG=C:\Users\abhin\real-time-ml-system-cohort-4\...`
     anywhere, update to the new path
  5. **Rebuild the devcontainer** so the path change in
     `.devcontainer/devcontainer.json` takes effect
- **Risk**: low; reversible. The orphan `.git` backup zip exists for
  recovery. The outer `.git/` carries the full history. No git history
  was rewritten.

### Action 10 — Devcontainer path fix

- **What**: Updated `.devcontainer/devcontainer.json` line 74 from
  `/workspaces/real-time-ml-system-cohort-4/.venv/bin/python` to
  `/workspaces/realtime-credit-decisioning/.venv/bin/python`.
- **Why**: VS Code maps the host workspace folder to
  `/workspaces/<folder-name>/` inside the devcontainer. After the rename,
  the old path no longer exists inside the container.

### Action 11 — Wrote ADR 004: monolithic decisioner on the request path

- **What**: Wrote a substantive ADR
  (`docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md`)
  answering the senior architecture question raised in conversation:
  *"is the per-topic-microservice pattern correct for our domain?"*
- **Why**: The answer is *yes for streaming and batch planes, no for the
  synchronous decision plane.* The ADR documents the latency-budget
  reasoning, the three-plane decomposition, alternatives considered
  (including Pau's-pattern-applied-uniformly, which we rejected on the
  latency budget), and the consequences (positive + negative) of the
  chosen decomposition.
- **Files touched**:
  - NEW: `docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md`
  - `docs/decisions/README.md` — added ADR 004 to the index with
    Accepted status, removed it from the "placeholders" list
- **Implication for the build plan**: the original "8 finance services"
  list collapses to **5 services** (`transactions`,
  `behavioral_features`, `decisioner`, `drift_monitor`,
  `retraining_flow`, plus optional `outcome_collector`). The collapsed
  `decisioner` is a single Rust process loading per-segment NN models
  via ONNX Runtime in-process.

### Action 12 — Updated this log

- **What**: Appended Session 3 entries; refreshed the "still pending"
  list below to reflect what was completed in Sessions 2 and 3.

---

## 2026-06-03 — Session 4 — Repo layout doc + Kustomize scaffold + workspace expansion

### Action 13 — Wrote `docs/repo_layout.md`

- **What**: Comprehensive meta-document explaining the entire repo layout
  — every top-level directory, the conventions, the provenance of each
  convention (project_01 vs uv vs Kustomize docs vs original), the
  trade-offs considered, and the layout invariants that should not be
  broken in PR review.
- **Why**: Senior interviewers ask "why is your repo structured this
  way?" — `docs/repo_layout.md` is the answer in one place. Also serves
  as onboarding for any future contributor (or future-self) without
  requiring archaeology of the design conversation.
- **Files touched**: NEW `docs/repo_layout.md`
- **Sections covered**: top-level layout, `services/` (Python +
  Rust shapes), `deployments/` (base+overlays per ADR 006), `infra/`
  (Terraform + Python lib coexistence), `docs/` (chapter numbering +
  ADRs + cards + runbooks), `.github/`, what is intentionally NOT in
  the repo, the six layout invariants, and a provenance table mapping
  each convention to its source.

### Action 14 — Scaffolded `deployments/base/` and `deployments/overlays/`

- **What**: Created the cloud-agnostic Kustomize base + per-environment
  overlay skeletons per ADR 006.
- **Files created**:
  - `deployments/base/README.md`
  - `deployments/base/kustomization.yaml` (resource list commented out;
    services added as each migrates from `dev/kind/`)
  - `deployments/overlays/local-kind/README.md`
  - `deployments/overlays/local-kind/kustomization.yaml` (image map
    for `localhost:5000/<svc>:dev`, replica=1 patch, ConfigMap with
    MinIO + Kafka + MLflow + RisingWave endpoints)
  - `deployments/overlays/aws-eks/README.md`
  - `deployments/overlays/aws-eks/kustomization.yaml` (ECR image map
    with `AWS_ACCOUNT_ID`/`AWS_REGION` placeholders, replica=3 patch,
    IRSA patch for MLflow Deployment, ConfigMap pointing at S3 + RDS)
  - `deployments/overlays/on-prem/README.md` (placeholder)
- **Migration policy**: legacy `deployments/dev/kind/manifests/` remains
  the authoritative source until each crypto manifest is touched; we
  migrate manifest-by-manifest to avoid a big-bang rewrite that could
  break the running crypto pipeline.

### Action 15 — Workspace expansion: added 5 finance-domain Python services to `pyproject.toml`

- **What**: Added `transactions`, `behavioral_features`, `drift_monitor`,
  `retraining_flow`, `outcome_collector` to the `[tool.uv.workspace].members`
  list in the root `pyproject.toml`. Comment headers separate the
  crypto and finance pipelines for clarity.
- **Note**: `decisioner` is NOT in this list — it's a Rust crate (Cargo
  workspace member, not uv). It will be added to the root `Cargo.toml`
  workspace in a follow-up action when its Cargo.toml is written.
- **What you (the user) need to do**: `uv sync` once the per-service
  `pyproject.toml` files are created (next batch) to install the new
  workspace members.

### Action 16 — Updated this log

- **What**: Appended Session 4 entries.

---

## 2026-06-03 — Session 5 — Service skeletons (Python + Rust)

### Action 17 — Created 5 Python finance-service skeletons

- **What**: Each service has `pyproject.toml`, `README.md`,
  `src/<name>/__init__.py`, and `src/<name>/main.py` (logger-only stub
  with a TODO marker for the day it implements).
- **Services**: `transactions` (Day 1), `behavioral_features` (Day 1),
  `drift_monitor` (Day 5), `retraining_flow` (Day 5), `outcome_collector`
  (Day 6).
- **Dependency choices** in each `pyproject.toml`:
  - All Python services: `loguru`, `pydantic`, `pydantic-settings`
  - Streaming services (`transactions`, `behavioral_features`,
    `drift_monitor`, `outcome_collector`): `quixstreams>=3.13.1`
  - `behavioral_features` + `outcome_collector`: `risingwave-py` for
    RisingWave-as-feature-store writes/reads
  - `drift_monitor`: `evidently<0.5`, `river>=0.21`, `scipy`, `numpy`
    for PSI/KS/ADWIN detectors
  - `retraining_flow`: `metaflow>=2.12`, `mlflow==2.22.0` (matches
    server bump from Action 7), `optuna`, `scikit-learn`, `torch`,
    `onnx`, `onnxruntime` — supports the fan-out training + ONNX
    export for Rust serving per ADR 004
- **All Python service main.py files** log a "skeleton; Day N will
  implement X" line so they're runnable from Day 0 (won't crash, just
  log and exit). This lets us add them to the smoke test before any
  real implementation lands.

### Action 18 — Created Rust `decisioner` skeleton

- **What**: Created `services/decisioner/` as a standalone Rust crate
  (NOT yet a root-workspace member — deliberately deferred to Day 2 so
  Day 0 doesn't require touching the root `Cargo.toml`).
- **Files**:
  - `services/decisioner/Cargo.toml` — lists every dependency the
    request-path implementation will need: `axum`, `tokio`, `serde`,
    `sqlx` (Postgres for RisingWave lookups), `ort` (ONNX Runtime per
    ADR 004), `ndarray`, `rdkafka` (audit-log queue), `tracing`,
    `anyhow`, `thiserror`
  - `services/decisioner/README.md` — purpose, SLO target, why Rust
    (links ADR 004), Day-by-Day implementation plan
  - `services/decisioner/src/main.rs` — compiles, binds to
    `DECISIONER_PORT` (default 3000), serves `/health` → `ok`. TODO
    markers for Days 2–4.

### Action 19 — Updated this log

- **What**: Appended Session 5 entries.

---

## 2026-06-03 — Session 6 — infra/lib + infra/terraform skeletons

### Action 20 — Created `infra/lib/` Python abstractions

- **What**: New uv-workspace member package `infra_lib` at `infra/lib/`
  with five thin abstraction modules. Application services will import
  from `infra_lib.*` instead of touching boto3 / cloud SDKs directly.
- **Layout invariant enforced**: per
  `docs/repo_layout.md` invariant #2 — "cloud touch happens only inside
  `infra/`". Direct `import boto3` in a service is now flagged as an
  anti-pattern; if it happens, `infra_lib` should be extended instead.
- **Files created**:
  - `infra/lib/pyproject.toml` (uv workspace member, name `infra_lib`)
  - `infra/lib/src/infra_lib/__init__.py`
  - `infra/lib/src/infra_lib/object_store.py` — boto3 S3 client with
    configurable endpoint (MinIO locally / AWS S3 / GCS S3-interop;
    `OBJECT_STORE_ENDPOINT` env var switches)
  - `infra/lib/src/infra_lib/feature_store_client.py` — RisingWave
    connection-config helper + DSN builder (per ADR 002)
  - `infra/lib/src/infra_lib/workflow_trigger.py` — workflow trigger
    abstraction; backends Argo Events (default) and AWS EventBridge
    (AWS overlay); raises `NotImplementedError` until Day 5
  - `infra/lib/src/infra_lib/secret_provider.py` — secret abstraction;
    backends env (default), AWS Secrets Manager, Vault; planned Day 8
  - `infra/lib/src/infra_lib/trace.py` — OpenTelemetry setup stub;
    Day 7 wires the real OTLP exporter
- **Workspace member added**: `infra/lib` appended to
  `[tool.uv.workspace].members` in root `pyproject.toml`. Comment
  separates it from the service-pipeline members.

### Action 21 — Created `infra/terraform/` skeleton

- **What**: Root-module Terraform skeleton matching the
  `project_01/infra/` convention, plus per-AWS-concern module
  placeholders. Skeleton files compile (`terraform init` works); the
  per-module `.tf` files are deferred to Day 8 when AWS overlay work
  begins.
- **Files created**:
  - `infra/terraform/main.tf` — root module wiring (all module calls
    commented out; uncomment as each is implemented Day 8)
  - `infra/terraform/variables.tf` — `aws_region`, `cluster_name`,
    `cluster_version`, `environment`, `tags`
  - `infra/terraform/versions.tf` — `>= 1.7`, AWS `~> 5.50`,
    Kubernetes `~> 2.30`, Helm `~> 2.13`; AWS provider with default tags
  - `infra/terraform/terraform.tfvars.example` — copy-to-tfvars template
  - `infra/terraform/README.md` — what it creates, status,
    usage walkthrough, cost note (EKS ~$73/mo control plane + nodes),
    Terraform-vs-CDK rationale stub (full ADR 010 planned for Day 8)
  - Per-module READMEs documenting planned inputs / outputs / Day 8
    implementation plan:
    - `infra/terraform/modules/vpc/README.md` — community
      `terraform-aws-modules/vpc/aws` plan
    - `infra/terraform/modules/eks_cluster/README.md` — community
      `terraform-aws-modules/eks/aws` plan, IRSA-on, ALB Controller
      + EBS CSI + VPC CNI addons
    - `infra/terraform/modules/ecr/README.md` — per-service repos,
      lifecycle policy keeping last 30 + 7 days untagged
    - `infra/terraform/modules/s3/README.md` — MLflow artifacts +
      decision log buckets, SSE-S3, public-access-block, lifecycle
    - `infra/terraform/modules/iam_irsa/README.md` — IRSA role per
      ServiceAccount; outputs role ARNs for overlay annotations
    - `infra/terraform/modules/mlflow_server/README.md` —
      EC2-MLflow OPTIONAL module (default MLflow stays in-cluster
      per ADR 005)
- **Split documented**: `infra/terraform/` provisions *infrastructure*
  (cluster, ECR, S3, IAM); `deployments/overlays/aws-eks/` deploys
  *workloads* into that infrastructure. The boundary is intentional and
  matches ADR 006.

### Action 22 — Updated this log

- **What**: Appended Session 6 entries.

---

## 2026-06-03 — Session 7 — .github + pre-commit + legacy move

### Action 23 — Wrote `.github/` scaffolding

- **What**: Mirrored project_01's `.github/` layout.
- **Files**:
  - `.github/CODEOWNERS` — auto-request reviews on
    `docs/decisions/` + `infra/` + secret-handling paths
  - `.github/pull_request_template.md` — PR checklist with the eight
    invariants (lint/typecheck/tests, ADR-needed, no secrets, no
    hardcoded paths, infra cost/risk)
  - `.github/BRANCH_PROTECTION.md` — documents the GitHub branch
    protection settings for `main` in markdown (not buried in the
    GitHub UI), with a `gh api` snippet for replaying
  - `.github/workflows/ci.yml` — five jobs: `lint` (ruff check + format
    check), `typecheck` (mypy --strict over `services/` + `infra/lib/`),
    `test` (pytest), `rust-check` (cargo check + clippy + fmt for
    decisioner), `kustomize-validate` (renders both overlays through
    kubeval)
- **Why each CI job**: enforces the layout invariants from
  `docs/repo_layout.md` mechanically. A reviewer can rely on green CI
  as proof that the code-hygiene rules from memory (`uv` only,
  `mypy --strict`, ruff line 100, pytest ≥75%, seeds) are not
  aspirational.

### Action 24 — Wrote `.pre-commit-config.yaml`

- **What**: Local hooks run on every commit:
  - `ruff check --fix` + `ruff format` for Python
  - `mypy --strict` scoped to `services/` and `infra/lib/`
  - `cargo fmt --check` for the decisioner Rust crate
  - Cross-cutting: trailing whitespace, EOF newline, YAML / TOML
    validity, merge-conflict markers, large-file guard (500KB cap)
  - `detect-secrets` with a baseline file — catches accidentally
    committed credentials (defense-in-depth alongside the
    `mlflow-minio-secret.yaml` gitignore from Session 1)
- **How to enable**: `uv run pre-commit install` once after cloning.

### Action 25 — Moved Bitnami MLflow Helm values to `deployments/legacy/`

- **What**: Moved `deployments/dev/kind/manifests/mlflow-values.yaml` →
  `deployments/legacy/mlflow-values-bitnami.yaml`. Wrote
  `deployments/legacy/README.md` documenting the move with the ADR 005
  reference and a policy statement: when a manifest is replaced, move
  it here with a 1-line reason rather than deleting it — the historical
  artifact is interview-defensible signal.
- **Why**: The Bitnami chart's direct-S3 artifact path was the
  bug-prone setup that motivated the custom `mlflow-final.yaml`
  Deployment (ADR 005). Keeping `mlflow-values.yaml` in the active
  manifests directory invited confusion about which was authoritative;
  moving it to `legacy/` makes the answer obvious.

### Action 26 — Updated this log

- **What**: Appended Session 7 entries.

---

## 2026-06-03 — Session 8 — Chapter docs + cards + runbooks (Day 0 close)

### Action 27 — Wrote eight numbered chapter docs

- **What**: `01_problem_and_domain.md` through `08_realtime_vs_batch.md`,
  mirroring the `project_01/docs/0[1-8]_*.md` chapter convention.
- **Content shape**: substantive intro paragraph + fixed section
  structure in each. Concept chapters (01, 03, 05, 06, 08) have current
  content; measurement chapters (02, 04, 07) have scaffold tables with
  TODO markers that fill in as each day's work produces numbers.
- **Cross-references**: every chapter links the relevant ADRs and
  source-of-truth docs by path so updates don't fork.

### Action 28 — Wrote `architecture_diagrams.md` with five canonical diagrams

- **What**: D1 four-plane overview, D2 single decision call graph,
  D3 drift-triggered retraining loop, D4 cloud-agnostic substrate +
  AWS overlay, D5 cross-plane communication invariants. All ASCII;
  referenced from `docs/05_architecture.md`.

### Action 29 — Wrote `data_card.md` and `model_card.md`

- **What**: `data_card.md` follows the Google Data Cards Playbook;
  documents the synthetic-transaction schema, intended use, ECOA
  feature exclusion, generation process. `model_card.md` follows
  Mitchell et al. 2019, template per registered uplift model; placeholders
  populated as Day 2 trains the first six segment models.

### Action 30 — Wrote `incidents.md` with two Day-0 incidents

- **What**: Operational incidents log. Two recorded so far: MinIO
  credential hardcoding (Session 1) and nested duplicate git repos
  (Session 3). Both with severity / detection / root cause /
  resolution / prevention. Template appended for future incidents.

### Action 31 — Wrote `tour.md` (10-minute interview demo script)

- **What**: Scene-by-scene script with target times, screen content,
  talk-tracks, and per-scene fallbacks. Memorize-before-interview
  document.

### Action 32 — Wrote `project_book.md` (narrative overview)

- **What**: The "blog post" version. Readable top-to-bottom by a
  non-engineer. Covers what / why / how / what's hard / what's out of
  scope / what comes next.

### Action 33 — Wrote four runbooks (`runbooks/`)

- **What**: `retraining.md`, `rollback.md`, `drift_response.md`,
  `oncall.md`. Each is the operational how-to for the corresponding
  system behavior, with diagnostic checklists for common failure modes
  and explicit "what NOT to do" sections.

### Action 34 — Updated this log

- **What**: Appended Session 8 entries.

---

## Day 0 — final status

| Day 0 line item | Status |
|------------------|--------|
| Tag crypto baseline (`v0.1.0-crypto-baseline`) | Deferred to user — requires `git tag` after the post-rename `git add -A && git commit` |
| Smoke test target wired in justfile | Done (`just test-crypto-smoke`) |
| Smoke test SCRIPT (`scripts/smoke_test_crypto.sh`) | Deferred to start of Day 1 alongside synthetic generator |
| MLflow server image bump v2.11.3 → v2.22.0 | Done (Session 2) |
| Move `mlflow-values.yaml` to `legacy/` | Done (Session 7) |
| Cloud-agnostic restructure — `base/` + `overlays/{local-kind,aws-eks,on-prem}/` | Done (Session 4) |
| Workspace expansion — 5 Python finance services + Rust decisioner | Done (Sessions 4 + 5) |
| `infra/lib/` Python abstractions (object_store, feature_store_client, workflow_trigger, secret_provider, trace) | Done (Session 6) |
| `infra/terraform/` skeleton with per-module READMEs | Done (Session 6) |
| Devcontainer path fix (post-rename) | Done (Session 3) |
| Devcontainer tool additions (kubectl, kustomize, helm, awscli, terraform CLI, k6, Rust) | Deferred to start of Day 1 — easier to add when first needed |
| Docs scaffold — 8 chapter docs, cards, incidents, tour, project_book, architecture_diagrams | Done (Session 8) |
| Runbooks — retraining, rollback, drift_response, oncall | Done (Session 8) |
| Pre-commit config merged (ruff + mypy --strict + cargo fmt + cross-cutting + detect-secrets) | Done (Session 7) |
| `.github/` scaffold — CODEOWNERS, PR template, BRANCH_PROTECTION, workflows/ci.yml | Done (Session 7) |
| ADR 004 — monolithic decisioner | Done (Session 3) |
| ADR 005, 006 (existing) | Done (Session 2) |
| ADRs 001, 002, 003 | Done (Session 2) |

**Day 0 is functionally complete.** Two trivial items deferred:
the smoke-test shell script and the devcontainer tool additions.
Both naturally land at the start of Day 1.

**What you (the user) need to do before Day 1** (updated after Session 9):

1. `cd C:\Users\abhin\realtime-credit-decisioning`
2. `git add -A && git commit -m "Day 0: infra hardening, repo restructure, crypto split (ADR 007)"`
3. `uv sync` (reconcile lockfile against the slimmed workspace)
4. Rebuild your devcontainer so the path change takes effect
5. `bash scripts/create-mlflow-secret.sh` then
   `kubectl -n mlflow rollout restart deployment/mlflow-tracking` to
   apply the credential rotation + MLflow image bump in your cluster

The `v0.1.0-crypto-baseline` tag is no longer relevant — crypto code is
out per ADR 007.

---

## 2026-06-04 — Session 9 — Crypto-domain split (ADR 007) and archive

### Action 35 — Cloned cohort-4 archive

- **What**: Cloned this repo's git history into a sibling directory
  `C:\Users\abhin\realtime-ml-cohort-4-archive\`, then checked out a
  new `cohort-4` branch at the pre-Day-0 commit `5d09345`.
- **Result**: archive contains the session-1 seed (the `trades`
  service and `lessons/` directory) plus the early kafka-setup commits.
  It does NOT contain later-cohort work that was never `git add`-ed in
  the original repo. See `docs/incidents.md` 2026-06-04 entry.

### Action 36 — Physically removed crypto-domain code from this repo

- **What**: `Remove-Item -Recurse -Force` on the crypto services,
  vendored ta-lib, lessons, crypto Grafana dashboard, crypto Dockerfiles,
  and crypto deployment manifest directories.
- **Removed**:
  - `services/{trades,candles,technical_indicators,predictor,prediction-api}/`
  - `ta-lib/`, `ta-lib-0.4.0-src.tar.gz`
  - `lessons/`, `dashboards/candles.json`
  - `deployments/dev/{trades,candles,technical-indicators,prediction-api,prediction-generator,training-pipeline,backfill-technical-indicators}/`
  - `Docker/{trades,candles,technical_indicators,technical_indicators_1stage,prediction-api,prediction-generator,training-pipeline}.DockerFile`
  - `mlruns/`, `state/` (already gitignored; physical clean for disk hygiene)
- **Retained**:
  - `services/news/`, `services/news-sentiment/` — feed the
    `macro_sentiment_1h` feature (ADR 002, ADR 007)
  - `deployments/dev/kind/` (shared cluster infra), `deployments/dev/news-ingestor/`
  - `Docker/news-ingestor.DockerFile`
  - `scripts/build-and-push-image.sh`, `scripts/deploy.sh`

### Action 37 — Updated `pyproject.toml`

- Renamed `[project].name` from `crypto-predictor-system` to
  `realtime-credit-decisioning-platform`
- Removed `candles`, `trades`, `predictor`, `technical-indicators`
  references from `[project].dependencies` and `[tool.uv.sources]`
- Workspace members trimmed to the finance services + `infra/lib`,
  plus retained `news` and `news-sentiment`
- Added `[tool.ruff].extend-exclude` and `[tool.mypy].exclude` for
  generated BAML code at `**/baml_client`

### Action 38 — Updated root `Cargo.toml`

- Removed phantom `services/prediction-api` workspace member
- Added `services/decisioner` as the lone Rust workspace member
- Added `resolver = "2"` (required for the new resolver behavior)

### Action 39 — Updated `justfile`

- Removed `test-crypto-smoke` target — there's no crypto pipeline to
  smoke-test anymore
- Replaced `docker-build-all` crypto service list with the finance
  service list (plus retained `news-ingestor`)
- Updated `dev service=...` example from `trades` to `transactions`

### Action 40 — Wrote ADR 007

- `docs/decisions/007-crypto-split-archive-retain-sentiment.md` documents
  the decision: split rationale, what stays vs goes vs why, archive
  location, known debt around the `coin`-keyed BAML schema, and the
  alternatives rejected (lint-exclude, full delete, git submodule,
  legacy/ subdir)
- Index updated: ADR 007 now Accepted in `docs/decisions/README.md`

### Action 41 — Updated `docs/repo_layout.md` and `docs/incidents.md`

- `repo_layout.md` services/ inventory now reflects finance-only +
  retained sentiment pattern; the "Why mix CRYPTO and FINANCE" section
  rewritten as "Why news + news-sentiment are retained" with the
  archive-pointer footnote
- `incidents.md` gained a Medium-severity entry for the lost
  uncommitted cohort work (2026-06-04), with the
  "no destructive op without `git status` first" rule added under
  Prevention

### Action 42 — Updated this log

- **What**: Appended Session 9 entries; superseded the pre-Session-9
  "before Day 1" checklist above with the updated version that drops
  the now-irrelevant `v0.1.0-crypto-baseline` tag step
