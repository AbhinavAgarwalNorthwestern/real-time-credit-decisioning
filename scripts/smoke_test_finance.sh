#!/usr/bin/env bash
# Finance-pipeline smoke test.
#
# Phase 1 (Day 1): infrastructure health checks — Kafka, RisingWave, MLflow,
#                  and the credit-decisioning namespace are reachable and Ready.
# Phase 2 (Day 1 close): synthetic transaction events flow through Kafka
#                        and land as rows in the RisingWave materialized views
#                        (per ADR 009 — no Python behavioral_features service).
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

# How long to wait for data to flow through (producer → Kafka → RW MV).
DATA_FLOW_TIMEOUT="${DATA_FLOW_TIMEOUT:-60}"

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

# Check Kafka topic has messages by summing the latest offsets across partitions.
# Uses the modern kafka-get-offsets.sh wrapper + --bootstrap-server (the
# Kafka 3.x `kafka-run-class.sh kafka.tools.GetOffsetShell --broker-list`
# form was removed in Kafka 4.x; cluster runs 4.1.2 per INFRASTRUCTURE.md).
kafka_topic_has_messages() {
    local topic="$1"
    local offsets
    offsets=$(kubectl -n "$NS_KAFKA" exec kafka-e11b-dual-role-0 -c kafka -- \
        /opt/kafka/bin/kafka-get-offsets.sh \
        --bootstrap-server localhost:9092 \
        --topic "$topic" | awk -F: '{sum += $3} END {print sum}')
    if [[ -z "$offsets" ]] || [[ "$offsets" == "0" ]]; then
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Phase 1 — infrastructure
# ---------------------------------------------------------------------------
phase_1() {
    step "Phase 1: cluster + infra reachability"

    require_ns "$NS_KAFKA"
    require_ns "$NS_RISINGWAVE"
    require_ns "$NS_MLFLOW"

    require_pod_ready "$NS_KAFKA"      "strimzi.io/cluster=kafka-e11b"   "Kafka broker"
    require_pod_ready "$NS_RISINGWAVE" "risingwave/component=frontend"   "RisingWave frontend"
    require_pod_ready "$NS_RISINGWAVE" "app.kubernetes.io/name=minio"   "MinIO"
    require_pod_ready "$NS_MLFLOW"     "app=mlflow"                      "MLflow tracking server"

    ok "Phase 1 passed"
}

# ---------------------------------------------------------------------------
# Phase 2 — transactions producer + data path to RisingWave MVs
# ---------------------------------------------------------------------------
phase_2() {
    step "Phase 2: transactions producer + data path (per ADR 009)"

    require_ns "$NS_FINANCE"

    # Check transactions producer pod is Running
    require_pod_ready "$NS_FINANCE" "app.kubernetes.io/name=transactions" "transactions producer"

    # Wait for events to appear in the Kafka transactions topic
    step "Checking Kafka 'transactions' topic has messages"
    local elapsed=0
    while ! kafka_topic_has_messages "transactions"; do
        elapsed=$((elapsed + 5))
        if [[ $elapsed -ge $DATA_FLOW_TIMEOUT ]]; then
            fail "No messages in 'transactions' topic after ${DATA_FLOW_TIMEOUT}s — producer may not be emitting" 3
        fi
        printf "    Waiting for messages (%ds / %ds)...\n" "$elapsed" "$DATA_FLOW_TIMEOUT"
        sleep 5
    done
    ok "Kafka 'transactions' topic has messages"

    # Check RisingWave source is consuming. The DDL creates two MVs:
    # `behavioral_features_5m` (the tumbling-window aggregate) and
    # `behavioral_features_latest` (a derived snapshot). We check the
    # tumbling MV — it's the source of truth and `_latest` depends on it.
    step "Checking RisingWave behavioral_features_5m MV"
    elapsed=0
    while true; do
        local result
        result="$(rw_count "behavioral_features_5m")"
        if [[ "$result" == "QUERY_FAILED" ]]; then
            if [[ $elapsed -ge $DATA_FLOW_TIMEOUT ]]; then
                fail "RisingWave query failed — is psql installed and RW reachable? Did you apply DDL via deployments/dev/risingwave/apply_ddl.sh?" 3
            fi
        elif [[ "$result" -gt 0 ]]; then
            ok "behavioral_features_5m MV has $result row(s)"
            break
        fi
        elapsed=$((elapsed + 5))
        if [[ $elapsed -ge $DATA_FLOW_TIMEOUT ]]; then
            fail "behavioral_features_5m has 0 rows after ${DATA_FLOW_TIMEOUT}s — MV not computing (windows may not have closed yet; watermark = event_time - 10s, tumble = 5 min)" 3
        fi
        printf "    Waiting for MV rows (%ds / %ds)...\n" "$elapsed" "$DATA_FLOW_TIMEOUT"
        sleep 5
    done

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
