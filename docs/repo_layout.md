# Repository Layout — Conventions and Reasoning

This document explains *how* the repo is organized and *why* — the
conventions, their provenance, and the trade-offs that were considered.
If you're trying to figure out where something belongs, start here.

Layout decisions that warrant their own ADRs (cloud-agnostic Kustomize
overlays, monolithic decisioner, RisingWave-as-feature-store, etc.) link
to those ADRs rather than duplicate the reasoning.

---

## Top-level layout

```
realtime-credit-decisioning/
├── README.md                  # 30-second pitch + quick start
├── justfile                   # Task runner (see "Why just?" below)
├── Makefile                   # Legacy — kept for crypto pipeline compatibility
├── env.shared                 # Non-secret env vars sourced by direnv
├── .env.local                 # Secrets — gitignored
├── pyproject.toml             # uv workspace root
├── Cargo.toml + Cargo.lock    # Rust workspace root (decisioner, prediction-api)
├── mise.toml                  # Tool version pins (uv, ruff, etc.)
├── .devcontainer/             # VS Code devcontainer (cloud-agnostic dev env)
├── .github/                   # CODEOWNERS, PR template, CI workflows
├── .gitignore
├── .pre-commit-config.yaml    # ruff + mypy --strict on commit
├── docs/                      # All documentation (see "docs/ convention")
├── services/                  # Application services (both planes)
├── deployments/               # K8s manifests (base + overlays)
├── infra/                     # IaC + thin Python abstractions for cloud touch
├── scripts/                   # Shell scripts (smoke tests, secret rotation, etc.)
├── lessons/                   # Course notes from the streaming substrate (kept for reference)
├── dashboards/                # Grafana JSON exports
├── mlruns/                    # MLflow local store (gitignored)
├── state/                     # Local Quixstreams state (gitignored)
├── target/                    # Rust build artifacts (gitignored)
└── .venv/                     # Python venv (gitignored)
```

### Why this top-level shape

The decision was to **mirror `project_01` (battery-pdm) conventions as
closely as possible**, because (a) those conventions are already validated
against `mise.toml` + `uv` + `justfile` workflows that work on the author's
Windows + WSL + devcontainer setup, and (b) consistency across portfolio
projects is itself a senior signal — a reviewer who's seen one of the
projects can find their way around the other immediately.

Where we deviate from `project_01`, we deviate on purpose:

| Where | `project_01` | This repo | Why |
|-------|--------------|-----------|-----|
| Service layout | `src/battery_pdm/` (single package) | `services/<name>/` (per-service packages) | We're a multi-service streaming system; battery-pdm is a batch monolith |
| Container orchestration | AWS Batch direct | K8s (kind locally; EKS in cloud) | Cloud-agnostic by ADR 006 |
| Manifest tool | n/a (Terraform deploys to Batch directly) | Kustomize base+overlays | We have a K8s control plane to manage |
| Languages | Python only | Python + Rust | Rust decisioner per ADR 004 |
| Task runner | `justfile` | `justfile` (and `Makefile` kept for legacy crypto) | Same |
| Docs | numbered chapters + ADRs + cards | same | Direct adoption |
| IaC | `infra/main.tf` at root | `infra/terraform/` (subdir) + `infra/` Python | We have non-Terraform IaC concerns too (Python abstractions) |

---

## `services/` — the application code

```
services/
├── news/                      # ARCHIVE — Pau's course leftover; not on active surface (ADR 011)
├── news-sentiment/            # ARCHIVE — same (ADR 011)
│
├── transactions/              # synthetic credit txn stream producer (Day 1)
├── training_flow/             # Day-2 offline training pipeline (synthetic-RCT + T-learner)
├── decisioner/                # Python FastAPI /decide — collapsed request plane (ADR 008, supersedes 004)
├── drift_monitor/             # reads decision stream → emits drift events (Day 5)
├── retraining_flow/           # Metaflow flow on the batch plane (Day 5)
└── outcome_collector/         # joins outcomes back to decisions for off-policy eval (Day 6)
```

Note: `services/behavioral_features/` was removed in Day 1 per ADR 009 —
feature computation moved into RisingWave SQL.

### Why news + news-sentiment are archived (not deleted)

ADR 007 originally retained these two for the `macro_sentiment_1h`
feature. ADR 011 superseded that decision: the feature was never
actually wired into the credit-decisioning pipeline, the services were
running cost for no signal, and the platform now ships with a purely
behavioral feature set.

The directories remain on disk as provenance — they document the Pau
course inheritance baseline and the scope of what was rebuilt — but
they exit the uv workspace, exit CI, and exit the active docs.

The full cohort-4 baseline lives in a separate archive at
`C:\Users\abhin\realtime-ml-cohort-4-archive\` on the `cohort-4`
branch, frozen at the pre-Day-0 commit `5d09345`. It is not pulled
into this repo; it exists only as a provenance reference.

### Why each service is its own directory with its own pyproject.toml?

Each service is a separately-deployable artifact (its own Dockerfile, its
own K8s Deployment, its own dependencies). uv workspaces lets every service
have its own `pyproject.toml` while sharing the lockfile, which is the
cleanest way to express "independent but coordinated" packages.

### What lives inside a Python service dir?

```
services/<name>/
├── pyproject.toml            # name, version, deps; uv workspace member
├── settings.env              # non-secret per-service env (existing crypto convention)
├── README.md                 # what this service does, in 2-3 paragraphs
├── src/<name>/               # the package (src-layout for uv)
│   ├── __init__.py
│   ├── config.py             # pydantic Settings class
│   ├── main.py               # entrypoint
│   └── py.typed              # marks as typed for mypy --strict
└── state/                    # Quixstreams state if stateful (gitignored)
```

### What lives inside a Rust service dir?

```
services/decisioner/
├── Cargo.toml                # crate manifest; member of root Cargo workspace
├── README.md
├── src/
│   ├── main.rs               # tokio main + axum router
│   ├── lib.rs                # public crate surface
│   ├── config.rs             # env-driven Settings
│   ├── db.rs                 # sqlx PG pool for RisingWave lookup
│   ├── routes/
│   │   ├── health.rs
│   │   └── decide.rs         # the /decide handler
│   ├── inference.rs          # ort (ONNX Runtime) wrapper
│   └── bandit.rs             # contextual bandit
└── target/                   # build artifacts (gitignored)
```

---

## `deployments/` — Kustomize base + overlays

```
deployments/
├── base/                      # cloud-agnostic; works on any K8s
│   ├── kustomization.yaml
│   ├── services-crypto/       # the existing crypto service manifests
│   ├── services-finance/      # new finance service manifests
│   ├── mlflow/                # our custom MLflow Deployment (ADR 005)
│   └── README.md
└── overlays/
    ├── local-kind/            # patches: kind, MinIO, local registry, lower limits
    ├── aws-eks/               # patches: ECR, S3, IRSA, gp3 storage, ALB ingress
    └── on-prem/               # placeholder for future on-prem
```

Rationale documented in **ADR 006: Kustomize base+overlays (not Helm)**.
Two-line summary: each environment is a small overlay that patches the
shared `base/`; Helm is reserved for third-party charts (Strimzi, RisingWave)
where the chart provides genuine value.

The legacy `deployments/dev/kind/` directory from the crypto pipeline is
preserved as-is for now. Migration to `base/` + `overlays/local-kind/`
happens manifest-by-manifest as each service is touched.

---

## `infra/` — IaC and cloud-touch abstractions

```
infra/
├── terraform/                 # AWS infrastructure (EKS, VPC, ECR, S3, IRSA)
│   ├── main.tf
│   ├── variables.tf
│   ├── versions.tf
│   ├── terraform.tfvars.example
│   └── modules/
│       ├── vpc/
│       ├── eks_cluster/
│       ├── ecr/
│       ├── s3/
│       ├── iam_irsa/
│       └── mlflow_server/
└── lib/                       # Python: thin abstractions for cloud touch
    ├── object_store.py        # boto3 with configurable endpoint (MinIO / S3 / GCS)
    ├── secret_provider.py     # K8s Secret / AWS SM / GCP SM via External Secrets
    ├── feature_store_client.py # RisingWave-Postgres client
    ├── workflow_trigger.py    # Metaflow / Argo Events trigger helper
    └── trace.py               # OpenTelemetry exporter config
```

### Why `infra/terraform/` and `infra/lib/` together (not separate top-levels)?

Both are "the parts of the system that touch *outside* the application
code" — one provisions infrastructure, the other gives application code a
thin abstraction over cloud APIs. Keeping them under `infra/` makes the
"where does cloud touch happen?" answer one-directory clear.

`project_01` puts Terraform at `infra/` directly (no subdirectory) and has
no Python infra layer. We deviate because we have both concerns; nesting
under `infra/` is the cleanest expression.

### Why `infra/lib/` is Python and not Rust?

The cloud-touch abstractions are imported by Python services (transactions,
behavioral_features, drift_monitor, retraining_flow). The Rust decisioner
has its own thin equivalents inline (sqlx for RisingWave, env vars for
config, no S3 access in the request path).

---

## `docs/` — chapter docs + ADRs + cards + runbooks

```
docs/
├── repo_layout.md             # this file
├── day0_log.md                # running change log for Day 0 infra work
│
├── 01_problem_and_domain.md   # what we're solving + domain background
├── 02_data_and_features.md    # synthetic txn schema + behavioral features
├── 03_models_and_choices.md   # neural T-learner + bandit + champion-challenger
├── 04_results_and_metrics.md  # uplift gain, latency p99, throughput (populated as built)
├── 05_architecture.md         # the three-plane diagram, call graphs
├── 06_production_patterns.md  # interview-ready production patterns walkthrough
├── 07_interview_qa.md         # drilled Q&A on the above
├── 08_realtime_vs_batch.md    # why the streaming and batch planes are split
│
├── architecture_diagrams.md   # ASCII + mermaid diagrams
├── data_card.md               # dataset card per Google's Data Cards Playbook
├── model_card.md              # model card per Mitchell et al. 2019
├── incidents.md               # operational incidents log (populated as they happen)
├── tour.md                    # 10-minute interview demo script
├── project_book.md            # narrative overview (the "blog post" version)
├── DESIGN.md                  # one-pager design summary for fast skim
├── AWS_DEPLOYMENT.md          # how to deploy via the aws-eks overlay
├── REGULATORY_COMPLIANCE.md   # SR 11-7 / ECOA / Reg B mapping (Day 8)
├── LOAD_TEST_RESULTS.md       # k6 reports (Day 7)
│
├── runbooks/
│   ├── retraining.md
│   ├── rollback.md
│   ├── drift_response.md
│   └── oncall.md
│
├── decisions/                 # Architecture Decision Records (ADRs)
│   ├── README.md              # index
│   ├── 001-...md
│   └── ...
│
└── screenshots/               # demo screenshots, dashboard exports
```

### Why chapter numbering (`01_`, `02_`, ...)?

Directly adopted from `project_01/docs/`. Numbered chapters force a linear
reading path: someone landing on the repo can read top-to-bottom and end
up with a complete mental model. Unnumbered docs (cards, runbooks,
incidents) are reference material consulted as needed.

### Why ADRs in `docs/decisions/` and not at the repo root?

ADRs are documentation, not config. Keeping them under `docs/` means the
docs directory is the single source of truth for project knowledge.
`project_01` uses the same layout.

### Why a `day0_log.md` separately?

Day 0 is infra hardening + scaffolding work that happens once and then
should be findable later. The chronological log makes it easy to answer
"what did I change to make MLflow work?" or "when did we rename the repo?"
in a single grep. After Day 0 ends, this file stops being appended to
(it becomes an immutable record).

---

## `scripts/` — shell scripts

`build-and-push-image.sh` and `deploy.sh` are general-purpose build
pipeline helpers retained from the cohort substrate (ADR 007).
`create-mlflow-secret.sh` was authored here and is the idempotent
secret-applier for the MLflow MinIO Secret. New scripts
(`smoke_test_finance.sh`, `demo_drift.ps1`) land as the corresponding
days do.

No subdirectory structure — scripts are flat and named clearly.

---

## `.github/`

```
.github/
├── CODEOWNERS                 # auto-request review (single-author for now)
├── BRANCH_PROTECTION.md       # documents the GitHub branch protection rules in code
├── pull_request_template.md   # checklist: tests, docs, ADR-needed?
└── workflows/
    ├── ci.yml                 # lint + typecheck + test on every PR
    ├── deploy.yml             # build images + push to ECR on tag
    └── register-flows.yml     # register Metaflow flows on flow code changes
```

Convention directly mirrors `project_01/.github/`.

---

## Things that are **NOT** here, on purpose

- **`tests/` at the repo root**: tests live inside each service's package
  (`services/<name>/tests/`), per uv conventions. A repo-root `tests/` would
  blur the per-service ownership.
- **`apps/` or `cmd/` top-level**: each service in `services/` IS an app;
  no separate dir for entry points.
- **`packages/` or `libs/` for shared Python code**: shared code lives in
  `infra/lib/`; no separate `libs/` because there are very few cross-service
  shared modules. We resist premature shared-library extraction.
- **`configs/` directory**: per-service config lives in
  `services/<name>/settings.env`; cluster-level config lives in Kustomize.
  No global `configs/` to drift from those.
- **`api/` top-level for HTTP services**: the Rust HTTP service lives in
  `services/decisioner/` like every other service. It isn't special
  enough to warrant its own top-level dir.

---

## Layout invariants that should not be broken

These are the rules I'd flag in PR review:

1. **Every directory under `services/` is independently deployable** —
   has its own `pyproject.toml` / `Cargo.toml`, Dockerfile, K8s manifests.
   If something doesn't deploy on its own, it doesn't live here.
2. **Cloud touch happens only inside `infra/`** — application services
   import from `infra.lib.*` for S3, secrets, etc. They never `import
   boto3` directly. (Exception: the Rust decisioner has tiny inline
   equivalents because it doesn't share Python with the others.)
3. **The `base/` overlay is cloud-agnostic** — no AWS-specific anything
   in `deployments/base/`. If it can only run on AWS, it belongs in
   `overlays/aws-eks/`.
4. **ADRs are immutable once Accepted** — if a decision changes, write a
   new ADR that supersedes the old one; don't edit the original.
5. **`.env.local` and `*-secret.yaml` are never committed** — enforced by
   `.gitignore` patterns + secret-rotation in `day0_log.md`.
6. **MLflow run paths and Quixstreams state stay local** — `mlruns/`,
   `state/`, `target/`, `.venv/` are gitignored. Reproducibility comes
   from code + Terraform, not from committing build artifacts.
7. **Commit early and often — uncommitted work has no archive.** Before
   any destructive filesystem operation, run `git status` and confirm
   nothing valuable is untracked. Added after the 2026-06-04 incident
   (see `docs/incidents.md`) where months of un-staged work were lost
   during the crypto-domain split.

---

## Provenance summary

| Convention | Source | Notes |
|------------|--------|-------|
| Numbered chapter docs | `project_01` (battery-pdm) | Direct adoption |
| `docs/decisions/` Nygard ADRs | `project_01` | Direct adoption |
| `justfile` + Makefile coexistence | `project_01` + cohort substrate | `project_01` justfile pattern; Makefile retained for the news pipeline |
| `services/<name>/` per-service packages | uv workspace conventions + cohort substrate | Existing pattern adopted from the upstream |
| Kustomize `base/` + `overlays/` | Kustomize docs + ArgoCD best practices | ADR 006 |
| Three-plane decomposition | Authored here | ADR 004 |
| RisingWave-as-feature-store | Authored here | ADR 002 |
| `infra/terraform/` + `infra/lib/` | Adapted from `project_01` (Terraform-only) | We added the Python lib subdir |
| `env.shared` + `.env.local` split | direnv convention + Twelve-Factor | Common practice |
| `.github/` CODEOWNERS + workflows | `project_01` | Direct adoption |
| `data_card.md` + `model_card.md` | Google Data Cards Playbook + Mitchell et al. 2019 | Standard ML documentation |
| `runbooks/` | SRE Book convention | Standard ops practice |
| `mise.toml` for tool pinning | mise.jdx.dev convention | Already in the repo |
