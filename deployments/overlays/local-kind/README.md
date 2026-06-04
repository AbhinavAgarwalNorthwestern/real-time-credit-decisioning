# `overlays/local-kind/` — local laptop development on a kind cluster

Patches the cloud-agnostic `base/` for laptop development:

- **Images** point at a local registry (typically `localhost:5000/<service>:dev`),
  populated by `scripts/build-and-push-image.sh <service> dev`
- **Storage class** is `standard` (kind's default `local-path` provisioner)
- **Replicas** are forced to 1 for every Deployment (laptop scale)
- **Resource limits** are small (CPU 100m, memory 256Mi typical)
- **MinIO** is the object store (provided by the bundled RisingWave Helm chart)
- **MLflow** uses an embedded MinIO Secret (`mlflow-minio-secret`, sourced from
  `.env.local` via `scripts/create-mlflow-secret.sh`)
- **Ingress** uses nginx-ingress (installed by `deployments/dev/kind/`)

## Apply

```bash
just k8s-apply-local
# or directly:
kubectl apply -k deployments/overlays/local-kind
```

## Migration status

The existing crypto-pipeline manifests live in `deployments/dev/kind/` and
are not yet referenced by this overlay. Migration happens manifest-by-manifest
as each service is touched. Until then, the legacy directory and this
overlay coexist.
