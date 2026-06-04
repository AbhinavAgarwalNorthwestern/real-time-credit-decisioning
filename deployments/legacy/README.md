# `deployments/legacy/` — superseded manifests kept for reference

Manifests in this directory are **NOT applied** by any active overlay.
They are kept so the project's history is readable — someone reviewing
the codebase later can see what was tried and why it was replaced.

## Inventory

| File | Replaced by | Why |
|------|-------------|-----|
| `mlflow-values-bitnami.yaml` | `deployments/dev/kind/manifests/mlflow-final.yaml` | The Bitnami Helm chart routed artifacts directly from client → MinIO. That failed for clients outside the cluster because they couldn't resolve the cluster-internal MinIO DNS. We replaced it with a raw Deployment running `mlflow server --serve-artifacts` (artifact proxy mode). See **ADR 005** (`docs/decisions/005-mlflow-artifact-proxy-not-direct-s3.md`). |

## Policy

When a manifest is replaced by a better version, move the old file here
with a 1-line entry above. Do not delete the old file outright — its
existence + the reasoning is interview-defensible signal (it shows real
debugging, not a green-field fairytale).
