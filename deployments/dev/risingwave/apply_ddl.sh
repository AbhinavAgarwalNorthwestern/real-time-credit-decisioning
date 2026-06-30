#!/bin/bash
# Apply every .sql file in this directory to RisingWave, in lexical order.
#
# Files are numbered (00_, 01_, …) so dependency order is explicit.
# Uses the Postgres wire protocol (RisingWave is wire-compatible).
#
# Idempotent: each DDL file uses `CREATE … IF NOT EXISTS`, so re-applying
# is a no-op for already-present objects.
#
# Usage:
#   ./apply_ddl.sh                              # uses defaults below
#   RW_HOST=localhost RW_PORT=4567 ./apply_ddl.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RW_HOST="${RW_HOST:-localhost}"
RW_PORT="${RW_PORT:-4567}"
RW_USER="${RW_USER:-root}"
RW_DATABASE="${RW_DATABASE:-dev}"

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m!!!\033[0m %s\n' "$*" >&2; }

# Confirm psql is available
if ! command -v psql >/dev/null 2>&1; then
    err "psql not found on PATH. Install via mise (already in mise.toml) or apt."
    exit 1
fi

# Confirm RisingWave is reachable before we start applying
if ! pg_isready -h "$RW_HOST" -p "$RW_PORT" -U "$RW_USER" -d "$RW_DATABASE" -t 5 >/dev/null 2>&1; then
    err "RisingWave not reachable at ${RW_HOST}:${RW_PORT}."
    err "  If running from the devcontainer, first port-forward:"
    err "    kubectl -n risingwave port-forward svc/risingwave 4567:4567 &"
    exit 1
fi

log "Applying RisingWave DDL to ${RW_HOST}:${RW_PORT}/${RW_DATABASE}..."

# Apply every .sql file in lexical order
shopt -s nullglob
sql_files=("${SCRIPT_DIR}"/*.sql)
if [ ${#sql_files[@]} -eq 0 ]; then
    err "No .sql files found in ${SCRIPT_DIR}"
    exit 1
fi

failed=0
for sql_file in "${sql_files[@]}"; do
    name="$(basename "$sql_file")"
    log "  Applying ${name}..."
    if ! psql -h "$RW_HOST" -p "$RW_PORT" -U "$RW_USER" -d "$RW_DATABASE" \
              -v ON_ERROR_STOP=1 -f "$sql_file"; then
        err "    FAILED: ${name}"
        failed=$((failed + 1))
    fi
done

if [ "$failed" -gt 0 ]; then
    err "${failed} DDL file(s) failed to apply."
    exit 1
fi

log "DDL apply complete. Verify with:"
log "  psql -h ${RW_HOST} -p ${RW_PORT} -U ${RW_USER} -d ${RW_DATABASE} \\"
log "       -c 'SELECT * FROM behavioral_features_latest LIMIT 5;'"
