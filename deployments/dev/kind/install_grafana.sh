#!/bin/bash
# Install Grafana into the monitoring namespace.
#
# KNOWN DEPRECATION (Day 1, 2026-06-05):
#   The grafana/grafana Helm chart is marked deprecated upstream and the
#   pods reliably PodInitializing-stall in our kind cluster. We keep this
#   script wired up so create_cluster.sh's orchestration is complete, but
#   the helm --timeout is short (60 s) so a failed install doesn't pause
#   the bootstrap for the default 5 minutes.
#
#   Day 7 TODO: replace `grafana/grafana` with one of:
#     - bitnami/grafana       (Bitnami chart, still maintained)
#     - grafana-operator      (CRD-based, cleaner for managed dashboards)
#
# Idempotent + path-independent.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

helm repo add grafana https://grafana.github.io/helm-charts --force-update
helm repo update

helm upgrade --install --create-namespace --wait grafana grafana/grafana \
    --namespace=monitoring \
    --timeout=60s \
    --values "${SCRIPT_DIR}/manifests/grafana-values.yaml"
