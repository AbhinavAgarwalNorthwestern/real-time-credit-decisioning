# `deployments/base/` — cloud-agnostic Kubernetes manifests

Everything in this directory must work unchanged on any Kubernetes cluster:
local kind, EKS, GKE, AKS, on-prem. No AWS-specific annotations, no GCP
service accounts, no cluster-specific storage classes.

Per-environment differences (image registries, storage classes, ingress
controllers, secrets sources) live in `../overlays/<env>/` as patches over
this base.

See **ADR 006** (`docs/decisions/006-base-overlays-kustomize-not-helm.md`)
for the reasoning behind this structure.

## Structure

```
base/
├── kustomization.yaml         # lists all base resources
├── services-crypto/           # existing crypto service manifests (migration progressive)
├── services-finance/          # new finance service manifests
└── mlflow/                    # the custom MLflow Deployment (ADR 005)
```

## Migration status

The legacy `deployments/dev/kind/manifests/` directory is the authoritative
source for the existing crypto pipeline. Manifests migrate to `base/` +
`overlays/local-kind/` as they're touched. Do not duplicate manifests in
both locations.
