#!/bin/bash
# Install External Secrets Operator into the dev kind cluster.
#
# Used to bootstrap FAANG Tier 1D (secret rotation) in dev. The production
# AWS overlay applies the same operator + AWS Secrets Manager backend in
# deployments/overlays/aws-eks/external-secrets-store.yaml.
#
# Idempotent: re-running is a no-op if the chart is already installed.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="external-secrets"

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m!!!\033[0m %s\n' "$*" >&2; }

if ! command -v helm >/dev/null 2>&1; then
    err 'helm not found on PATH; install via mise'
    exit 1
fi

log "Adding external-secrets Helm repo..."
helm repo add external-secrets https://charts.external-secrets.io 2>&1 | sed 's/^/  /'
helm repo update external-secrets 2>&1 | sed 's/^/  /'

log "Installing operator into namespace ${NAMESPACE}..."
helm upgrade --install external-secrets \
    external-secrets/external-secrets \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --set installCRDs=true \
    --set webhook.port=9443 \
    --wait \
    --timeout=120s

log "Waiting for operator pods to be Ready..."
kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod \
    -l app.kubernetes.io/name=external-secrets \
    --timeout=120s

log "ESO installed successfully. Next steps:"
log "  - For AWS: kubectl apply -f deployments/overlays/aws-eks/external-secrets-store.yaml"
log "  - For dev: use Kubernetes-Secret-backed SecretStore (no rotation; for testing only)"
log "  - Docs: docs/runbooks/secret_rotation.md"
