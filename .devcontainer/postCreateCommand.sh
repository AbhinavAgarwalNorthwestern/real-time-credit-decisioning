#!/bin/bash
# IMPORTANT: deliberately NOT using `set -e`. mise install can fail on transient
# network errors (rustup network blip, aqua-registry timeout, etc.). We want
# subsequent setup steps (k9s symlinks, pre-commit, bashrc activation) to run
# even if mise install partially failed — those failures are independently
# recoverable by running `mise install` again from a terminal.
set -uo pipefail

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. mise install with retry — covers transient network failures
# ---------------------------------------------------------------------------
log "Trusting mise.toml"
mise trust || true

mise_install_with_retry() {
    local max_attempts=3
    local attempt=1
    while [ "$attempt" -le "$max_attempts" ]; do
        log "mise install (attempt $attempt/$max_attempts)"
        if mise install; then
            return 0
        fi
        if [ "$attempt" -lt "$max_attempts" ]; then
            log "Retrying in 5s ..."
            sleep 5
        fi
        attempt=$((attempt + 1))
    done
    warn "mise install failed after $max_attempts attempts."
    warn "Run 'mise install' from a fresh terminal to retry."
    return 1
}
mise_install_with_retry || true

# ---------------------------------------------------------------------------
# 2. Activate mise in bash (idempotent — won't duplicate the line)
# ---------------------------------------------------------------------------
ACTIVATION_LINE='eval "$(/usr/local/bin/mise activate bash)"'
if ! grep -qxF "$ACTIVATION_LINE" ~/.bashrc 2>/dev/null; then
    log "Adding mise activation to ~/.bashrc"
    echo "$ACTIVATION_LINE" >> ~/.bashrc
else
    log "mise activation already in ~/.bashrc"
fi

# ---------------------------------------------------------------------------
# 3. Symlink k9s config from the repo into ~/.config/k9s — idempotent
# ---------------------------------------------------------------------------
WORKSPACE_K9S="${PWD}/k9s"
if [ -d "${WORKSPACE_K9S}" ]; then
    log "Symlinking k9s configs into ~/.config/k9s"
    mkdir -p "${HOME}/.config/k9s"
    for f in aliases.yaml hotkeys.yaml views.yaml; do
        if [ -f "${WORKSPACE_K9S}/${f}" ]; then
            ln -sf "${WORKSPACE_K9S}/${f}" "${HOME}/.config/k9s/${f}"
        fi
    done
else
    warn "No k9s/ directory in workspace; skipping k9s symlinks."
fi

# ---------------------------------------------------------------------------
# 4. pre-commit hooks (idempotent)
# ---------------------------------------------------------------------------
if command -v pre-commit >/dev/null 2>&1; then
    log "Installing pre-commit hooks"
    pre-commit install --install-hooks || warn "pre-commit install failed; run manually later."
else
    warn "pre-commit not found on PATH; install will happen after mise install succeeds."
fi

log "postCreateCommand complete."
