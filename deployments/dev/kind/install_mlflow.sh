#!/bin/bash
# Bootstrap MLflow into the kind cluster.
#
# What was previously manual (per Pau's cohort doc) and is now automated:
#   1. Create the `mlflow` Postgres database in the bundled RisingWave Postgres
#   2. Extract MinIO root credentials from the auto-generated `risingwave-minio`
#      Secret and create the `mlflow-minio-secret` Secret in the `mlflow`
#      namespace from those values
#   3. Apply `manifests/mlflow-final.yaml` (the custom Deployment with
#      --serve-artifacts per ADR 005)
#   4. Wait for the rollout to complete
#
# Idempotent + path-independent.
#
# Security note (Day 7 hardening target):
#   This uses the MinIO root credentials directly as the MLflow access key.
#   That gives MLflow full bucket-admin access — fine for an ephemeral local
#   kind cluster that never leaves the laptop. Day 7 will replace this with
#   a scoped service-account access key created via:
#       mc admin user svcacct add ...
#   See docs/INFRASTRUCTURE.md §6.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Create the mlflow Postgres database
# ---------------------------------------------------------------------------
log "Creating 'mlflow' database in Postgres (idempotent)..."
PG_PASS=$(kubectl -n risingwave get secret risingwave-postgresql \
    -o jsonpath='{.data.postgres-password}' | base64 -d)

# psql exits non-zero if the database already exists; we tolerate that.
kubectl -n risingwave exec risingwave-postgresql-0 -- \
    env PGPASSWORD="$PG_PASS" psql -U postgres \
    -c "CREATE DATABASE mlflow;" 2>&1 \
    | grep -v 'already exists' \
    || true

# ---------------------------------------------------------------------------
# 2. Extract MinIO root credentials
# ---------------------------------------------------------------------------
log "Extracting MinIO root credentials..."
MINIO_USER=$(kubectl -n risingwave get secret risingwave-minio \
    -o jsonpath='{.data.root-user}' | base64 -d)
MINIO_PASS=$(kubectl -n risingwave get secret risingwave-minio \
    -o jsonpath='{.data.root-password}' | base64 -d)

if [ -z "$MINIO_USER" ] || [ -z "$MINIO_PASS" ]; then
    echo "ERROR: failed to extract MinIO credentials from Secret 'risingwave-minio'" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Create the mlflow-minio-secret K8s Secret
# ---------------------------------------------------------------------------
log "Creating mlflow-minio-secret in namespace 'mlflow' (idempotent)..."
kubectl create namespace mlflow --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic mlflow-minio-secret \
    --namespace=mlflow \
    --from-literal=AccessKeyID="$MINIO_USER" \
    --from-literal=SecretKey="$MINIO_PASS" \
    --dry-run=client -o yaml \
    | kubectl apply -f -

# ---------------------------------------------------------------------------
# 4. Apply the MLflow custom Deployment (per ADR 005)
# ---------------------------------------------------------------------------
log "Applying mlflow-final.yaml..."
kubectl apply -f "${SCRIPT_DIR}/manifests/mlflow-final.yaml"

# ---------------------------------------------------------------------------
# 5. Wait for the rollout to complete
# ---------------------------------------------------------------------------
log "Waiting for MLflow tracking Deployment to roll out..."
kubectl -n mlflow rollout status deployment/mlflow-tracking --timeout=300s

log "MLflow setup complete."
log ""
log "Verify the tracking UI is reachable:"
log "  kubectl -n mlflow port-forward svc/mlflow-tracking 8889:80 &"
log "  curl -sI http://localhost:8889/health   # expect HTTP 200"
log "  Open http://localhost:8889 in your browser"
