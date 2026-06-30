#!/bin/bash
# Bootstrap the local kind cluster + every infra component the platform needs:
#   1. kind cluster + docker network
#   2. ingress-nginx
#   3. RisingWave (with bundled Postgres + MinIO)
#   4. Strimzi Kafka operator + Kafka cluster
#   5. Kafka UI
#   6. Grafana
#
# MLflow is NOT installed here — it depends on MinIO access keys that you
# either generate manually in the MinIO console (per the cohort doc) or
# extract from the auto-generated `risingwave-minio` Secret. See the
# "Next steps" message printed at the end.
#
# Idempotent: re-running deletes the old cluster + network first.
# Path-independent: resolves all referenced files via $SCRIPT_DIR.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

CLUSTER_NAME="rwml-34fa"
NETWORK_NAME="rwml-34fa-network"

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

# 1. Delete the cluster (if it exists)
log "Deleting old cluster '${CLUSTER_NAME}' if present..."
kind delete cluster --name "$CLUSTER_NAME" || true

# 2. Delete the docker network (if it exists)
log "Deleting old docker network '${NETWORK_NAME}' if present..."
docker network rm "$NETWORK_NAME" 2>/dev/null || true

# 3. Create the docker network
log "Creating docker network '${NETWORK_NAME}'..."
docker network create --subnet 172.200.0.0/16 "$NETWORK_NAME"

# 4. Create the kind cluster
log "Creating kind cluster '${CLUSTER_NAME}'..."
KIND_EXPERIMENTAL_DOCKER_NETWORK="$NETWORK_NAME" \
    kind create cluster --config "${SCRIPT_DIR}/kind-with-portmapping.yaml"

# 5. Wait for the control-plane node to become Ready
log "Waiting for cluster node Ready..."
kubectl wait --for=condition=Ready node --all --timeout=120s

# 6. Install ingress-nginx
log "Installing ingress-nginx..."
kubectl apply -f "${SCRIPT_DIR}/manifests/ingress-nginx-all-in-one.yaml"

# 7. Install RisingWave (with bundled Postgres + MinIO that MLflow will use)
log "Installing RisingWave (with bundled Postgres + MinIO)..."
chmod +x "${SCRIPT_DIR}/install_risingwave.sh"
"${SCRIPT_DIR}/install_risingwave.sh"

# 8. Install Strimzi + Kafka cluster
log "Installing Kafka (Strimzi operator + Kafka cluster)..."
chmod +x "${SCRIPT_DIR}/install_kafka.sh"
"${SCRIPT_DIR}/install_kafka.sh"

# 9. Install Kafka UI
log "Installing Kafka UI..."
chmod +x "${SCRIPT_DIR}/install_kafka_ui.sh"
"${SCRIPT_DIR}/install_kafka_ui.sh"

# 10. Install Grafana — tolerant; chart is deprecated (Day 7 TODO to replace)
log "Installing Grafana (deprecated chart; allowed to fail)..."
chmod +x "${SCRIPT_DIR}/install_grafana.sh"
"${SCRIPT_DIR}/install_grafana.sh" \
    || log "  ⚠ Grafana install failed (known: deprecated grafana/grafana chart). Continuing."

# 11. Install MLflow (database + secret + Deployment + rollout, fully automated)
log "Installing MLflow (DB + secret + Deployment)..."
chmod +x "${SCRIPT_DIR}/install_mlflow.sh"
"${SCRIPT_DIR}/install_mlflow.sh" \
    || log "  ⚠ MLflow install failed — check 'kubectl logs -n mlflow deployment/mlflow-tracking'."

log ""
log "Cluster bootstrap complete."
log ""
log "Next step — run smoke test phase 1:"
log "       PHASE=1 bash ${REPO_ROOT}/scripts/smoke_test_finance.sh"
log ""
log "Useful follow-up commands:"
log "  - kubectl get pods -A                         # confirm everything Running"
log "  - kubectl -n mlflow port-forward svc/mlflow-tracking 8889:80   # MLflow UI"
log "  - kubectl -n kafka  port-forward svc/kafka-ui 8182:8080        # Kafka UI"
