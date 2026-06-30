#!/bin/bash
# Install RisingWave (streaming SQL DB) + its bundled Postgres + bundled MinIO.
# Idempotent + path-independent. helm upgrade --install handles re-runs.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

helm repo add risingwavelabs https://risingwavelabs.github.io/helm-charts/ --force-update
helm repo update

helm upgrade --install --create-namespace --wait risingwave risingwavelabs/risingwave \
    --namespace=risingwave \
    -f "${SCRIPT_DIR}/manifests/risingwave-values.yaml"
