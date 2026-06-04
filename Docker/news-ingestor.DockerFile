# syntax=docker/dockerfile:1

# ==========================================
# STAGE 1: The Builder
# ==========================================
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Enable bytecode compilation and copy mode for speed
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# 1. Install Dependencies (The "Heavy" Lifting)
# Create the directory structure so paths match the lock file
RUN mkdir -p services/news

# Mount the lock and configs to install dependencies
# We use the bind mount here to keep the layer clean
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=services/news/pyproject.toml,target=services/news/pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --package news

# 2. Install Project Code
# Copy the actual code into the builder
COPY services/news /app/services/news
COPY pyproject.toml uv.lock /app/

# Sync the project itself (installs your code into the .venv)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package news

# ==========================================
# STAGE 2: The Runner (Final Image)
# ==========================================
FROM python:3.12-slim-bookworm

WORKDIR /app

# 1. Copy the Virtual Environment from the builder
# This contains all your installed dependencies (FastAPI, Pandas, etc.)
COPY --from=builder /app/.venv /app/.venv

# 2. Copy the Service Code from the builder
# Since uv installs projects in "editable" mode by default,
# the venv points to this specific directory. It MUST exist here.
COPY --from=builder /app/services/news /app/services/news

# 3. Add venv to PATH
# This automatically makes 'python' and 'uv' commands use your venv
ENV PATH="/app/.venv/bin:$PATH"

# 4. Run the app
ENTRYPOINT []
CMD ["python", "/app/services/news/src/news/main.py"]
