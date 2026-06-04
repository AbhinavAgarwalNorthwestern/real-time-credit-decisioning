# ADR 005: MLflow tracking server with `--serve-artifacts` proxy

**Status:** Accepted
**Date:** 2026-06-03
**Decision makers:** Platform owner

## Context

The platform uses MLflow for experiment tracking and the model registry.
Artifacts (training datasets, EDA reports, model files, SHAP values, plots)
need to land in object storage — locally in MinIO, in S3 on AWS.

Two architectural shapes for artifact handling:

1. **Direct client-to-store**: each client (training Pod, laptop, CI runner)
   talks directly to MinIO/S3 to upload/download artifacts. The MLflow
   tracking server only handles metadata (params, metrics, registry).
2. **Server-side proxy**: MLflow server is configured with
   `--serve-artifacts` and `--default-artifact-root mlflow-artifacts:/`.
   Clients send artifacts to the MLflow server's `/api/2.0/mlflow-artifacts/`
   endpoint, which proxies to the underlying store server-side.

The initial Bitnami Helm chart deployment of MLflow used shape (1).
Artifact uploads failed from outside the cluster because the cluster-internal
DNS name of MinIO (`risingwave-minio.risingwave.svc.cluster.local`) was not
resolvable from a laptop client, and distributing MinIO credentials to every
client widens the attack surface.

Investigation traced the failure to the artifact-write path crossing a
network boundary the client could not reach. The fix was to route artifact
I/O through the MLflow server itself.

## Decision

We run a **custom MLflow Deployment** (`deployments/dev/kind/manifests/mlflow-final.yaml`)
that starts the server with:

```
mlflow server \
  --backend-store-uri $DATABASE_URL \
  --default-artifact-root mlflow-artifacts:/ \
  --artifacts-destination s3://mlflow-d971/ \
  --serve-artifacts
```

The MinIO endpoint (`MLFLOW_S3_ENDPOINT_URL`) and AWS-style credentials
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) are configured on the
**server only**, sourced from the `mlflow-minio-secret` K8s Secret. Clients
need only the MLflow tracking URI; they never touch MinIO directly.

The Bitnami Helm chart (`mlflow-values.yaml`) is retained for reference but
not used.

## Consequences

### Positive

- **Clients don't need MinIO network reachability.** Works from a laptop,
  from CI runners, from any pod in any namespace.
- **Single security boundary** at the MLflow server. Rotating credentials
  is one place, not N places.
- **Standard production pattern** — most enterprise MLflow deployments run
  with `--serve-artifacts` for the same reasons.
- **Works across NAT/firewall boundaries** — no need for VPN or cluster
  ingress to MinIO.
- **Cluster-internal DNS does not leak to clients** — clients only know
  the MLflow URL, not the storage URL.

### Negative

- **MLflow server becomes a throughput bottleneck** for artifact I/O. In
  practice artifact upload is rare (per-run, not per-request), so this
  rarely matters. Server pod sizing accounts for it.
- **Server pod needs egress credentials** to the underlying store. On AWS
  this becomes IRSA, eliminating the embedded-credentials problem; on
  local-kind it stays a K8s Secret sourced from `.env.local`.
- **Custom Deployment instead of Bitnami chart** — slightly more
  maintenance burden. Mitigated by keeping the YAML small and the
  responsibility tight (just MLflow, just serve-artifacts).
- **One more pod in the request path** for artifact reads. Not measurable
  at our usage pattern.

## Alternatives considered

- **Direct client → MinIO/S3** (Bitnami default): rejected because client
  network reachability and credential distribution are real operational
  problems.
- **Pre-signed URLs**: MLflow can issue presigned URLs to clients. Adds a
  round trip and additional code; doesn't eliminate the credential problem
  on the server side.
- **Separate artifact service** (custom microservice): redundant — that's
  exactly what `--serve-artifacts` already provides.
- **Skip MLflow entirely, use W&B / Comet / in-house**: rejected — MLflow
  is the registry that the model-loading code already depends on, and is
  the cleanest open-source path.

## Related

- ADR 006: the AWS overlay swaps the underlying store from MinIO to S3 via
  IRSA, but the server-proxy pattern is identical
- `deployments/dev/kind/manifests/mlflow-final.yaml`: the actual Deployment
- `scripts/create-mlflow-secret.sh`: idempotent secret apply from
  `.env.local`
- `docs/day0_log.md`: the rotation event and the credential refactor
