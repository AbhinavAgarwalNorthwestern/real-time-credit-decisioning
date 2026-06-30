# ADR 013 — Dev-vs-AWS Validation Split

**Status**: Accepted (2026-06-30)

**Context**

Sessions 1-7 (the original FAANG-hardened build) and the customer-attributes
rollout all ran the full 11-service stack on a local kind cluster inside a
VS Code devcontainer. This worked through v1.0.0 but hit hard walls during
v1.1.0:

- Docker Desktop's WSL2 disk image exhausted available C: drive space
  multiple times (98% utilization) during repeated `uv sync` cycles and
  kind volume growth.
- Bundled MinIO inside the RW Helm chart was repeatedly rate-limited
  (`MINIO_API_REQUESTS_MAX` 114 → patched to 10000) under combined
  backfill + live producer load.
- The RisingWave shared-source replay path tripped a Hummock storage
  bug (`unexpected decreasing now`); the workaround
  (`scan.startup.mode = 'latest'`) costs the ability to replay historical
  Kafka events at apply time.
- The training pipeline retry loop wasted multiple session-hours
  reproducing the same memory pressure failures.
- Docker Desktop's engine itself entered a "starting → stopped" loop,
  blocking all local work mid-session.

The local cluster had served its purpose: it proved the architecture
end-to-end and surfaced the operational failure modes that any production
deployment will eventually encounter. Continuing to run all 11 services
locally past that point was strictly negative value.

**Decision**

Adopt a hard split between local and AWS work, codified per session
phase:

| Phase | Where | What runs |
|---|---|---|
| **Sessions 1-5 of a project** | Local kind in devcontainer | Full stack. Goal: prove the architecture works end-to-end. Surface operational failure modes. |
| **Sessions 6+** | Local laptop (VS Code, no devcontainer) for code editing + unit tests of single services. AWS dev EKS for everything else. | Inner loop on laptop. Integration validation, multi-service tests, full retraining, version tags, load tests — all on dev EKS. |

Concrete rules:

1. **Code editing**: laptop only. VS Code on Windows, no devcontainer
   needed for editing.
2. **Unit tests for a single service**: laptop is fine if the venv is
   already populated. Multi-service unit suites: CI on GitHub Actions.
3. **Integration tests (multi-service)**: CI on GitHub Actions or
   on dev EKS via a Job.
4. **Pipeline tests (full training_flow on real RW)**: dev EKS only.
   Triggered by `kubectl create job` against a manifest in
   `deployments/base/services-finance/`.
5. **Load tests**: dev EKS only. k6 from a GitHub Actions runner against
   the EKS-resolved LoadBalancer.
6. **Version tags (`v*`)**: only after cluster validation on dev EKS.
   A tag implies the artifact has been observed working in cluster.
7. **Tear-down discipline**: `terraform destroy` at end of each working
   session that spun up dev EKS. Manual destroy is the safety net since
   no billing alarm is configured (user choice).

The cloud-agnostic property (ADR 006: Kustomize base + overlays) is
preserved: code structure remains portable to GCP / on-prem; the dev
substrate is *where we happen to run dev*, not *which cloud the project
targets*.

**Consequences**

Positive:

- v1.1.0 unblocked: AWS dev EKS has the disk + memory + service quotas
  the laptop kind cluster cannot offer.
- The proper CI/CD pattern (GitHub Actions → ECR → kubectl apply) gets
  exercised end-to-end, not just declared. Real value for the FAANG /
  India-fintech resume defense.
- Operational reality matches: in real life nobody runs production-shape
  workloads on a laptop. The local kind cluster was a learning artifact;
  past that artifact's purpose, AWS is the right substrate.
- The kind cluster's pain (Hummock errors, MinIO 429, snapshot backfill
  bug, pyarrow corruption, Docker WSL2 disk pressure) is now permanent
  knowledge documented in `INFRASTRUCTURE.md` fix log — interview gold
  on its own.

Negative:

- ~$16/day spend during active dev EKS sessions (manageable; tear down
  end of day).
- Cloud-agnostic *coverage* drops if we never exercise the GCP / on-prem
  overlays. Mitigation: per `INFRASTRUCTURE.md` §X, quarterly minimal
  kind smoke tests confirm the local-kind overlay still applies cleanly.
- The 1-day kind cluster proof becomes outdated unless reproduced
  periodically. Mitigation: same as above.

**Specific tactical decisions made in service of this ADR (2026-06-30 session)**

- GitHub OIDC IAM role created via AWS CLI (not Terraform yet — codify
  follow-up). Trust policy scoped to
  `repo:AbhinavAgarwalNorthwestern/real-time-credit-decisioning:*`.
  Attached `AdministratorAccess` for speed; narrow to least-privilege
  in follow-up.
- Local Terraform state used for the dev cluster (S3 backend block
  commented out in `main.tf`). For prod-grade workflows, create the
  state bucket manually with versioning + encryption + lock and uncomment.
- Node group sized to 5× m6i.large (40 GB RAM, 10 vCPU) — bigger than
  the original 3 × m6i.large default to accommodate all 11 services
  plus the backfill+training peak. Cost: ~$5/day premium.
- Local Docker abandoned for this project. Image builds run in GitHub
  Actions and push to ECR. Devcontainer not used post v1.0.0.

**Alternatives considered**

- *Continue on local kind with more disk*: would require Docker Desktop
  reconfiguration + larger C: partition + WSL2 VHD growth. Doesn't
  address the structural issue (laptop CPU + memory still saturate
  during backfill+training peak).
- *Move to a hosted dev K8s service like Civo / DigitalOcean*: cheaper
  ($5/day vs $16) but breaks the AWS-overlay validation path (different
  managed services, different IAM model). Not aligned with the
  India-fintech AWS positioning.
- *Run dev on AWS but in a separate AWS account*: cleaner blast-radius
  isolation. Adds AWS Organizations setup overhead. Deferred for now;
  reconsider if multi-project AWS work scales up.

**References**

- ADR 006: Kustomize base + overlays (preserves cloud-agnostic claim)
- `docs/INFRASTRUCTURE.md` §7 fix-log: kind operational failure modes
  that motivated this decision
- `docs/AWS_DEPLOYMENT.md`: procedure + rationale for the AWS path
- `docs/scope_expansion_plan.md` Phase G: External Secrets Operator
  (formalizes the secret-rotation half of dev EKS workflow)
