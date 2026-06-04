#!/usr/bin/env bash
# Finance-pipeline smoke test.
#
# Phase 1 (Day 1): infrastructure health checks — Kafka, RisingWave, MLflow,
#                  and the credit-decisioning namespace are reachable and Ready.
# Phase 2 (Day 1 close): synthetic transaction event flows through Kafka
#                        and lands as a row in the behavioral_features
#                        materialized view in RisingWave.
# Phase 3 (Day 3+): full /decide → audit log → outcome round-trip.
#
# Exit codes:
#   0   all checks passed
#   1   infra failure (Kafka/RW/MLflow unreachable)
#   2   application service failure (pod not Ready)
#   3   end-to-end data path failure (no feature row produced)
#
# Idempotent. Safe to run repeatedly.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — overridable via env.shared / .env.local
# ---------------------------------------------------------------------------
NS_KAFKA="${NS_KAFKA:-kafka}"
NS_RISINGWAVE="${NS_RISINGWAVE:-risingwave}"
NS_MLFLOW="${NS_MLFLOW:-mlflow}"
NS_FINANCE="${NS_FINANCE:-real-time-ml}"

# How long to wait for any single pod to become Ready before failing.
POD_READY_TIMEOUT="${POD_READY_TIMEOUT:-180s}"

# Phase the test will run up to. Override with PHASE=1 / 2 / 3.
PHASE="${PHASE:-2}"

# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------
GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
RESET="\033[0m"

step() { printf "${YELLOW}==> %s${RESET}\n" "$1"; }
ok()   { printf "    ${GREEN}OK${RESET}  %s\n" "$1"; }
fail() { printf "    ${RED}FAIL${RESET} %s\n" "$1"; exit "${2:-1}"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Confirm a namespace exists.
require_ns() {
    local ns="$1"
    kubectl get namespace "$ns" >/dev/null 2>&1 \
        || fail "namespace '$ns' missing" 1
}

# Confirm at least one pod matching the selector is Running and Ready.
require_pod_ready() {
    local ns="$1" selector="$2" what="$3"
    if ! kubectl -n "$ns" get pods -l "$selector" 2>/dev/null | grep -q Running; then
        fail "$what pod not found or not Running in ns '$ns' (selector: $selector)" 2
    fi
    kubectl -n "$ns" wait --for=condition=Ready pod -l "$selector" \
        --timeout="$POD_READY_TIMEOUT" >/dev/null \
        || fail "$what pod did not become Ready within $POD_READY_TIMEOUT" 2
    ok "$what Ready in ns '$ns'"
}

# Confirm an HTTP endpoint responds 2xx within ~10s.
require_http_ok() {
    local url="$1" what="$2"
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" || echo "000")"
    if [[ "$code" =~ ^2 ]]; then
        ok "$what HTTP $code"
    else
        fail "$what HTTP $code (url: $url)" 1
    fi
}

# Count rows in a RisingWave table/view (Postgres protocol).
rw_count() {
    local relation="$1"
    PGPASSWORD="${RW_PASSWORD:-}" psql \
        -h "${RW_HOST:-localhost}" \
        -p "${RW_PORT:-4567}" \
        -U "${RW_USER:-root}" \
        -d "${RW_DATABASE:-dev}" \
        -t -A -c "SELECT count(*) FROM $relation;" 2>/dev/null \
        || echo "QUERY_FAILED"
}

# ---------------------------------------------------------------------------
# Phase 1 — infrastructure
# ---------------------------------------------------------------------------
phase_1() {
    step "Phase 1: cluster + infra reachability"

    require_ns "$NS_KAFKA"
    require_ns "$NS_RISINGWAVE"
    require_ns "$NS_MLFLOW"

    require_pod_ready "$NS_KAFKA"      "strimzi.io/cluster=kafka-e11b"      "Kafka broker"
    require_pod_ready "$NS_RISINGWAVE" "app.kubernetes.io/component=frontend" "RisingWave frontend"
    require_pod_ready "$NS_RISINGWAVE" "app=minio"                          "MinIO"
    require_pod_ready "$NS_MLFLOW"     "app=mlflow"                          "MLflow tracking server"

    ok "Phase 1 passed"
}

# ---------------------------------------------------------------------------
# Phase 2 — finance services up and the data path works
# ---------------------------------------------------------------------------
phase_2() {
    step "Phase 2: finance services + behavioral-features data path"

    require_ns "$NS_FINANCE"
    require_pod_ready "$NS_FINANCE" "app=transactions"          "transactions producer"
    require_pod_ready "$NS_FINANCE" "app=behavioral-features"   "behavioral_features transformer"

    step "Checking behavioral_features materialized view in RisingWave"
    local result
    result="$(rw_count "behavioral_features")"
    if [[ "$result" == "QUERY_FAILED" ]]; then
        fail "RisingWave query failed — is psql installed and RW reachable?" 3
    fi
    if [[ "$result" -gt 0 ]]; then
        ok "behavioral_features has $result row(s)"
    else
        fail "behavioral_features has 0 rows — synthetic stream → MV path not flowing" 3
    fi

    ok "Phase 2 passed"
}

# ---------------------------------------------------------------------------
# Phase 3 — full request path (Day 3+)
# ---------------------------------------------------------------------------
phase_3() {
    step "Phase 3: decisioner /decide end-to-end (TODO Day 3+)"
    ok "Phase 3 placeholder; not yet implemented"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$PHASE" in
    1) phase_1 ;;
    2) phase_1; phase_2 ;;
    3) phase_1; phase_2; phase_3 ;;
    *) fail "Invalid PHASE=$PHASE (expected 1|2|3)" 1 ;;
esac

echo
step "Smoke test complete — PHASE=$PHASE"
