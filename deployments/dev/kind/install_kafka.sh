#!/bin/bash
# Install Strimzi Kafka operator + the kafka-e11b Kafka cluster.
# Idempotent + path-independent.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Namespace (idempotent — won't error if it exists)
kubectl create namespace kafka --dry-run=client -o yaml | kubectl apply -f -

# Strimzi operator + CRDs.
# Use --server-side because Strimzi's CRDs are large enough to bust the
# 256 KB last-applied-configuration annotation that `kubectl apply` writes
# client-side. With client-side apply the CRDs would silently fail to install
# while the smaller operator manifests succeed — leaving us with a "running
# operator + no CRDs" state.
kubectl apply --server-side --force-conflicts \
    -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka

# Wait for the Strimzi operator Deployment to be Ready before applying the CR
kubectl -n kafka rollout status deployment/strimzi-cluster-operator --timeout=180s || true

# Wait for the CRDs to be Established. The operator deployment can become
# Ready before the API server is serving the CRD endpoints — applying the
# CR before this point fails with "no matches for kind" and is silent
# because we don't `set -e`.
kubectl wait --for=condition=Established --timeout=60s \
    crd/kafkas.kafka.strimzi.io \
    crd/kafkanodepools.kafka.strimzi.io \
    crd/kafkatopics.kafka.strimzi.io \
    crd/kafkausers.kafka.strimzi.io

# Apply the Kafka cluster CR.
# The manifest uses apiVersion kafka.strimzi.io/v1 (Strimzi 0.46+ removed
# v1beta2 from served versions — see day0_log Session 11 / Day 1 Phase B).
kubectl apply -f "${SCRIPT_DIR}/manifests/kafka-e11b.yaml"

# Wait for the Kafka cluster CR to be Ready before applying topics.
# The entity-operator (which reconciles KafkaTopic CRs onto the broker)
# is part of the Kafka cluster — it doesn't exist until the cluster is up.
# Without this wait, topic creation would race the operator and silently
# fail; symptom downstream is "topic <name> not found" when RisingWave
# tries to CREATE SOURCE.
kubectl wait --for=condition=Ready --timeout=300s kafka/kafka-e11b -n kafka || true

# Apply topic CRs (transactions, decisions, outcomes, drift-events).
# These are needed by RisingWave's CREATE SOURCE (Day 1 D1), the decisioner's
# audit log producer (Day 3), and the drift_monitor → retraining_flow event
# chain (Day 5). Creating them at bootstrap time avoids per-day topic-create
# steps and the "topic not found" race seen on first Day 1 bootstrap attempt.
kubectl apply -f "${SCRIPT_DIR}/manifests/kafka-topics.yaml"
