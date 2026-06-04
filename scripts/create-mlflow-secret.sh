#!/usr/bin/env bash
# Creates/updates the mlflow-minio-secret K8s Secret from .env.local credentials.
# Idempotent — safe to run repeatedly. Run after rotating MinIO credentials.
#
# Usage:  bash scripts/create-mlflow-secret.sh
# Reads:  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY from .env.local at repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.local"

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: ${ENV_FILE} not found" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "ERROR: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in .env.local" >&2
  exit 1
fi

kubectl create namespace mlflow --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic mlflow-minio-secret \
  --namespace=mlflow \
  --from-literal=AccessKeyID="${AWS_ACCESS_KEY_ID}" \
  --from-literal=SecretKey="${AWS_SECRET_ACCESS_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "OK mlflow-minio-secret applied to namespace mlflow"
echo "Restart the MLflow pod to pick up the new credentials:"
echo "  kubectl -n mlflow rollout restart deployment/mlflow-tracking"
