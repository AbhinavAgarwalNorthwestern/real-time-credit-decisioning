# ADR 006: Kustomize base+overlays for our manifests (Helm only for third-party charts)

**Status:** Accepted
**Date:** 2026-06-03
**Decision makers:** Platform owner

## Context

The system targets three deployment environments:

- **local-kind** — laptop development, single-node kind cluster
- **aws-eks** — production-style deployment on AWS EKS (managed by
  Terraform, see ADR 010 when written)
- **on-prem** — placeholder for a future on-cluster deployment with
  external object storage and in-house secrets

Each environment differs in:

- Image references (local registry vs ECR vs Harbor)
- Storage classes (local-path vs gp3 vs RWX)
- Ingress controller (nginx vs AWS Load Balancer Controller vs MetalLB)
- Object store endpoint (MinIO vs S3 vs internal)
- Database backend (bundled Postgres vs RDS)
- Credential source (K8s Secret vs IRSA vs External Secrets / Vault)
- Replica counts and resource limits

The application logic is identical across all three. The same Docker
images, the same Python/Rust code, the same Kafka topics, the same MLflow
model registry contract.

Two manifest-management strategies:

- **Helm everywhere** — every service is a chart with `values.yaml` per
  environment
- **Kustomize base+overlays** — one base set of manifests in plain YAML,
  per-environment overlays that patch the deltas

## Decision

We use **Kustomize base+overlays** for our own manifests. **Helm is used
only** to install third-party charts where the chart provides genuine value:
**Strimzi** (Kafka operator), **RisingWave** chart, and any future managed
operator install.

Structure:

```
deployments/
├── base/                       # cloud-agnostic; works on any K8s
│   ├── services-finance/       # our services, no env-specific values
│   ├── mlflow/                 # our custom MLflow Deployment (ADR 005)
│   └── kustomization.yaml
└── overlays/
    ├── local-kind/             # patches: local image registry, MinIO,
    │                           # bundled Postgres, kind storage class
    ├── aws-eks/                # patches: ECR images, S3 endpoint via
    │                           # IRSA, RDS Postgres, gp3 storage class,
    │                           # AWS LB Controller ingress
    └── on-prem/                # placeholder
```

## Consequences

### Positive

- **One source of truth.** All environments share the same base manifests;
  diffs are visible in the small overlay patches.
- **Plain YAML.** No template language; no `{{ if eq .Values.foo "bar" }}`
  blocks. Easier to review in PRs.
- **`kubectl` native.** `kubectl apply -k overlays/aws-eks` works directly,
  no extra binary required at runtime.
- **GitOps-friendly.** ArgoCD and Flux both natively understand Kustomize
  bases and overlays.
- **Cleaner mental model.** Environment differences are explicit; nothing
  is hidden inside a values file.
- **Helm still wins where it should.** Third-party operators we install via
  their official Helm chart use Helm. We don't fight the maintainers.

### Negative

- **Less powerful parameterization** than Helm templates. Our parameter
  space is small enough that this doesn't bite.
- **No package versioning** like Helm's chart versions. We pin via image
  tags and git tags instead.
- **Strategic merge patches have edge cases** with lists of named items
  (containers, ports). Workaround: use `jsonpatch` operations for those.
- **Less common at conservative banks** than Helm. Mitigated by
  explaining the choice via this ADR.

## Alternatives considered

- **Helm everywhere**: heavier; template language adds a layer of
  indirection; every minor edit is a values-file dance.
- **Helmfile**: another layer on top of Helm; doesn't fix the underlying
  template-language issue.
- **Jsonnet / CUE**: more powerful but steep learning curve; overkill for
  our deployment matrix size.
- **Plain YAML with `envsubst` / `sed`**: fragile; no diff-friendliness;
  hard to review.
- **Each environment as its own copy of the YAML**: drift is inevitable
  within a week.

## Related

- ADR 003: Metaflow `@kubernetes` runs on the same K8s clusters managed by
  these overlays
- ADR 005: the MLflow Deployment lives in `base/mlflow/`; the AWS overlay
  patches it to use IRSA instead of an embedded K8s Secret
- `docs/AWS_DEPLOYMENT.md`: the actual `aws-eks` overlay walkthrough
