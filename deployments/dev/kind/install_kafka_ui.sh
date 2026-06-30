#!/bin/bash
# Install Kafka UI (provectus/kafka-ui).
# Idempotent + path-independent.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

kubectl apply -f "${SCRIPT_DIR}/manifests/kafka-ui-all-in-one.yaml"
